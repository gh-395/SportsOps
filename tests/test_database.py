import re
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import Connection, func, inspect, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable
from sqlalchemy.orm import Session

from sportsops.data.seed_data import DEMO_MATCHES
from sportsops.database.engine import create_database_engine, initialize_database
from sportsops.database.migrations import migrate_schema
from sportsops.database.models import (
    Base,
    Competition,
    DataQualityAlert,
    MatchEvent,
    MatchRecord,
    MatchStatistics,
    Season,
    Team,
)
from sportsops.database.validation import (
    record_issues,
    validate_database,
    validate_fixture,
)
from sportsops.database.service import DatabaseService
from scripts.verify_postgres import _get_table_columns
from sportsops.ui.detail_table_models import AlertFilterProxyModel, AlertTableModel


ROOT = Path(__file__).resolve().parents[1]


class DatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_database_engine("sqlite://")
        initialize_database(self.engine)
        self.session = Session(self.engine)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()


class DatabaseIntegrityTests(DatabaseTestCase):
    def test_seed_creates_normalized_simulated_records(self) -> None:
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Competition)), 3)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Season)), 3)
        self.assertEqual(self.session.scalar(select(func.count()).select_from(Team)), 14)
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(MatchRecord)), 7
        )
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(MatchStatistics)), 7
        )
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(MatchEvent)), 15
        )
        self.assertEqual(
            self.session.scalar(
                select(func.count()).select_from(MatchRecord).where(
                    MatchRecord.is_simulated.is_(True)
                )
            ),
            7,
        )
        self.assertEqual(validate_database(self.session), ())

    def test_database_initialization_is_idempotent(self) -> None:
        initialize_database(self.engine)
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(MatchRecord)), 7
        )
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(MatchEvent)), 15
        )

    def test_sqlite_foreign_key_enforcement_is_enabled(self) -> None:
        enabled = self.session.connection().exec_driver_sql(
            "PRAGMA foreign_keys"
        ).scalar_one()
        self.assertEqual(enabled, 1)

    def test_score_check_and_event_key_constraint_are_enforced(self) -> None:
        match = self.session.scalar(
            select(MatchRecord).where(MatchRecord.external_id == "SIM-1001")
        )
        with self.assertRaises(IntegrityError):
            self.session.add(
                MatchRecord(
                    external_id="SIM-BAD-SCORE",
                    season_id=match.season_id,
                    home_team_id=match.home_team_id,
                    away_team_id=match.away_team_id,
                    kickoff_at=match.kickoff_at,
                    status="LIVE",
                    home_score=-1,
                    away_score=0,
                    is_simulated=True,
                )
            )
            self.session.flush()
        self.session.rollback()

        existing_event = self.session.scalar(
            select(MatchEvent).where(MatchEvent.match_id == match.id)
        )
        self.session.add(
            MatchEvent(
                match_id=match.id,
                event_key=existing_event.event_key,
                minute=existing_event.minute,
                event_type="GOAL",
                description="duplicate external event key",
                is_simulated=True,
            )
        )
        with self.assertRaises(IntegrityError):
            self.session.flush()
        self.session.rollback()

    def test_validator_detects_duplicate_events_and_persists_alert(self) -> None:
        match = self.session.scalar(
            select(MatchRecord).where(MatchRecord.external_id == "SIM-1001")
        )
        original = self.session.scalar(
            select(MatchEvent).where(
                MatchEvent.match_id == match.id,
                MatchEvent.event_key == "1001-1",
            )
        )
        self.session.add(
            MatchEvent(
                match_id=match.id,
                event_key="1001-duplicate",
                minute=original.minute,
                event_type=original.event_type,
                team_id=original.team_id,
                description=original.description,
                is_simulated=True,
            )
        )
        self.session.flush()

        issues = validate_database(self.session)
        self.assertTrue(any(issue.rule_code == "DUPLICATE_EVENT" for issue in issues))
        record_issues(self.session, issues)
        self.session.flush()
        alerts = self.session.scalars(
            select(DataQualityAlert).where(DataQualityAlert.rule_code == "DUPLICATE_EVENT")
        ).all()
        self.assertEqual(len(alerts), 1)

    def test_alert_filter_and_acknowledgement_are_persisted(self) -> None:
        match = self.session.scalar(
            select(MatchRecord).where(MatchRecord.external_id == "SIM-1001")
        )
        self.session.add(
            DataQualityAlert(
                match_id=match.id,
                rule_code="TEST_ALERT",
                severity="ERROR",
                message="Simulated data-quality test alert",
            )
        )
        self.session.commit()

        service = DatabaseService(self.engine)
        alert = next(
            item
            for item in service.load_alerts()
            if item.description == "Simulated data-quality test alert"
        )
        model = AlertTableModel(service.load_alerts())
        proxy = AlertFilterProxyModel()
        proxy.setSourceModel(model)
        proxy.set_severity("ERROR")
        proxy.set_match_id("SIM-1001")
        self.assertEqual(proxy.rowCount(), 1)
        self.assertEqual(service.acknowledge_alert(alert.alert_id), True)
        self.assertEqual(service.acknowledge_alert(alert.alert_id), False)

        acknowledged = next(
            item
            for item in service.load_alerts()
            if item.alert_id == alert.alert_id
        )
        self.assertIsNotNone(acknowledged.acknowledged_at)
        self.assertIsNone(acknowledged.resolved_at)
        refreshed_match = next(
            item
            for item in service.load_matches()
            if item.match_id == "SIM-1001"
        )
        self.assertEqual(refreshed_match.data_quality, "ERROR")
        with self.assertRaises(LookupError):
            service.acknowledge_alert(-1)


class DataValidationTests(unittest.TestCase):
    def test_detects_invalid_scores_missing_data_and_inconsistent_match(self) -> None:
        fixture = replace(
            DEMO_MATCHES[0],
            competition="",
            home_team="Arsenal",
            away_team="Arsenal",
            home_score=-1,
        )
        rule_codes = {issue.rule_code for issue in validate_fixture(fixture)}
        self.assertIn("MISSING_MATCH_DATA", rule_codes)
        self.assertIn("INCONSISTENT_TEAMS", rule_codes)
        self.assertIn("INVALID_SCORE", rule_codes)

    def test_detects_duplicate_event_and_inconsistent_statistics(self) -> None:
        original = DEMO_MATCHES[0]
        fixture = replace(
            original,
            away_possession=original.home_possession,
            events=original.events + (original.events[0],),
        )
        rule_codes = {issue.rule_code for issue in validate_fixture(fixture)}
        self.assertIn("DUPLICATE_EVENT", rule_codes)
        self.assertIn("INCONSISTENT_POSSESSION", rule_codes)

    def test_detects_missing_events(self) -> None:
        fixture = replace(DEMO_MATCHES[0], events=())
        rule_codes = {issue.rule_code for issue in validate_fixture(fixture)}
        self.assertIn("MISSING_EVENTS", rule_codes)


class AnalysisQueryTests(DatabaseTestCase):
    def test_all_analysis_queries_execute(self) -> None:
        sql_path = ROOT / "sql" / "analysis_queries.sql"
        script = sql_path.read_text(encoding="utf-8")
        sections = re.split(r"(?m)^-- Query \d+ - .*?$", script)
        queries = [
            section.strip()
            for section in sections
            if section.strip()
        ]
        self.assertEqual(len(queries), 23)
        with self.engine.connect() as connection:
            for number, query in enumerate(queries, start=1):
                with self.subTest(query=number):
                    self.assertTrue(query.endswith(";"))
                    connection.exec_driver_sql(query)

    def test_postgresql_schema_documents_all_orm_tables(self) -> None:
        schema_path = ROOT / "sql" / "schema.sql"
        schema = schema_path.read_text(encoding="utf-8")
        compiled_tables = {
            table.name: str(CreateTable(table).compile(dialect=postgresql.dialect()))
            for table in Base.metadata.sorted_tables
        }
        for table_name in (
            "competitions",
            "seasons",
            "teams",
            "matches",
            "match_events",
            "match_statistics",
            "data_quality_alerts",
            "markets",
            "market_selections",
            "odds_history",
            "trading_risk_alerts",
            "trading_audit_log",
            "simulation_runs",
            "simulation_match_states",
            "simulation_events",
            "simulation_market_controls",
            "simulation_market_states",
            "simulation_selection_quotes",
            "simulation_odds_history",
            "simulation_audit_log",
            "trading_risk_incidents",
            "trading_risk_occurrences",
            "historical_import_batches",
            "historical_matches",
        ):
            with self.subTest(table=table_name):
                self.assertRegex(
                    schema, rf"CREATE TABLE(?: IF NOT EXISTS)? {table_name}\s*\("
                )
                self.assertIn(table_name, compiled_tables)
                self.assertIn(f"CREATE TABLE {table_name}", compiled_tables[table_name])
        self.assertIn("TIMESTAMP WITH TIME ZONE", compiled_tables["matches"])
        self.assertIn(
            "event_sequence",
            compiled_tables["simulation_events"],
        )
        self.assertIn("updated_at", schema)
        self.assertIn("BEFORE UPDATE OR DELETE", schema)
        numeric_columns = {
            ("simulation_selection_quotes", "decimal_odds"): (8, 3),
            ("simulation_selection_quotes", "fair_probability"): (10, 9),
            ("simulation_odds_history", "previous_odds"): (8, 3),
            ("simulation_odds_history", "current_odds"): (8, 3),
            ("simulation_odds_history", "fair_probability"): (10, 9),
            ("simulation_odds_history", "percentage_change"): (9, 4),
        }
        for (table_name, column_name), expected in numeric_columns.items():
            with self.subTest(table=table_name, column=column_name):
                orm_column = Base.metadata.tables[table_name].c[column_name]
                self.assertEqual(
                    (orm_column.type.precision, orm_column.type.scale),
                    expected,
                )
                table_ddl = re.search(
                    rf"CREATE TABLE(?: IF NOT EXISTS)? {table_name}\s*\((.*?)\n\);",
                    schema,
                    re.DOTALL,
                )
                self.assertIsNotNone(table_ddl)
                self.assertRegex(
                    table_ddl.group(1),
                    rf"\b{column_name}\s+NUMERIC\({expected[0]},\s*{expected[1]}\)",
                )


class MigrationTests(unittest.TestCase):
    def test_migrations_reflect_through_the_active_connection(self) -> None:
        engine = create_database_engine("sqlite://")
        reflected_bindings = []

        def inspect_binding(bind):
            reflected_bindings.append(bind)
            return inspect(bind)

        try:
            with patch(
                "sportsops.database.migrations.inspect",
                side_effect=inspect_binding,
            ):
                migrate_schema(engine)

            self.assertTrue(reflected_bindings)
            self.assertTrue(
                all(isinstance(bind, Connection) for bind in reflected_bindings)
            )
            self.assertEqual(len({id(bind) for bind in reflected_bindings}), 1)
        finally:
            engine.dispose()

    def test_postgresql_verifier_reflects_table_in_explicit_migration_schema(self) -> None:
        engine = create_database_engine("sqlite://")
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE data_quality_alerts "
                    "(id INTEGER PRIMARY KEY, public_marker TEXT)"
                )
                connection.exec_driver_sql(
                    "ATTACH DATABASE ':memory:' AS migration_schema"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE migration_schema.data_quality_alerts "
                    "(id INTEGER PRIMARY KEY, acknowledged_at DATETIME)"
                )
                columns = {
                    column["name"]
                    for column in _get_table_columns(
                        connection,
                        "data_quality_alerts",
                        "migration_schema",
                    )
                }
            self.assertEqual(columns, {"id", "acknowledged_at"})
        finally:
            engine.dispose()

    def test_existing_sqlite_match_table_gets_last_update_column(self) -> None:
        engine = create_database_engine("sqlite://")
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE matches (id INTEGER PRIMARY KEY, "
                    "kickoff_at DATETIME NOT NULL, "
                    "is_simulated BOOLEAN NOT NULL DEFAULT 1)"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE data_quality_alerts "
                    "(id INTEGER PRIMARY KEY, resolved_at DATETIME)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO matches (id, kickoff_at) VALUES (1, '2026-01-01 12:00:00')"
                )
                connection.exec_driver_sql(
                    "INSERT INTO matches (id, kickoff_at, is_simulated) "
                    "VALUES (2, '2025-01-01 12:00:00', 0)"
                )
            migrate_schema(engine)
            with engine.connect() as connection:
                updated_at = connection.exec_driver_sql(
                    "SELECT updated_at FROM matches WHERE id = 1"
                ).scalar_one()
                alert_columns = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        "PRAGMA table_info(data_quality_alerts)"
                    ).all()
                }
                match_columns = {
                    row[1]
                    for row in connection.exec_driver_sql(
                        "PRAGMA table_info(matches)"
                    ).all()
                }
                migrated_match = connection.exec_driver_sql(
                    "SELECT data_origin, source_name, source_reference "
                    "FROM matches WHERE id = 1"
                ).one()
                migrated_historical = connection.exec_driver_sql(
                    "SELECT data_origin, source_name FROM matches WHERE id = 2"
                ).one()
            self.assertEqual(updated_at, "2026-01-01 12:00:00")
            self.assertIn("acknowledged_at", alert_columns)
            self.assertTrue(
                {"data_origin", "source_name", "source_reference"} <= match_columns
            )
            self.assertEqual(migrated_match[0], "SYNTHETIC")
            self.assertEqual(
                migrated_match[1], "SportsOps synthetic legacy data"
            )
            self.assertEqual(migrated_historical[0], "HISTORICAL")
            self.assertEqual(
                migrated_historical[1], "Unverified legacy source"
            )

            migrate_schema(engine)
            with engine.connect() as connection:
                self.assertEqual(
                    connection.exec_driver_sql(
                        "SELECT COUNT(*) FROM matches"
                    ).scalar_one(),
                    2,
                )
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
