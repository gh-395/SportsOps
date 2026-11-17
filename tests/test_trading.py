import os
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sportsops.database.engine import create_database_engine, initialize_database
from sportsops.database.models import (
    Base,
    Market,
    MarketSelection,
    MatchRecord,
    OddsHistory,
    SimulationMatchState,
    SimulationOddsHistory,
    SimulationRun,
    TradingAuditLog,
    TradingRiskIncident,
)
from sportsops.database.simulation_repository import (
    _aware,
    _record_risk_occurrence,
    _risk_severity,
    _validate_simulation_quote,
)
from sportsops.database.migrations import migrate_schema
from sportsops.database.service import DatabaseService
from sportsops.database.seed import seed_demo_data, seed_demo_markets
from sportsops.models.simulation import TradingRiskIncidentInfo
from sportsops.models.trading import (
    OddsMovementInfo,
    RiskThresholds,
    TradingAuditInfo,
)
from sportsops.services.market_simulation import MarketSimulationController
from sportsops.ui.main_window import MainWindow
from sportsops.ui.market_models import (
    OddsHistoryTableModel,
    TradingAuditTableModel,
    TradingRiskTableModel,
)
from sportsops.ui.match_table_model import (
    _format_timestamp,
    _format_timestamp_compact,
)


class TradingDatabaseTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_database_engine("sqlite://")
        initialize_database(self.engine)
        self.database = DatabaseService(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()


class TradingDatabaseTests(TradingDatabaseTestCase):
    def test_legacy_incident_migration_adds_run_scope_without_rewriting_rows(self) -> None:
        legacy_engine = create_database_engine("sqlite://")
        try:
            excluded = {"trading_risk_incidents", "trading_risk_occurrences"}
            Base.metadata.create_all(
                legacy_engine,
                tables=[
                    table
                    for table in Base.metadata.sorted_tables
                    if table.name not in excluded
                ],
            )
            with Session(legacy_engine) as session, session.begin():
                seed_demo_data(session)
                seed_demo_markets(session)
                match = session.scalar(select(MatchRecord).limit(1))
                market = session.scalar(
                    select(Market).where(Market.match_id == match.id)
                )
                selection = session.scalar(
                    select(MarketSelection).where(
                        MarketSelection.market_id == market.id
                    )
                )
                match_id, market_id, selection_id = (
                    match.id,
                    market.id,
                    selection.id,
                )
            with legacy_engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE trading_risk_incidents ("
                    "id INTEGER PRIMARY KEY, match_id INTEGER NOT NULL, "
                    "market_id INTEGER NOT NULL, selection_id INTEGER NOT NULL, "
                    "rule_code VARCHAR(40) NOT NULL, "
                    "highest_severity VARCHAR(10) NOT NULL, description TEXT NOT NULL, "
                    "occurrence_count INTEGER NOT NULL, first_seen_at DATETIME NOT NULL, "
                    "last_seen_at DATETIME NOT NULL, status VARCHAR(16) NOT NULL, "
                    "acknowledged_at DATETIME, acknowledgement_note TEXT, "
                    "cooldown_until DATETIME)"
                )
                connection.exec_driver_sql(
                    "CREATE UNIQUE INDEX uq_trading_risk_incidents_open_key "
                    "ON trading_risk_incidents "
                    "(match_id, market_id, selection_id, rule_code) "
                    "WHERE status = 'OPEN'"
                )
                connection.exec_driver_sql(
                    "INSERT INTO trading_risk_incidents "
                    "(id, match_id, market_id, selection_id, rule_code, "
                    "highest_severity, description, occurrence_count, "
                    "first_seen_at, last_seen_at, status) "
                    "VALUES (1, ?, ?, ?, 'LEGACY', 'WARNING', 'Retained', 2, "
                    "'2025-11-16 23:59:00', '2025-11-17 00:01:00', 'OPEN')",
                    (match_id, market_id, selection_id),
                )

            migrate_schema(legacy_engine)
            migrate_schema(legacy_engine)
            with legacy_engine.connect() as connection:
                row = connection.exec_driver_sql(
                    "SELECT id, run_id, description, occurrence_count, "
                    "first_seen_at, last_seen_at FROM trading_risk_incidents"
                ).one()
            self.assertEqual(
                row,
                (
                    1,
                    None,
                    "Retained",
                    2,
                    "2025-11-16 23:59:00",
                    "2025-11-17 00:01:00",
                ),
            )
            index = next(
                item
                for item in inspect(legacy_engine).get_indexes(
                    "trading_risk_incidents"
                )
                if item["name"] == "uq_trading_risk_incidents_open_key"
            )
            self.assertEqual(
                index["column_names"],
                ["run_id", "match_id", "market_id", "selection_id", "rule_code"],
            )
        finally:
            legacy_engine.dispose()

    def test_inconsistent_legacy_timestamps_stop_migration_without_changes(self) -> None:
        legacy_engine = create_database_engine("sqlite://")
        try:
            with legacy_engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE simulation_runs (run_id VARCHAR(32) PRIMARY KEY)"
                )
                connection.exec_driver_sql(
                    "CREATE TABLE trading_risk_incidents ("
                    "id INTEGER PRIMARY KEY, first_seen_at DATETIME NOT NULL, "
                    "last_seen_at DATETIME NOT NULL)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO trading_risk_incidents "
                    "(id, first_seen_at, last_seen_at) "
                    "VALUES (7, '2025-11-17 10:00:00', '2025-11-17 09:00:00')"
                )
            with self.assertRaisesRegex(RuntimeError, "No timestamps were changed"):
                migrate_schema(legacy_engine)
            columns = {
                column["name"]
                for column in inspect(legacy_engine).get_columns(
                    "trading_risk_incidents"
                )
            }
            self.assertNotIn("run_id", columns)
            with legacy_engine.connect() as connection:
                row = connection.exec_driver_sql(
                    "SELECT first_seen_at, last_seen_at "
                    "FROM trading_risk_incidents WHERE id = 7"
                ).one()
            self.assertEqual(
                row,
                ("2025-11-17 10:00:00", "2025-11-17 09:00:00"),
            )
        finally:
            legacy_engine.dispose()

    def test_phase_three_database_upgrade_preserves_existing_match_rows(self) -> None:
        engine = create_database_engine("sqlite://")
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "CREATE TABLE matches (id INTEGER PRIMARY KEY, "
                    "external_id VARCHAR(40) NOT NULL UNIQUE, "
                    "kickoff_at DATETIME NOT NULL, "
                    "is_simulated BOOLEAN NOT NULL DEFAULT 1)"
                )
                connection.exec_driver_sql(
                    "INSERT INTO matches "
                    "(id, external_id, kickoff_at, is_simulated) "
                    "VALUES (9, 'LEGACY-9', '2025-08-15 17:00:00', 1)"
                )
            migrate_schema(engine)
            migrate_schema(engine)
            with engine.connect() as connection:
                row = connection.exec_driver_sql(
                    "SELECT id, external_id, data_origin, source_name "
                    "FROM matches"
                ).one()
            self.assertEqual(
                row,
                (9, "LEGACY-9", "SYNTHETIC", "SportsOps synthetic legacy data"),
            )
        finally:
            engine.dispose()

    def test_risk_thresholds_must_be_positive_and_ordered(self) -> None:
        with self.assertRaises(ValueError):
            RiskThresholds(
                warning_percent=Decimal("0"),
                critical_percent=Decimal("15"),
            )
        with self.assertRaises(ValueError):
            RiskThresholds(
                warning_percent=Decimal("20"),
                critical_percent=Decimal("15"),
            )

    def test_invalid_simulation_quotes_fail_before_persistence(self) -> None:
        with self.assertRaisesRegex(ValueError, "odds limits"):
            _validate_simulation_quote(
                Decimal("1000.001"), Decimal("0.001"), 1
            )
        with self.assertRaisesRegex(ValueError, "fair probability"):
            _validate_simulation_quote(
                Decimal("2.000"), Decimal("1.001"), 1
            )

    def test_seed_creates_linked_simulated_markets_idempotently(self) -> None:
        rows = self.database.load_markets()
        self.assertEqual(len(rows), 49)
        self.assertTrue(all(row.simulated for row in rows))
        self.assertEqual(len({row.market_id for row in rows}), 21)
        self.assertTrue(all(row.decimal_odds > Decimal("1") for row in rows))
        first = rows[0]
        self.assertEqual(
            first.implied_probability,
            (Decimal("100") / first.decimal_odds).quantize(Decimal("0.01")),
        )
        initialize_database(self.engine)
        self.assertEqual(len(self.database.load_markets()), 49)
        self.assertEqual(len(self.database.load_trading_audit_log()), 21)

    def test_event_simulation_groups_repeated_risk_occurrences(self) -> None:
        thresholds = RiskThresholds(
            warning_percent=Decimal("1"),
            critical_percent=Decimal("2"),
        )
        run = self.database.start_event_simulation(42, 5, thresholds)
        first_changes = self.database.advance_event_simulation(run.run_id)
        self.assertGreater(first_changes, 0)
        self.assertEqual(self.database.load_trading_risk_incidents(), ())
        self.assertIsNone(
            _risk_severity(
                Decimal("1000"),
                thresholds.warning_percent,
                thresholds.critical_percent,
                "GOAL",
            )
        )
        selected = self.database.load_markets()[0]
        incident_times = (
            datetime(2025, 11, 16, 23, 59, 59, tzinfo=timezone.utc),
            datetime(2025, 11, 17, 0, 0, 1, tzinfo=timezone.utc),
        )
        with Session(self.engine) as session, session.begin():
            simulation_run = session.get(SimulationRun, run.run_id)
            match = session.scalar(
                select(MatchRecord).where(
                    MatchRecord.external_id == selected.match_id
                )
            )
            market = session.get(Market, selected.market_id)
            selection = session.get(MarketSelection, selected.selection_id)
            for index, occurred_at in enumerate(incident_times):
                history = SimulationOddsHistory(
                    run_id=run.run_id,
                    market_id=market.id,
                    selection_id=selection.id,
                    previous_odds=Decimal("2.000"),
                    current_odds=Decimal("2.500"),
                    fair_probability=Decimal("0.400000000"),
                    percentage_change=Decimal("25.0000"),
                    cause_type="UNEXPECTED_SOURCE",
                    reason="Unexpected price change used by regression test.",
                    changed_at=occurred_at,
                    idempotency_key=f"{run.run_id}:unexpected:{index}",
                )
                session.add(history)
                session.flush()
                severity = _risk_severity(
                    Decimal("25"),
                    simulation_run.warning_percent,
                    simulation_run.critical_percent,
                    history.cause_type,
                )
                self.assertEqual(severity, "ERROR")
                _record_risk_occurrence(
                    session,
                    simulation_run,
                    match.id,
                    market,
                    selection,
                    history,
                    severity,
                    Decimal("25"),
                )

        incidents = self.database.load_trading_risk_incidents("all")
        self.assertEqual(len(incidents), 1)
        incident = incidents[0]
        self.assertEqual(incident.run_id, run.run_id)
        self.assertEqual(incident.occurrence_count, 2)
        self.assertEqual(
            incident.first_seen_at.replace(tzinfo=timezone.utc),
            incident_times[0],
        )
        self.assertEqual(
            incident.last_seen_at.replace(tzinfo=timezone.utc),
            incident_times[1],
        )
        risk_table = TradingRiskTableModel(incidents)
        first_seen_text = risk_table.data(risk_table.index(0, 7))
        last_seen_text = risk_table.data(risk_table.index(0, 8))
        self.assertEqual(first_seen_text, _format_timestamp(incident_times[0]))
        self.assertEqual(last_seen_text, _format_timestamp(incident_times[1]))
        self.assertIn(
            incident_times[0].astimezone().strftime("%Y-%m-%d"),
            first_seen_text,
        )
        self.assertIn(
            incident_times[1].astimezone().strftime("%Y-%m-%d"),
            last_seen_text,
        )
        self.assertTrue(
            all(
                movement.reason
                for row in self.database.load_markets()
                for movement in self.database.load_odds_history(row.selection_id)
                if movement.source.startswith("EVENT:")
            )
        )
        self.assertTrue(
            self.database.acknowledge_trading_risk_incident(
                incident.incident_id, "Reviewed grouped simulated movement"
            )
        )
        self.assertFalse(
            self.database.acknowledge_trading_risk_incident(
                incident.incident_id, "Changed note"
            )
        )
        acknowledged = next(
            item
            for item in self.database.load_trading_risk_incidents()
            if item.incident_id == incident.incident_id
        )
        self.assertEqual(acknowledged.status, "ACKNOWLEDGED")
        self.assertEqual(
            acknowledged.acknowledgement_note,
            "Reviewed grouped simulated movement",
        )
        self.assertTrue(
            any(
                entry.event_type == "RISK_INCIDENT_ACKNOWLEDGED"
                for entry in self.database.load_trading_audit_log()
            )
        )

    def test_incidents_are_scoped_per_run_and_reset_keeps_history(self) -> None:
        first_run = self.database.start_event_simulation(101, 5, RiskThresholds())
        selected = self.database.load_markets()[0]
        with Session(self.engine) as session, session.begin():
            match = session.scalar(
                select(MatchRecord).where(
                    MatchRecord.external_id == selected.match_id
                )
            )
            timestamp = datetime(2025, 11, 16, 12, 0, tzinfo=timezone.utc)
            session.add(
                TradingRiskIncident(
                    run_id=first_run.run_id,
                    match_id=match.id,
                    market_id=selected.market_id,
                    selection_id=selected.selection_id,
                    rule_code="TEST_RUN_SCOPE",
                    highest_severity="WARNING",
                    description="Run scoping regression test.",
                    occurrence_count=1,
                    first_seen_at=timestamp,
                    last_seen_at=timestamp,
                )
            )
        self.assertTrue(self.database.reset_event_simulation(first_run.run_id))
        second_run = self.database.start_event_simulation(202, 5, RiskThresholds())
        with Session(self.engine) as session, session.begin():
            match = session.scalar(
                select(MatchRecord).where(
                    MatchRecord.external_id == selected.match_id
                )
            )
            timestamp = datetime(2025, 11, 17, 12, 0, tzinfo=timezone.utc)
            session.add(
                TradingRiskIncident(
                    run_id=second_run.run_id,
                    match_id=match.id,
                    market_id=selected.market_id,
                    selection_id=selected.selection_id,
                    rule_code="TEST_RUN_SCOPE",
                    highest_severity="WARNING",
                    description="Run scoping regression test.",
                    occurrence_count=1,
                    first_seen_at=timestamp,
                    last_seen_at=timestamp,
                )
            )
        self.assertEqual(
            [incident.run_id for incident in self.database.load_trading_risk_incidents()],
            [second_run.run_id],
        )
        self.assertEqual(
            len(self.database.load_trading_risk_incidents("historical")),
            1,
        )
        all_incidents = self.database.load_trading_risk_incidents("all")
        self.assertEqual(
            {incident.run_id for incident in all_incidents},
            {first_run.run_id, second_run.run_id},
        )
        self.assertEqual(len(all_incidents), 2)
        risk_table = TradingRiskTableModel(all_incidents)
        self.assertEqual(risk_table.alert_at(0).run_id, second_run.run_id)
        self.assertEqual(risk_table.alert_at(1).run_id, first_run.run_id)

    def test_market_suspension_requires_reason_and_audits_transitions(self) -> None:
        open_market = next(
            row for row in self.database.load_markets() if row.market_status == "OPEN"
        )
        with self.assertRaises(ValueError):
            self.database.suspend_market(open_market.market_id, "  ")
        self.assertTrue(
            self.database.suspend_market(open_market.market_id, "Trading review")
        )
        suspended = next(
            row
            for row in self.database.load_markets()
            if row.market_id == open_market.market_id
        )
        self.assertEqual(suspended.market_status, "SUSPENDED")
        self.assertTrue(self.database.reopen_market(open_market.market_id))
        audit_types = {
            entry.event_type for entry in self.database.load_trading_audit_log()
        }
        self.assertIn("MARKET_SUSPENDED", audit_types)
        self.assertIn("MARKET_REOPENED", audit_types)

        closed_market = next(
            row for row in self.database.load_markets() if row.market_status == "CLOSED"
        )
        with self.assertRaises(ValueError):
            self.database.reopen_market(closed_market.market_id)

    def test_database_constraints_and_audit_append_only_trigger(self) -> None:
        with Session(self.engine) as session:
            row = self.database.load_markets()[0]
            with self.assertRaises(IntegrityError):
                session.add(
                    Market(
                        match_id=session.scalar(
                            select(Market.match_id).where(Market.id == row.market_id)
                        ),
                        market_type="1X2",
                        status="OPEN",
                        is_simulated=True,
                    )
                )
                session.flush()
            session.rollback()

            selection = session.get(MarketSelection, row.selection_id)
            selection.current_odds = Decimal("1.000")
            with self.assertRaises(IntegrityError):
                session.flush()
            session.rollback()

            audit = session.scalar(select(TradingAuditLog).limit(1))
            audit.reason = "Attempted modification"
            with self.assertRaises(IntegrityError):
                session.flush()
            session.rollback()
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(TradingAuditLog)
                ),
                21,
            )

    def test_simulation_reset_preserves_ledger_and_base_market_data(self) -> None:
        controller = MarketSimulationController(self.database)
        initial_rows = self.database.load_markets()
        with Session(self.engine) as session:
            initial_market_prices = dict(
                session.execute(
                    select(MarketSelection.id, MarketSelection.current_odds)
                ).all()
            )
            initial_matches = session.scalar(
                select(func.count()).select_from(MatchRecord)
            )
        controller.start(seed=20261115, speed=5)
        self.assertGreater(controller.advance(), 0)
        controller.pause()
        self.assertEqual(controller.advance(), 0)
        self.assertTrue(controller.reset())

        restored_rows = self.database.load_markets()
        self.assertEqual(
            {row.selection_id: row.decimal_odds for row in restored_rows},
            {row.selection_id: row.decimal_odds for row in initial_rows},
        )
        with Session(self.engine) as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(OddsHistory)), 0
            )
            self.assertGreater(
                session.scalar(
                    select(func.count()).select_from(SimulationOddsHistory)
                ),
                0,
            )
            self.assertEqual(
                dict(
                    session.execute(
                        select(MarketSelection.id, MarketSelection.current_odds)
                    ).all()
                ),
                initial_market_prices,
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(MatchRecord)),
                initial_matches,
            )


class MarketWorkspaceSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.engine = create_database_engine("sqlite://")
        initialize_database(self.engine)
        self.window = MainWindow(DatabaseService(self.engine), "SQLite test")

    def tearDown(self) -> None:
        self.window.close()
        self.engine.dispose()

    def test_market_navigation_filtering_sorting_and_selection(self) -> None:
        workspace = self.window.market_workspace
        self.assertEqual(workspace.market_model.rowCount(), 49)
        self.window.navigation.setCurrentRow(1)
        self.assertIs(
            self.window.workspace_stack.currentWidget(),
            self.window.historical_workspace,
        )
        self.window.navigation.setCurrentRow(0)
        self.assertIs(self.window.workspace_stack.currentWidget(), workspace)
        self.assertEqual(self.window.navigation.count(), 2)

        workspace.market_type_filter.setCurrentText("1X2")
        self.assertEqual(workspace.market_proxy_model.rowCount(), 21)
        workspace.market_status_filter.setCurrentText("CLOSED")
        self.assertEqual(workspace.market_proxy_model.rowCount(), 3)
        workspace.market_status_filter.setCurrentText("All statuses")
        workspace.market_table.sortByColumn(6, Qt.SortOrder.AscendingOrder)
        self.assertGreater(
            float(workspace.market_proxy_model.data(
                workspace.market_proxy_model.index(0, 6)
            )),
            1.0,
        )
        workspace.market_table.selectRow(0)
        self.assertIn("SIM-", workspace.market_metadata.text())

    def test_timestamp_formatting_and_odds_movement_hierarchy(self) -> None:
        utc_timestamp = datetime(2025, 11, 17, 0, 15, tzinfo=timezone.utc)
        local_text = utc_timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        self.assertEqual(_format_timestamp(utc_timestamp), local_text)
        self.assertEqual(
            _format_timestamp(datetime(2025, 11, 17, 0, 15)),
            local_text,
        )
        offset_timestamp = datetime(
            2025, 11, 17, 5, 15, tzinfo=timezone(timedelta(hours=5))
        )
        self.assertEqual(
            _format_timestamp(offset_timestamp),
            offset_timestamp.astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        )
        self.assertEqual(
            _aware(offset_timestamp),
            offset_timestamp.astimezone(timezone.utc),
        )

        movements = tuple(
            OddsMovementInfo(
                history_id=index,
                selection_id=index,
                match_id="SIM-1001",
                market_type="1X2",
                selection_name="Home",
                previous_odds=Decimal("2.000"),
                current_odds=Decimal(str(2 + change / 100)),
                absolute_change=Decimal("0.100"),
                percentage_change=Decimal(str(change)),
                changed_at=utc_timestamp,
                source="SIMULATION",
                simulated=True,
                reason="Test odds movement",
            )
            for index, change in enumerate((20, -20, 5), start=1)
        )
        model = OddsHistoryTableModel(movements)
        positive = model.data(
            model.index(0, 4), Qt.ItemDataRole.BackgroundRole
        )
        negative = model.data(
            model.index(1, 4), Qt.ItemDataRole.BackgroundRole
        )
        routine = model.data(
            model.index(2, 4), Qt.ItemDataRole.BackgroundRole
        )
        self.assertEqual(positive.name(), "#263f48")
        self.assertEqual(negative.name(), "#4a402f")
        self.assertIsNone(routine)

        incident = TradingRiskIncidentInfo(
            incident_id=1,
            match_id="SIM-1001",
            market_type="1X2",
            selection_name="Home",
            rule_code="TEST",
            severity="WARNING",
            description="Cross-midnight test",
            occurrence_count=2,
            first_seen_at=datetime(2025, 11, 16, 23, 59, tzinfo=timezone.utc),
            last_seen_at=datetime(2025, 11, 17, 0, 1, tzinfo=timezone.utc),
            status="OPEN",
            acknowledged_at=None,
            acknowledgement_note=None,
            run_id="ab1234567890",
            run_status="RUNNING",
        )
        risk_model = TradingRiskTableModel((incident,))
        self.assertEqual(
            risk_model.data(risk_model.index(0, 7)),
            _format_timestamp(incident.first_seen_at),
        )
        self.assertEqual(
            risk_model.data(risk_model.index(0, 8)),
            _format_timestamp(incident.last_seen_at),
        )

    def test_simulation_control_updates_history_and_preserves_audit(self) -> None:
        workspace = self.window.market_workspace
        before_audit = len(self.window.market_workspace.database.load_trading_audit_log())
        workspace.start_simulation()
        self.assertGreater(workspace.simulation.advance(), 0)
        workspace.refresh()
        workspace.pause_simulation()
        self.assertGreater(workspace.history_model.rowCount(), 0)
        self.assertGreaterEqual(
            len(self.window.market_workspace.database.load_trading_audit_log()),
            before_audit + 42,
        )
        audit_types = {
            entry.event_type
            for entry in self.window.market_workspace.database.load_trading_audit_log()
        }
        self.assertIn("SIMULATION_STARTED", audit_types)
        self.assertIn("SIMULATION_PAUSED", audit_types)

    def test_market_actions_are_audited_in_workspace(self) -> None:
        workspace = self.window.market_workspace
        self.assertEqual(workspace.audit_model.rowCount(), 21)
        audit_market = workspace.audit_model.data(
            workspace.audit_model.index(0, 2)
        )
        self.assertIn(audit_market, ("1X2", "Over/Under 2.5", "Both Teams To Score"))
        self.assertTrue(
            all(
                workspace.audit_model.data(workspace.audit_model.index(0, column))
                for column in range(len(workspace.audit_model.HEADERS))
            )
        )

    def test_risk_filters_include_legacy_incidents_and_summary_matches_rows(self) -> None:
        workspace = self.window.market_workspace
        run = workspace.database.start_event_simulation(9191, 5, RiskThresholds())
        market_rows = workspace.database.load_markets()
        incident_times = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
        incident_specs = (
            (market_rows[0], None, "LEGACY_OPEN", "WARNING", "OPEN", 3),
            (
                market_rows[1],
                None,
                "LEGACY_ACKNOWLEDGED",
                "WARNING",
                "ACKNOWLEDGED",
                2,
            ),
            (market_rows[2], run.run_id, "CURRENT_RUN", "ERROR", "OPEN", 4),
        )
        with Session(self.engine) as session, session.begin():
            for (
                market_row,
                run_id,
                rule_code,
                severity,
                status,
                occurrence_count,
            ) in incident_specs:
                match = session.scalar(
                    select(MatchRecord).where(
                        MatchRecord.external_id == market_row.match_id
                    )
                )
                session.add(
                    TradingRiskIncident(
                        run_id=run_id,
                        match_id=match.id,
                        market_id=market_row.market_id,
                        selection_id=market_row.selection_id,
                        rule_code=rule_code,
                        highest_severity=severity,
                        description=f"Test incident {rule_code}",
                        occurrence_count=occurrence_count,
                        first_seen_at=incident_times,
                        last_seen_at=incident_times,
                        status=status,
                        acknowledged_at=(
                            incident_times if status == "ACKNOWLEDGED" else None
                        ),
                    )
                )

        workspace.risk_run_filter.setCurrentIndex(1)
        self.assertEqual(workspace.risk_model.rowCount(), 3)
        self.assertEqual(workspace.risk_proxy_model.rowCount(), 3)
        self.assertEqual(
            workspace.risk_summary.text(),
            "3 incidents | 2 open | 9 occurrences | All runs",
        )
        self.assertTrue(workspace.risk_empty_label.isHidden())
        self.assertTrue(
            any(incident.run_id is None for incident in workspace.risk_model.alerts)
        )

        workspace.risk_severity_filter.setCurrentText("ERROR")
        self.assertEqual(workspace.risk_proxy_model.rowCount(), 1)
        self.assertEqual(
            workspace.risk_summary.text(),
            "1 incident | 1 open | 4 occurrences | All runs",
        )

        workspace.risk_severity_filter.setCurrentText("All severities")
        workspace.risk_status_filter.setCurrentText("ACKNOWLEDGED")
        self.assertEqual(workspace.risk_proxy_model.rowCount(), 1)
        self.assertEqual(
            workspace.risk_summary.text(),
            "1 incident | 0 open | 2 occurrences | All runs",
        )

        workspace.risk_status_filter.setCurrentText("RESOLVED")
        self.assertEqual(workspace.risk_proxy_model.rowCount(), 0)
        self.assertEqual(
            workspace.risk_summary.text(),
            "0 incidents | 0 open | 0 occurrences | All runs",
        )
        self.assertIn("No incidents match", workspace.risk_empty_label.text())

        workspace.risk_status_filter.setCurrentText("All incidents")
        workspace.risk_run_filter.setCurrentIndex(0)
        self.assertEqual(workspace.risk_proxy_model.rowCount(), 1)
        self.assertEqual(
            workspace.risk_summary.text(),
            "1 incident | 1 open | 4 occurrences | Current / last run",
        )
        workspace.risk_run_filter.setCurrentIndex(2)
        self.assertEqual(workspace.risk_proxy_model.rowCount(), 2)
        self.assertEqual(
            workspace.risk_summary.text(),
            "2 incidents | 1 open | 5 occurrences | Historical only",
        )

    def test_audit_and_odds_timestamps_are_compact_tooltipped_and_sortable(self) -> None:
        workspace = self.window.market_workspace
        earlier = datetime(2026, 11, 16, 23, 45, tzinfo=timezone.utc)
        later = datetime(2026, 11, 17, 0, 15, tzinfo=timezone.utc)
        workspace.history_model.replace_movements(
            (
                OddsMovementInfo(
                    history_id=2,
                    selection_id=2,
                    match_id="SIM-1001",
                    market_type="1X2",
                    selection_name="Home",
                    previous_odds=Decimal("2.000"),
                    current_odds=Decimal("2.100"),
                    absolute_change=Decimal("0.100"),
                    percentage_change=Decimal("5.0000"),
                    changed_at=later,
                    source="SIMULATION",
                    simulated=True,
                    reason="Later movement",
                ),
                OddsMovementInfo(
                    history_id=1,
                    selection_id=1,
                    match_id="SIM-1001",
                    market_type="1X2",
                    selection_name="Home",
                    previous_odds=Decimal("2.100"),
                    current_odds=Decimal("2.200"),
                    absolute_change=Decimal("0.100"),
                    percentage_change=Decimal("4.7619"),
                    changed_at=earlier,
                    source="SIMULATION",
                    simulated=True,
                    reason="Earlier movement",
                ),
            )
        )
        workspace.history_table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        history_view = workspace.history_table.model()
        history_index = history_view.index(0, 0)
        self.assertEqual(
            history_view.data(history_index),
            _format_timestamp_compact(earlier),
        )
        self.assertEqual(
            history_view.data(history_index, Qt.ItemDataRole.ToolTipRole),
            _format_timestamp(earlier),
        )
        self.assertLess(
            history_view.data(history_index, OddsHistoryTableModel.SORT_ROLE),
            history_view.data(
                history_view.index(1, 0), OddsHistoryTableModel.SORT_ROLE
            ),
        )

        workspace.audit_model.replace_entries(
            (
                TradingAuditInfo(
                    audit_id=2,
                    match_id="SIM-1001",
                    market_type="1X2",
                    selection_name="Home",
                    event_type="LATER",
                    details="Later audit",
                    actor="Operator",
                    created_at=later,
                    simulated=True,
                ),
                TradingAuditInfo(
                    audit_id=1,
                    match_id="SIM-1001",
                    market_type="1X2",
                    selection_name="Home",
                    event_type="EARLIER",
                    details="Earlier audit",
                    actor="Operator",
                    created_at=earlier,
                    simulated=True,
                ),
            )
        )
        workspace.audit_table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        audit_view = workspace.audit_table.model()
        audit_index = audit_view.index(0, 0)
        self.assertEqual(
            audit_view.data(audit_index),
            _format_timestamp_compact(earlier),
        )
        self.assertEqual(
            audit_view.data(audit_index, Qt.ItemDataRole.ToolTipRole),
            _format_timestamp(earlier),
        )
        self.assertLess(
            audit_view.data(audit_index, TradingAuditTableModel.SORT_ROLE),
            audit_view.data(
                audit_view.index(1, 0), TradingAuditTableModel.SORT_ROLE
            ),
        )
        selected = workspace._selected_market_row
        self.assertIsNotNone(selected)
        self.assertFalse(workspace.suspend_selected(" "))
        self.assertTrue(workspace.suspend_selected("Operator review"))
        self.assertEqual(workspace._selected_market_row.market_status, "SUSPENDED")
        self.assertTrue(workspace.reopen_selected("Review complete"))
        self.assertEqual(workspace._selected_market_row.market_status, "OPEN")
        audit = self.window.market_workspace.database.load_trading_audit_log()
        self.assertIn("MARKET_MANUALLY_SUSPENDED", {entry.event_type for entry in audit})
        self.assertIn("MARKET_MANUALLY_REOPENED", {entry.event_type for entry in audit})


if __name__ == "__main__":
    unittest.main()
