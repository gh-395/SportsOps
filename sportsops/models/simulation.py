from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class SimulationMatchInfo:
    match_id: str
    fixture: str
    status: str
    minute: int
    home_score: int
    away_score: int
    home_red_cards: int
    away_red_cards: int
    updated_at: datetime


@dataclass(frozen=True)
class SimulationEventInfo:
    event_id: int
    match_id: str
    sequence: int
    minute: int
    event_type: str
    team: str
    description: str
    occurred_at: datetime


@dataclass(frozen=True)
class SimulationRunInfo:
    run_id: str
    seed: int
    speed: int
    status: str
    started_at: datetime
    matches: tuple[SimulationMatchInfo, ...]


@dataclass(frozen=True)
class RiskOccurrenceInfo:
    severity: str
    description: str
    occurred_at: datetime
    previous_odds: Decimal | None
    current_odds: Decimal | None
    percentage_change: Decimal | None


@dataclass(frozen=True)
class TradingRiskIncidentInfo:
    incident_id: int
    match_id: str
    market_type: str
    selection_name: str
    rule_code: str
    severity: str
    description: str
    occurrence_count: int
    first_seen_at: datetime
    last_seen_at: datetime
    status: str
    acknowledged_at: datetime | None
    acknowledgement_note: str | None
    run_id: str | None = None
    run_status: str | None = None


@dataclass(frozen=True)
class SimulationAuditInfo:
    audit_id: int
    match_id: str
    market_type: str
    selection_name: str
    event_type: str
    details: str
    actor: str
    created_at: datetime
