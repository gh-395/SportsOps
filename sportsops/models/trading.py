from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class RiskThresholds:
    warning_percent: Decimal = Decimal("10")
    critical_percent: Decimal = Decimal("15")

    def __post_init__(self) -> None:
        if self.warning_percent <= 0:
            raise ValueError("The warning threshold must be greater than zero.")
        if self.critical_percent < self.warning_percent:
            raise ValueError(
                "The critical threshold must be at least the warning threshold."
            )


@dataclass(frozen=True)
class MarketRow:
    market_id: int
    selection_id: int
    match_id: str
    competition: str
    fixture: str
    market_type: str
    selection_key: str
    selection_name: str
    decimal_odds: Decimal
    implied_probability: Decimal
    market_status: str
    previous_odds: Decimal | None
    absolute_change: Decimal
    percentage_change: Decimal
    last_update: datetime
    simulated: bool
    home_score: int | None = None
    away_score: int | None = None
    match_minute: int | None = None
    match_status: str | None = None
    home_red_cards: int = 0
    away_red_cards: int = 0


@dataclass(frozen=True)
class OddsMovementInfo:
    history_id: int
    selection_id: int
    match_id: str
    market_type: str
    selection_name: str
    previous_odds: Decimal
    current_odds: Decimal
    absolute_change: Decimal
    percentage_change: Decimal
    changed_at: datetime
    source: str
    simulated: bool
    reason: str = ""


@dataclass(frozen=True)
class TradingRiskInfo:
    alert_id: int
    match_id: str
    market_type: str
    selection_name: str
    severity: str
    description: str
    detected_at: datetime
    acknowledged_at: datetime | None
    acknowledgement_note: str | None


@dataclass(frozen=True)
class TradingAuditInfo:
    audit_id: int
    match_id: str
    market_type: str
    selection_name: str
    event_type: str
    details: str
    actor: str
    created_at: datetime
    simulated: bool
