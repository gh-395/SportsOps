import os
import re
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Numeric, create_engine, event, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sportsops.database.engine import initialize_database
from sportsops.database.migrations import migrate_schema
from sportsops.database.models import (
    Base,
    Market,
    MarketSelection,
    SimulationEvent,
    SimulationOddsHistory,
    SimulationSelectionQuote,
    TradingRiskIncident,
    TradingAuditLog,
)
from sportsops.database.trading_repository import load_markets
from sportsops.database.service import DatabaseService
from sportsops.models.trading import RiskThresholds


def _sql_statements(script: str) -> list[str]:
    statements: list[str] = []
    current: list[str] = []
    single_quoted = False
    double_quoted = False
    line_comment = False
    dollar_tag: str | None = None
    index = 0

    while index < len(script):
        if line_comment:
            char = script[index]
            current.append(char)
            if char == "\n":
                line_comment = False
            index += 1
            continue
        if dollar_tag is not None:
            if script.startswith(dollar_tag, index):
                current.append(dollar_tag)
                index += len(dollar_tag)
                dollar_tag = None
                continue
            current.append(script[index])
            index += 1
            continue
        if single_quoted:
            current.append(script[index])
            if script[index] == "'":
                if index + 1 < len(script) and script[index + 1] == "'":
                    current.append("'")
                    index += 2
                    continue
                single_quoted = False
            index += 1
            continue
        if double_quoted:
            current.append(script[index])
            if script[index] == '"':
                double_quoted = False
            index += 1
            continue
        if script.startswith("--", index):
            current.extend(("--",))
            line_comment = True
            index += 2
            continue
        if script[index] == "'":
            single_quoted = True
            current.append("'")
            index += 1
            continue
        if script[index] == '"':
            double_quoted = True
            current.append('"')
            index += 1
            continue
        if script[index] == "$":
            match = re.match(r"\$[A-Za-z_0-9]*\$", script[index:])
            if match:
                dollar_tag = match.group(0)
                current.append(dollar_tag)
                index += len(dollar_tag)
                continue
        if script[index] == ";":
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
            index += 1
            continue
        current.append(script[index])
        index += 1

    remainder = "".join(current).strip()
    if remainder:
        statements.append(remainder)
    if single_quoted or double_quoted or dollar_tag is not None:
        raise ValueError("Unterminated quoted section in PostgreSQL DDL.")
    return statements


def _queries() -> list[str]:
    script = (ROOT / "sql" / "analysis_queries.sql").read_text(encoding="utf-8")
    sections = re.split(r"(?m)^-- Query \d+ - .*?$", script)
    queries = [section.strip() for section in sections if section.strip()]
    if len(queries) != 23:
        raise ValueError(f"Expected 23 analysis queries, found {len(queries)}.")
    return queries


def _scoped_engine(database_url: str, schema_name: str):
    engine = create_engine(database_url)

    if engine.dialect.name == "postgresql":
        @event.listens_for(engine, "connect")
        def set_search_path(dbapi_connection, connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute(f'SET search_path TO "{schema_name}"')
            cursor.close()

    return engine


def _get_table_columns(connection, table_name: str, schema_name: str | None = None):
    return inspect(connection).get_columns(table_name, schema=schema_name)


def _verify_legacy_migration(
    database_url: str, schema_name: str, decoy_schema_name: str
) -> None:
    engine = _scoped_engine(database_url, schema_name)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                f'CREATE TABLE "{decoy_schema_name}".trading_risk_incidents '
                "(id INTEGER PRIMARY KEY, decoy_marker TEXT NOT NULL)"
            )
            connection.exec_driver_sql(
                f'INSERT INTO "{decoy_schema_name}".trading_risk_incidents '
                "(id, decoy_marker) VALUES (99, 'must remain untouched')"
            )
            connection.exec_driver_sql(
                "CREATE TABLE matches (id INTEGER PRIMARY KEY, "
                "kickoff_at TIMESTAMP WITH TIME ZONE NOT NULL, "
                "is_simulated BOOLEAN NOT NULL DEFAULT TRUE)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE data_quality_alerts "
                "(id INTEGER PRIMARY KEY, resolved_at TIMESTAMP WITH TIME ZONE)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE simulation_runs (run_id VARCHAR(32) PRIMARY KEY)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE trading_risk_incidents ("
                "id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL, "
                "market_id INTEGER NOT NULL, selection_id INTEGER NOT NULL, "
                "rule_code VARCHAR(40) NOT NULL, "
                "highest_severity VARCHAR(10) NOT NULL, description TEXT NOT NULL, "
                "occurrence_count INTEGER NOT NULL, "
                "first_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, "
                "last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL, "
                "status VARCHAR(16) NOT NULL, "
                "acknowledged_at TIMESTAMP WITH TIME ZONE, "
                "acknowledgement_note TEXT, "
                "cooldown_until TIMESTAMP WITH TIME ZONE)"
            )
            connection.exec_driver_sql(
                "CREATE UNIQUE INDEX uq_trading_risk_incidents_open_key "
                "ON trading_risk_incidents "
                "(match_id, market_id, selection_id, rule_code) "
                "WHERE status = 'OPEN'"
            )
            connection.exec_driver_sql(
                "INSERT INTO matches (id, kickoff_at, is_simulated) "
                "VALUES (101, '2025-08-15 17:00:00+00', TRUE)"
            )
            connection.exec_driver_sql(
                "INSERT INTO trading_risk_incidents "
                "(id, match_id, market_id, selection_id, rule_code, "
                "highest_severity, description, occurrence_count, "
                "first_seen_at, last_seen_at, status) "
                "VALUES (7, 101, 202, 303, 'LEGACY', 'WARNING', 'Retained', 2, "
                "'2025-11-16 23:59:00+00', '2025-11-17 00:01:00+00', 'OPEN')"
            )
            if engine.dialect.name == "postgresql":
                current_schema = connection.scalar(text("SELECT current_schema()"))
                if current_schema != schema_name:
                    raise AssertionError(
                        f"Legacy migration is using schema {current_schema!r}, "
                        f"expected {schema_name!r}."
                    )
                expected_tables = set(
                    inspect(connection).get_table_names(schema=schema_name)
                )
            else:
                expected_tables = set(inspect(connection).get_table_names())
            if not {
                "matches",
                "data_quality_alerts",
                "simulation_runs",
                "trading_risk_incidents",
            } <= expected_tables:
                raise AssertionError(
                    "Legacy tables were not created in the expected migration "
                    f"schema {schema_name!r}: {sorted(expected_tables)}."
                )
        migrate_schema(engine)
        migrate_schema(engine)
        with engine.connect() as connection:
            if engine.dialect.name == "postgresql":
                current_schema = connection.scalar(text("SELECT current_schema()"))
                if current_schema != schema_name:
                    raise AssertionError(
                        f"Legacy migration reflection is using schema "
                        f"{current_schema!r}, expected {schema_name!r}."
                    )
            row = connection.exec_driver_sql(
                "SELECT id, data_origin, source_name FROM matches WHERE id = 101"
            ).one()
            alert_columns = {
                column["name"]
                for column in _get_table_columns(
                    connection,
                    "data_quality_alerts",
                    schema_name if engine.dialect.name == "postgresql" else None,
                )
            }
            risk_schema = (
                schema_name if engine.dialect.name == "postgresql" else None
            )
            risk_columns = {
                column["name"]
                for column in _get_table_columns(
                    connection, "trading_risk_incidents", risk_schema
                )
            }
            incident = connection.exec_driver_sql(
                "SELECT id, run_id, description, occurrence_count, "
                "first_seen_at, last_seen_at "
                "FROM trading_risk_incidents WHERE id = 7"
            ).one()
            risk_indexes = inspect(connection).get_indexes(
                "trading_risk_incidents", schema=risk_schema
            )
            risk_checks = inspect(connection).get_check_constraints(
                "trading_risk_incidents", schema=risk_schema
            )
            decoy_columns = {
                column["name"]
                for column in _get_table_columns(
                    connection, "trading_risk_incidents", decoy_schema_name
                )
            }
            decoy_marker = connection.exec_driver_sql(
                f'SELECT decoy_marker FROM '
                f'"{decoy_schema_name}".trading_risk_incidents WHERE id = 99'
            ).scalar_one()
        if row[0] != 101 or row[1] != "SYNTHETIC":
            raise AssertionError("Legacy PostgreSQL migration changed or lost data.")
        if "acknowledged_at" not in alert_columns:
            raise AssertionError("Legacy alert acknowledgement migration did not run.")
        if "run_id" not in risk_columns:
            raise AssertionError("Legacy risk incident run scope was not added.")
        if incident[:4] != (7, None, "Retained", 2):
            raise AssertionError(
                "Legacy risk incident fields were changed during migration."
            )
        index = next(
            (
                item
                for item in risk_indexes
                if item["name"] == "uq_trading_risk_incidents_open_key"
            ),
            None,
        )
        if index is None or index["column_names"] != [
            "run_id",
            "match_id",
            "market_id",
            "selection_id",
            "rule_code",
        ]:
            raise AssertionError("Legacy open-incident index was not migrated.")
        if not any(
            item["name"] == "ck_trading_risk_incidents_chronology"
            for item in risk_checks
        ):
            raise AssertionError(
                "PostgreSQL risk chronology constraint was not migrated."
            )
        if decoy_columns != {"id", "decoy_marker"} or (
            decoy_marker != "must remain untouched"
        ):
            raise AssertionError(
                "Migration modified the same-named risk table in another schema."
            )
        if engine.dialect.name == "postgresql":
            first_seen = incident[4].astimezone(timezone.utc)
            last_seen = incident[5].astimezone(timezone.utc)
            if first_seen != datetime(2025, 11, 16, 23, 59, tzinfo=timezone.utc):
                raise AssertionError("Legacy first-seen instant changed.")
            if last_seen != datetime(2025, 11, 17, 0, 1, tzinfo=timezone.utc):
                raise AssertionError("Legacy last-seen instant changed.")
    finally:
        engine.dispose()


def main() -> int:
    database_url = os.environ.get("SPORTSOPS_TEST_POSTGRES_URL")
    if not database_url:
        raise SystemExit(
            "Set SPORTSOPS_TEST_POSTGRES_URL to a disposable PostgreSQL database URL."
        )
    control_engine = create_engine(database_url)
    if control_engine.dialect.name != "postgresql":
        control_engine.dispose()
        raise SystemExit("SPORTSOPS_TEST_POSTGRES_URL must use PostgreSQL.")
    if control_engine.url.database != "sportsops_test":
        control_engine.dispose()
        raise SystemExit(
            "The PostgreSQL verifier only runs against the dedicated "
            "'sportsops_test' database."
        )

    suffix = uuid4().hex
    application_schema = f"sportsops_verify_{suffix}"
    migration_schema = f"sportsops_migrate_{suffix}"
    decoy_schema = f"sportsops_decoy_{suffix}"
    application_engine = None
    try:
        with control_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{application_schema}"')
            connection.exec_driver_sql(f'CREATE SCHEMA "{migration_schema}"')
            connection.exec_driver_sql(f'CREATE SCHEMA "{decoy_schema}"')
        application_engine = _scoped_engine(database_url, application_schema)

        schema_script = (ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
        with application_engine.begin() as connection:
            for statement in _sql_statements(schema_script):
                connection.exec_driver_sql(statement)
        print("PostgreSQL schema.sql: PASS (executed in isolated temporary schema)")

        with application_engine.connect() as connection:
            current_schema = connection.scalar(text("SELECT current_schema()"))
            if current_schema != application_schema:
                raise AssertionError(
                    f"Application reflection is using schema {current_schema!r}, "
                    f"expected {application_schema!r}."
                )
            inspector = inspect(connection)
            schema_tables = set(
                inspector.get_table_names(schema=application_schema)
            )
            for table in Base.metadata.sorted_tables:
                if table.name not in schema_tables:
                    raise AssertionError(
                        f"schema.sql is missing ORM table {table.name!r} "
                        f"from schema {application_schema!r}."
                    )
                expected_columns = {column.name for column in table.columns}
                inspected_columns = {
                    column["name"]: column
                    for column in inspector.get_columns(
                        table.name, schema=application_schema
                    )
                }
                actual_columns = set(inspected_columns)
                missing_columns = expected_columns - actual_columns
                if missing_columns:
                    raise AssertionError(
                        f"schema.sql table {table.name!r} is missing columns "
                        f"{sorted(missing_columns)}."
                    )
                for column in table.columns:
                    if isinstance(column.type, Numeric):
                        actual_type = inspected_columns[column.name]["type"]
                        actual_precision = getattr(actual_type, "precision", None)
                        actual_scale = getattr(actual_type, "scale", None)
                        expected_numeric = (
                            column.type.precision,
                            column.type.scale,
                        )
                        actual_numeric = (actual_precision, actual_scale)
                        if actual_numeric != expected_numeric:
                            raise AssertionError(
                                f"PostgreSQL numeric type mismatch for "
                                f"{table.name}.{column.name}: ORM "
                                f"{expected_numeric}, DDL {actual_numeric}."
                            )

        initialize_database(application_engine)
        first_market = None
        with Session(application_engine) as session:
            market_rows = load_markets(session)
            if len(market_rows) != 49:
                raise AssertionError(
                    f"Expected 49 seeded simulated market rows, got {len(market_rows)}."
                )
            first_market = session.scalar(select(Market).order_by(Market.id))
            session.flush()
        print(
            "ORM/DDL table and column coverage, seed, and PostgreSQL joins: PASS"
        )

        database = DatabaseService(application_engine)
        simulation_run = database.start_event_simulation(
            71001, 10, RiskThresholds()
        )
        for _ in range(12):
            current_run = database.load_latest_simulation_run()
            if current_run is None or current_run.status != "RUNNING":
                break
            database.advance_event_simulation(simulation_run.run_id)
        with Session(application_engine) as session:
            event_id = session.scalar(select(SimulationEvent.id).limit(1))
        if event_id is None:
            raise AssertionError("The PostgreSQL event simulation stored no events.")
        with application_engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(
                    text(
                        "UPDATE simulation_events "
                        "SET description = 'must fail' WHERE id = :id"
                    ),
                    {"id": event_id},
                )
            except DBAPIError as error:
                transaction.rollback()
                if "append-only" not in str(error).lower():
                    raise AssertionError(
                        "Simulation ledger update failed for an unexpected reason."
                    ) from error
            else:
                transaction.rollback()
                raise AssertionError(
                    "PostgreSQL simulation event trigger allowed an UPDATE."
                )
        print("PostgreSQL event simulation and append-only ledger trigger: PASS")

        timestamp_first = datetime(
            2025, 11, 16, 23, 59, tzinfo=timezone(timedelta(hours=-5))
        )
        timestamp_last = datetime(
            2025, 11, 17, 0, 1, tzinfo=timezone(timedelta(hours=-5))
        )
        with Session(application_engine) as session:
            selection = session.scalar(
                select(MarketSelection)
                .where(MarketSelection.market_id == first_market.id)
                .order_by(MarketSelection.id)
            )
            incident = TradingRiskIncident(
                run_id=simulation_run.run_id,
                match_id=first_market.match_id,
                market_id=first_market.id,
                selection_id=selection.id,
                rule_code="POSTGRES_TIMESTAMP",
                highest_severity="WARNING",
                description="PostgreSQL timezone round-trip regression probe.",
                occurrence_count=1,
                first_seen_at=timestamp_first,
                last_seen_at=timestamp_last,
            )
            session.add(incident)
            session.flush()
            session.refresh(incident)
            if incident.first_seen_at.astimezone(timezone.utc) != timestamp_first.astimezone(
                timezone.utc
            ):
                raise AssertionError("PostgreSQL changed the first-seen instant.")
            if incident.last_seen_at.astimezone(timezone.utc) != timestamp_last.astimezone(
                timezone.utc
            ):
                raise AssertionError("PostgreSQL changed the last-seen instant.")
            if incident.last_seen_at < incident.first_seen_at:
                raise AssertionError(
                    "PostgreSQL returned risk timestamps out of chronological order."
                )
        print("PostgreSQL risk timestamp timezone round-trip: PASS")

        with Session(application_engine) as session:
            selection = session.scalar(
                select(MarketSelection)
                .where(MarketSelection.market_id == first_market.id)
                .order_by(MarketSelection.id)
            )
            quote = session.get(
                SimulationSelectionQuote,
                (simulation_run.run_id, selection.id),
            )
            if quote is None:
                raise AssertionError("Simulation did not create its initial quote.")
            quote.decimal_odds = Decimal("1000.000")
            quote.fair_probability = Decimal("0.000000982")
            session.add(
                SimulationOddsHistory(
                    run_id=simulation_run.run_id,
                    market_id=first_market.id,
                    selection_id=selection.id,
                    previous_odds=Decimal("1.001"),
                    current_odds=Decimal("1000.000"),
                    fair_probability=Decimal("0.000000982"),
                    percentage_change=Decimal("99800.0999"),
                    cause_type="GOAL",
                    reason="PostgreSQL numeric-boundary regression probe.",
                    idempotency_key=f"{simulation_run.run_id}:numeric-boundary",
                )
            )
            session.flush()
        print(
            "PostgreSQL low-probability quote and maximum odds movement storage: PASS"
        )

        with application_engine.connect() as connection:
            for number, query in enumerate(_queries(), start=1):
                result = connection.exec_driver_sql(query)
                result.fetchall()
            print("PostgreSQL analysis queries: PASS (23 queries)")

        with Session(application_engine) as session:
            selection = session.scalar(
                select(MarketSelection)
                .where(MarketSelection.market_id == first_market.id)
                .order_by(MarketSelection.id)
            )
            selection.current_odds = 1
            try:
                session.flush()
            except IntegrityError:
                session.rollback()
            else:
                raise AssertionError("PostgreSQL accepted decimal odds <= 1.")
        print("PostgreSQL market odds check constraint: PASS")

        with application_engine.connect() as connection:
            transaction = connection.begin()
            audit_id = connection.exec_driver_sql(
                "SELECT MIN(id) FROM trading_audit_log"
            ).scalar_one()
            try:
                connection.execute(
                    text(
                        "UPDATE trading_audit_log "
                        "SET reason = 'must fail' WHERE id = :id"
                    ),
                    {"id": audit_id},
                )
            except DBAPIError:
                transaction.rollback()
            else:
                transaction.rollback()
                raise AssertionError("PostgreSQL audit trigger allowed an UPDATE.")
        print("PostgreSQL append-only audit trigger: PASS")

        _verify_legacy_migration(
            database_url, migration_schema, decoy_schema
        )
        print(
            "PostgreSQL repeatable, schema-isolated, data-preserving legacy "
            "migration: PASS"
        )
    finally:
        if application_engine is not None:
            application_engine.dispose()
        with control_engine.begin() as connection:
            connection.exec_driver_sql(
                f'DROP SCHEMA IF EXISTS "{application_schema}" CASCADE'
            )
            connection.exec_driver_sql(
                f'DROP SCHEMA IF EXISTS "{migration_schema}" CASCADE'
            )
            connection.exec_driver_sql(
                f'DROP SCHEMA IF EXISTS "{decoy_schema}" CASCADE'
            )
        control_engine.dispose()
    print("Temporary verification schemas dropped; no existing schemas modified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
