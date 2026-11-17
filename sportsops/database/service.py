from datetime import date
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sportsops.database.repository import (
    acknowledge_alert,
    load_alerts,
    load_match_details,
    load_matches,
)
from sportsops.database.historical_repository import (
    import_historical_csv,
    load_historical_facet_options,
    load_historical_filter_options,
    load_historical_import_batches,
    load_historical_match_page,
    load_historical_matches,
    load_historical_team_summary,
)
from sportsops.database.trading_repository import (
    acknowledge_trading_risk_alert,
    load_markets,
    load_odds_history,
    load_trading_audit_log,
    load_trading_risk_alerts,
    record_trading_operator_action,
    transition_market,
)
from sportsops.database.simulation_repository import (
    acknowledge_trading_risk_incident,
    advance_event_simulation,
    load_latest_simulation_run,
    load_risk_incident_occurrences,
    load_simulation_events,
    load_trading_risk_incidents,
    pause_event_simulation,
    reset_event_simulation,
    simulate_risk_event,
    set_event_simulation_speed,
    set_event_simulation_thresholds,
    start_event_simulation,
    transition_simulation_market,
)
from sportsops.models.match import AlertInfo, Match, MatchDetails
from sportsops.models.historical import (
    HistoricalFilterOptions,
    HistoricalImportBatchInfo,
    HistoricalImportResult,
    HistoricalMatchInfo,
    HistoricalTeamSummary,
)
from sportsops.models.trading import (
    MarketRow,
    OddsMovementInfo,
    RiskThresholds,
    TradingAuditInfo,
    TradingRiskInfo,
)
from sportsops.models.simulation import (
    RiskOccurrenceInfo,
    SimulationEventInfo,
    SimulationRunInfo,
    TradingRiskIncidentInfo,
)


class DatabaseService:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def load_matches(self) -> tuple[Match, ...]:
        with Session(self._engine) as session:
            return load_matches(session)

    def load_historical_matches(self) -> tuple[HistoricalMatchInfo, ...]:
        with Session(self._engine) as session:
            return load_historical_matches(session)

    def load_historical_match_page(
        self,
        *,
        competition_name: str | None = None,
        season_name: str | None = None,
        team_name: str | None = None,
        team_name_exact: str | None = None,
        search_text: str = "",
        result: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        page: int = 0,
        page_size: int = 200,
        sort_key: str = "kickoff_date",
        descending: bool = True,
    ) -> tuple[tuple[HistoricalMatchInfo, ...], int]:
        with Session(self._engine) as session:
            return load_historical_match_page(
                session,
                competition_name=competition_name,
                season_name=season_name,
                team_name=team_name,
                team_name_exact=team_name_exact,
                search_text=search_text,
                result=result,
                start_date=start_date,
                end_date=end_date,
                page=page,
                page_size=page_size,
                sort_key=sort_key,
                descending=descending,
            )

    def load_historical_filter_options(
        self,
        competition_name: str | None = None,
        season_name: str | None = None,
        team_name: str | None = None,
    ) -> HistoricalFilterOptions:
        with Session(self._engine) as session:
            return load_historical_filter_options(
                session,
                competition_name=competition_name,
                season_name=season_name,
                team_name=team_name,
            )

    def load_historical_facet_options(
        self,
        facet: str,
        *,
        competition_name: str | None = None,
        season_name: str | None = None,
        team_name: str | None = None,
    ) -> tuple[str, ...]:
        with Session(self._engine) as session:
            return load_historical_facet_options(
                session,
                facet,
                competition_name=competition_name,
                season_name=season_name,
                team_name=team_name,
            )

    def load_historical_team_summary(
        self,
        *,
        competition_name: str | None = None,
        season_name: str | None = None,
        team_name: str | None = None,
        team_name_exact: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        result: str | None = None,
        search_text: str = "",
    ) -> tuple[HistoricalTeamSummary, ...]:
        with Session(self._engine) as session:
            return load_historical_team_summary(
                session,
                competition_name=competition_name,
                season_name=season_name,
                team_name=team_name,
                team_name_exact=team_name_exact,
                start_date=start_date,
                end_date=end_date,
                result=result,
                search_text=search_text,
            )

    def load_historical_import_batches(
        self,
    ) -> tuple[HistoricalImportBatchInfo, ...]:
        with Session(self._engine) as session:
            return load_historical_import_batches(session)

    def import_historical_csv(
        self,
        file_path: str | Path,
        *,
        competition_name: str,
        country: str,
        season_name: str,
        season_start: date,
        season_end: date,
        source_name: str,
        source_url: str,
        reuse_terms: str,
    ) -> HistoricalImportResult:
        with Session(self._engine) as session, session.begin():
            return import_historical_csv(
                session,
                file_path,
                competition_name=competition_name,
                country=country,
                season_name=season_name,
                season_start=season_start,
                season_end=season_end,
                source_name=source_name,
                source_url=source_url,
                reuse_terms=reuse_terms,
            )

    def load_match_details(self, external_id: str) -> MatchDetails:
        with Session(self._engine) as session:
            return load_match_details(session, external_id)

    def load_alerts(self) -> tuple[AlertInfo, ...]:
        with Session(self._engine) as session:
            return load_alerts(session)

    def acknowledge_alert(self, alert_id: int) -> bool:
        with Session(self._engine) as session, session.begin():
            return acknowledge_alert(session, alert_id)

    def load_markets(self) -> tuple[MarketRow, ...]:
        with Session(self._engine) as session:
            return load_markets(session)

    def load_odds_history(
        self, selection_id: int
    ) -> tuple[OddsMovementInfo, ...]:
        with Session(self._engine) as session:
            return load_odds_history(session, selection_id)

    def load_trading_risk_alerts(self) -> tuple[TradingRiskInfo, ...]:
        with Session(self._engine) as session:
            return load_trading_risk_alerts(session)

    def load_trading_audit_log(self) -> tuple[TradingAuditInfo, ...]:
        with Session(self._engine) as session:
            return load_trading_audit_log(session)

    def suspend_market(self, market_id: int, reason: str) -> bool:
        with Session(self._engine) as session, session.begin():
            return transition_market(session, market_id, "SUSPENDED", reason)

    def reopen_market(self, market_id: int, reason: str = "") -> bool:
        with Session(self._engine) as session, session.begin():
            return transition_market(session, market_id, "OPEN", reason)

    def acknowledge_trading_risk_alert(
        self, alert_id: int, note: str | None = None
    ) -> bool:
        with Session(self._engine) as session, session.begin():
            return acknowledge_trading_risk_alert(session, alert_id, note)

    def load_trading_risk_incidents(
        self, scope: str = "current"
    ) -> tuple[TradingRiskIncidentInfo, ...]:
        with Session(self._engine) as session:
            return load_trading_risk_incidents(session, scope)

    def load_risk_incident_occurrences(
        self, incident_id: int
    ) -> tuple[RiskOccurrenceInfo, ...]:
        with Session(self._engine) as session:
            return load_risk_incident_occurrences(session, incident_id)

    def acknowledge_trading_risk_incident(
        self, incident_id: int, note: str | None = None
    ) -> bool:
        with Session(self._engine) as session, session.begin():
            return acknowledge_trading_risk_incident(session, incident_id, note)

    def load_latest_simulation_run(self) -> SimulationRunInfo | None:
        with Session(self._engine) as session:
            return load_latest_simulation_run(session)

    def load_simulation_events(
        self, match_id: str
    ) -> tuple[SimulationEventInfo, ...]:
        with Session(self._engine) as session:
            return load_simulation_events(session, match_id)

    def start_event_simulation(
        self, seed: int, speed: int, thresholds: RiskThresholds
    ) -> SimulationRunInfo:
        with Session(self._engine) as session, session.begin():
            run = start_event_simulation(session, seed, speed, thresholds)
            run_id = run.run_id
        with Session(self._engine) as session:
            info = load_latest_simulation_run(session)
        if info is None or info.run_id != run_id:
            raise RuntimeError("The simulation run could not be reloaded after start.")
        return info

    def pause_event_simulation(self, run_id: str) -> bool:
        with Session(self._engine) as session, session.begin():
            return pause_event_simulation(session, run_id)

    def set_event_simulation_speed(self, run_id: str, speed: int) -> None:
        with Session(self._engine) as session, session.begin():
            set_event_simulation_speed(session, run_id, speed)

    def set_event_simulation_thresholds(
        self, run_id: str, thresholds: RiskThresholds
    ) -> None:
        with Session(self._engine) as session, session.begin():
            set_event_simulation_thresholds(session, run_id, thresholds)

    def advance_event_simulation(self, run_id: str) -> int:
        with Session(self._engine) as session, session.begin():
            return advance_event_simulation(session, run_id)

    def simulate_risk_event(self, run_id: str, selection_id: int) -> str:
        with Session(self._engine) as session, session.begin():
            return simulate_risk_event(session, run_id, selection_id)

    def reset_event_simulation(self, run_id: str) -> bool:
        with Session(self._engine) as session, session.begin():
            return reset_event_simulation(session, run_id)

    def set_simulation_market_status(
        self, market_id: int, status: str, reason: str
    ) -> bool:
        with Session(self._engine) as session, session.begin():
            return transition_simulation_market(
                session, market_id, status, reason
            )

    def record_trading_operator_action(
        self,
        event_type: str,
        reason: str,
        old_value: str | None = None,
        new_value: str | None = None,
    ) -> None:
        with Session(self._engine) as session, session.begin():
            record_trading_operator_action(
                session, event_type, reason, old_value, new_value
            )
