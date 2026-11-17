
import unittest
from decimal import Decimal

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from sportsops.database.engine import (
    create_database_engine,
    initialize_database,
)
from sportsops.database.service import DatabaseService
from sportsops.database.models import (
    SimulationMarketState,
    SimulationSelectionQuote,
    SimulationOddsHistory,
    TradingRiskIncident,
    SimulationAuditLog,
)
from sportsops.models.trading import RiskThresholds


class RiskEventTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_database_engine("sqlite://")
        initialize_database(self.engine)
        self.database = DatabaseService(self.engine)
        thresholds = RiskThresholds(
            warning_percent=Decimal("10"),
            critical_percent=Decimal("15"),
        )
        self.run = self.database.start_event_simulation(42, 5, thresholds)

        with Session(self.engine) as session:
            self.selection_id = session.scalar(
                select(SimulationSelectionQuote.selection_id)
                .join(
                    SimulationMarketState,
                    (SimulationMarketState.run_id ==
                     SimulationSelectionQuote.run_id)
                    & (SimulationMarketState.market_id ==
                       SimulationSelectionQuote.market_id),
                )
                .where(
                    SimulationSelectionQuote.run_id == self.run.run_id,
                    SimulationMarketState.status == "OPEN",
                )
                .limit(1)
            )

        self.assertIsNotNone(self.selection_id)

    def tearDown(self):
        self.engine.dispose()

    def test_risk_event_creates_incident(self):
        severity = self.database.simulate_risk_event(
            self.run.run_id, self.selection_id
        )
        self.assertEqual(severity, "ERROR")

        incidents = self.database.load_trading_risk_incidents("all")
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0].run_id, self.run.run_id)

    def test_risk_event_records_odds_history(self):
        self.database.simulate_risk_event(
            self.run.run_id, self.selection_id
        )

        with Session(self.engine) as session:
            history = session.scalar(
                select(SimulationOddsHistory)
                .where(
                    SimulationOddsHistory.run_id == self.run.run_id,
                    SimulationOddsHistory.selection_id == self.selection_id,
                    SimulationOddsHistory.cause_type == "RISK_TEST",
                )
            )

            self.assertIsNotNone(history)
            self.assertNotEqual(
                history.previous_odds,
                history.current_odds,
            )
            self.assertGreater(
                abs(history.percentage_change),
                Decimal("15"),
            )

    
    def test_risk_event_persists_incident_and_audit(self):
        self.database.simulate_risk_event(
            self.run.run_id, self.selection_id
        )

        with Session(self.engine) as session:
            incident_count = session.scalar(
                select(func.count(TradingRiskIncident.id))
            )
            audit_count = session.scalar(
                select(func.count(SimulationAuditLog.id)).where(
                    SimulationAuditLog.run_id == self.run.run_id,
                    SimulationAuditLog.event_type == "DEMO_RISK_TEST",
                )
            )

        self.assertEqual(incident_count, 1)
        self.assertEqual(audit_count, 1)


    def test_invalid_risk_event_is_rejected(self):
        with self.assertRaises(ValueError):
            self.database.simulate_risk_event(
                self.run.run_id, -999999
            )

        self.database.pause_event_simulation(self.run.run_id)

        with self.assertRaises(ValueError):
            self.database.simulate_risk_event(
                self.run.run_id, self.selection_id
            )


if __name__ == "__main__":
    unittest.main()
