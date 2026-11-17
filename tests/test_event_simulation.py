import unittest
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sportsops.database.engine import create_database_engine, initialize_database
from sportsops.database.migrations import migrate_schema
from sportsops.database.models import (
    Base,
    Market,
    MarketSelection,
    MatchRecord,
    OddsHistory,
    SimulationEvent,
    SimulationMarketState,
    SimulationMatchState,
    SimulationOddsHistory,
    SimulationSelectionQuote,
    TradingRiskAlert,
    TradingRiskIncident,
    TradingRiskOccurrence,
)
from sportsops.database.seed import seed_demo_data, seed_demo_markets
from sportsops.database.service import DatabaseService
from sportsops.database.simulation_repository import initialize_phase6_data
from sportsops.models.trading import RiskThresholds
from sportsops.services.football_simulation import (
    calculate_event_driven_odds,
    calculate_fair_probabilities,
    generate_match_schedule,
    match_goal_rates,
)


class EventSimulationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_database_engine("sqlite://")
        initialize_database(self.engine)
        self.database = DatabaseService(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_seed_reproduces_schedule_and_changes_it_for_another_seed(self) -> None:
        first_home, first_away = match_goal_rates(7001, 100)
        first = generate_match_schedule(
            seed=7001,
            match_id=100,
            match_key="SIM-TEST",
            home_team="Home",
            away_team="Away",
            start_minute=0,
            start_status="SCHEDULED",
            home_goal_rate=first_home,
            away_goal_rate=first_away,
        )
        repeat = generate_match_schedule(
            seed=7001,
            match_id=100,
            match_key="SIM-TEST",
            home_team="Home",
            away_team="Away",
            start_minute=0,
            start_status="SCHEDULED",
            home_goal_rate=first_home,
            away_goal_rate=first_away,
        )
        other_home, other_away = match_goal_rates(7002, 100)
        other = generate_match_schedule(
            seed=7002,
            match_id=100,
            match_key="SIM-TEST",
            home_team="Home",
            away_team="Away",
            start_minute=0,
            start_status="SCHEDULED",
            home_goal_rate=other_home,
            away_goal_rate=other_away,
        )
        self.assertEqual(first, repeat)
        self.assertNotEqual(first, other)
        self.assertEqual(len({event.event_key for event in first}), len(first))
        self.assertEqual(
            [event.minute for event in first],
            sorted(event.minute for event in first),
        )
        self.assertEqual(first[0].event_type, "KICK_OFF")
        self.assertEqual(first[-1].event_type, "FULL_TIME")

    def test_seeded_match_schedule_covers_goal_red_card_and_halftime(self) -> None:
        home_rate, away_rate = match_goal_rates(2, 100)
        events = generate_match_schedule(
            seed=2,
            match_id=100,
            match_key="SIM-TEST",
            home_team="Home",
            away_team="Away",
            start_minute=0,
            start_status="SCHEDULED",
            home_goal_rate=home_rate,
            away_goal_rate=away_rate,
        )
        self.assertIn("GOAL", {event.event_type for event in events})
        self.assertIn("RED_CARD", {event.event_type for event in events})
        self.assertIn("HALF_TIME", {event.event_type for event in events})
        live_events = generate_match_schedule(
            seed=2,
            match_id=100,
            match_key="SIM-LIVE",
            home_team="Home",
            away_team="Away",
            start_minute=11,
            start_status="LIVE",
            home_goal_rate=home_rate,
            away_goal_rate=away_rate,
        )
        event_types = [event.event_type for event in live_events]
        self.assertIn("HALF_TIME", event_types)
        self.assertIn("SECOND_HALF", event_types)
        self.assertLess(
            event_types.index("HALF_TIME"),
            event_types.index("SECOND_HALF"),
        )

    def test_market_probabilities_are_coherent_and_include_margin(self) -> None:
        fair = calculate_fair_probabilities(
            minute=25,
            home_score=1,
            away_score=0,
            home_red_cards=0,
            away_red_cards=0,
            home_goal_rate=Decimal("1.45"),
            away_goal_rate=Decimal("1.10"),
        )
        quoted = calculate_event_driven_odds(fair)
        for market_type in ("1X2", "OVER_UNDER_2_5", "BOTH_TEAMS_TO_SCORE"):
            probability_sum = sum(
                value
                for (market, _), value in fair.items()
                if market == market_type
            )
            implied_sum = sum(
                Decimal("1") / odds
                for (market, _), odds in quoted.items()
                if market == market_type
            )
            self.assertAlmostEqual(float(probability_sum), 1.0, places=8)
            self.assertAlmostEqual(float(implied_sum), 1.05, delta=0.005)
        self.assertTrue(all(odds >= Decimal("1.010") for odds in quoted.values()))

    def test_low_and_zero_probability_quotes_obey_documented_odds_cap(self) -> None:
        quoted = calculate_event_driven_odds(
            {
                ("1X2", "HOME"): Decimal("0.000000982"),
                ("1X2", "DRAW"): Decimal("0"),
                ("1X2", "AWAY"): Decimal("0.999999018"),
                ("1X2", "EXTREME"): Decimal("1e-1000"),
            }
        )
        self.assertEqual(quoted[("1X2", "HOME")], Decimal("1000.000"))
        self.assertEqual(quoted[("1X2", "DRAW")], Decimal("1000.000"))
        self.assertEqual(quoted[("1X2", "EXTREME")], Decimal("1000.000"))
        self.assertLessEqual(
            max(quoted.values()),
            Decimal("1000.000"),
        )

    def test_extreme_late_match_probabilities_produce_storable_odds_movements(self) -> None:
        fair = calculate_fair_probabilities(
            minute=89,
            home_score=8,
            away_score=0,
            home_red_cards=0,
            away_red_cards=1,
            home_goal_rate=Decimal("1.75"),
            away_goal_rate=Decimal("1.35"),
        )
        quoted = calculate_event_driven_odds(fair)
        self.assertEqual(fair[("1X2", "AWAY")], Decimal("0E-9"))
        self.assertEqual(quoted[("1X2", "AWAY")], Decimal("1000.000"))
        self.assertTrue(all(odds <= Decimal("1000.000") for odds in quoted.values()))

        # NUMERIC(8, 3) source prices have a minimum valid value of 1.001.
        maximum_change = (
            (Decimal("1000.000") - Decimal("1.001"))
            / Decimal("1.001")
            * Decimal("100")
        ).quantize(Decimal("0.0001"))
        self.assertEqual(maximum_change, Decimal("99800.0999"))
        self.assertLess(maximum_change, Decimal("99999.9999"))

    def test_full_match_keeps_ordered_events_and_closes_run_markets(self) -> None:
        run = self.database.start_event_simulation(
            20261115, 10, RiskThresholds()
        )
        for _ in range(12):
            current = self.database.load_latest_simulation_run()
            if current is None or current.status != "RUNNING":
                break
            self.database.advance_event_simulation(run.run_id)
        finished = self.database.load_latest_simulation_run()
        self.assertIsNotNone(finished)
        self.assertEqual(finished.status, "COMPLETED")
        self.assertTrue(all(match.status == "FINISHED" for match in finished.matches))

        with Session(self.engine) as session:
            states = session.scalars(
                select(SimulationMatchState).where(
                    SimulationMatchState.run_id == run.run_id
                )
            ).all()
            for state in states:
                _, home_team_id, away_team_id, home_start, away_start = (
                    session.execute(
                        select(
                            MatchRecord.external_id,
                            MatchRecord.home_team_id,
                            MatchRecord.away_team_id,
                            MatchRecord.home_score,
                            MatchRecord.away_score,
                        ).where(MatchRecord.id == state.match_id)
                    ).one()
                )
                events = session.scalars(
                    select(SimulationEvent)
                    .where(
                        SimulationEvent.run_id == run.run_id,
                        SimulationEvent.match_id == state.match_id,
                    )
                    .order_by(SimulationEvent.sequence)
                ).all()
                self.assertEqual(
                    [event.minute for event in events],
                    sorted(event.minute for event in events),
                )
                self.assertEqual(
                    state.home_score,
                    (home_start or 0)
                    + sum(
                        event.event_type == "GOAL"
                        and event.team_id == home_team_id
                        for event in events
                    ),
                )
                self.assertEqual(
                    state.away_score,
                    (away_start or 0)
                    + sum(
                        event.event_type == "GOAL"
                        and event.team_id == away_team_id
                        for event in events
                    ),
                )
                self.assertIn("FULL_TIME", {event.event_type for event in events})
                self.assertEqual(state.minute, 90)
            self.assertTrue(
                all(
                    state.status == "CLOSED"
                    for state in session.scalars(
                        select(SimulationMarketState).where(
                            SimulationMarketState.run_id == run.run_id
                        )
                    )
                )
            )
            history_count = session.scalar(
                select(func.count()).select_from(SimulationOddsHistory)
            )
            self.assertGreater(history_count, 0)
        self.assertEqual(self.database.advance_event_simulation(run.run_id), 0)

    def test_manual_suspension_is_preserved_and_repriced_before_reopening(self) -> None:
        market_row = next(
            row
            for row in self.database.load_markets()
            if row.match_id == "SIM-1003" and row.market_status == "OPEN"
        )
        self.assertTrue(
            self.database.set_simulation_market_status(
                market_row.market_id, "SUSPENDED", "Operator investigation"
            )
        )
        run = self.database.start_event_simulation(
            9001, 5, RiskThresholds()
        )
        self.database.advance_event_simulation(run.run_id)
        suspended_row = next(
            row
            for row in self.database.load_markets()
            if row.market_id == market_row.market_id
        )
        self.assertEqual(suspended_row.market_status, "SUSPENDED")
        with Session(self.engine) as session:
            self.assertIsNone(
                session.get(
                    SimulationSelectionQuote,
                    (run.run_id, market_row.selection_id),
                )
            )
        self.assertTrue(
            self.database.set_simulation_market_status(
                market_row.market_id, "OPEN", "Review complete"
            )
        )
        opened_row = next(
            row
            for row in self.database.load_markets()
            if row.market_id == market_row.market_id
        )
        self.assertEqual(opened_row.market_status, "OPEN")
        with Session(self.engine) as session:
            quote = session.get(
                SimulationSelectionQuote,
                (run.run_id, market_row.selection_id),
            )
            self.assertIsNotNone(quote)
            self.assertIn("before reopening", quote.reason)

    def test_legacy_migration_backfills_groups_without_changing_old_rows(self) -> None:
        legacy_engine = create_database_engine("sqlite://")
        try:
            new_tables = {
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
            }
            legacy_tables = [
                table
                for table in Base.metadata.sorted_tables
                if table.name not in new_tables
            ]
            Base.metadata.create_all(legacy_engine, tables=legacy_tables)
            with Session(legacy_engine) as session, session.begin():
                seed_demo_data(session)
                seed_demo_markets(session)
                market_selection = session.scalar(select(MarketSelection).limit(1))
                history = OddsHistory(
                    selection_id=market_selection.id,
                    previous_odds=Decimal("2.000"),
                    current_odds=Decimal("2.100"),
                    source="SIMULATION",
                    is_simulated=True,
                    idempotency_key="legacy-migration-odds",
                )
                session.add(history)
                session.flush()
                session.add(
                    TradingRiskAlert(
                        odds_history_id=history.id,
                        severity="WARNING",
                        description="Preserved legacy alert",
                        is_simulated=True,
                    )
                )
            with Session(legacy_engine) as session:
                counts_before = {
                    name: session.scalar(
                        select(func.count()).select_from(Base.metadata.tables[name])
                    )
                    for name in (
                        "matches",
                        "markets",
                        "market_selections",
                        "odds_history",
                        "trading_risk_alerts",
                        "trading_audit_log",
                        "historical_matches",
                    )
                }
            Base.metadata.create_all(legacy_engine)
            migrate_schema(legacy_engine)
            with Session(legacy_engine) as session, session.begin():
                initialize_phase6_data(session)
            migrate_schema(legacy_engine)
            with Session(legacy_engine) as session, session.begin():
                initialize_phase6_data(session)
            with Session(legacy_engine) as session:
                counts_after = {
                    name: session.scalar(
                        select(func.count()).select_from(Base.metadata.tables[name])
                    )
                    for name in counts_before
                }
                self.assertEqual(counts_after, counts_before)
                incidents = session.scalars(
                    select(TradingRiskIncident).where(
                        TradingRiskIncident.rule_code == "LEGACY_ODDS_MOVEMENT"
                    )
                ).all()
                occurrences = session.scalars(
                    select(TradingRiskOccurrence).where(
                        TradingRiskOccurrence.legacy_alert_id.is_not(None)
                    )
                ).all()
                self.assertEqual(len(incidents), 1)
                self.assertEqual(incidents[0].occurrence_count, 1)
                self.assertEqual(len(occurrences), 1)
                occurrence = occurrences[0]
                with self.assertRaises(IntegrityError):
                    occurrence.description = "Attempted occurrence rewrite"
                    session.flush()
        finally:
            legacy_engine.dispose()


if __name__ == "__main__":
    unittest.main()
