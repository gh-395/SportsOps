from datetime import datetime, timezone
from dataclasses import replace
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased

from sportsops.database.models import (
    Competition,
    Market,
    MarketSelection,
    MatchRecord,
    OddsHistory,
    Season,
    SimulationMarketControl,
    Team,
    TradingAuditLog,
    TradingRiskAlert,
)
from sportsops.database.simulation_repository import (
    load_current_simulation_overlays,
    load_latest_simulation_history_by_selection,
    load_simulation_audit_log,
    load_simulation_market_controls,
    load_simulation_odds_history,
)
from sportsops.models.trading import (
    MarketRow,
    OddsMovementInfo,
    TradingAuditInfo,
    TradingRiskInfo,
)

_PERCENT_QUANTUM = Decimal("0.01")


def load_markets(session: Session) -> tuple[MarketRow, ...]:
    home_team = aliased(Team, name="market_home_team")
    away_team = aliased(Team, name="market_away_team")
    latest_history = (
        select(
            OddsHistory.selection_id.label("selection_id"),
            OddsHistory.previous_odds.label("previous_odds"),
            OddsHistory.current_odds.label("current_odds"),
            OddsHistory.changed_at.label("changed_at"),
            func.row_number()
            .over(
                partition_by=OddsHistory.selection_id,
                order_by=(OddsHistory.changed_at.desc(), OddsHistory.id.desc()),
            )
            .label("row_number"),
        )
        .subquery()
    )
    statement = (
        select(
            Market,
            MarketSelection,
            MatchRecord.id,
            MatchRecord.external_id,
            MatchRecord.status,
            MatchRecord.elapsed_minute,
            MatchRecord.home_score,
            MatchRecord.away_score,
            Competition.name,
            home_team.name,
            away_team.name,
            latest_history.c.previous_odds,
        )
        .join(MarketSelection, MarketSelection.market_id == Market.id)
        .join(MatchRecord, MatchRecord.id == Market.match_id)
        .join(Season, Season.id == MatchRecord.season_id)
        .join(Competition, Competition.id == Season.competition_id)
        .join(home_team, home_team.id == MatchRecord.home_team_id)
        .join(away_team, away_team.id == MatchRecord.away_team_id)
        .outerjoin(
            latest_history,
            (latest_history.c.selection_id == MarketSelection.id)
            & (latest_history.c.row_number == 1),
        )
        .order_by(MatchRecord.external_id, Market.market_type, MarketSelection.id)
    )
    quotes, market_states, match_states = load_current_simulation_overlays(session)
    control_statuses = load_simulation_market_controls(session)
    latest_simulation_history = load_latest_simulation_history_by_selection(session)
    market_rows = []
    for row in session.execute(statement).all():
        market_row = _to_market_row(row)
        quote = quotes.get(market_row.selection_id)
        market_state = market_states.get(market_row.market_id)
        match_state = match_states.get(row[2])
        latest_history = latest_simulation_history.get(market_row.selection_id)
        previous_odds = (
            latest_history.previous_odds
            if latest_history is not None
            else market_row.previous_odds
        )
        current_odds = quote.decimal_odds if quote is not None else market_row.decimal_odds
        market_status = (
            market_state.status
            if market_state is not None
            else control_statuses.get(market_row.market_id, market_row.market_status)
        )
        market_row = replace(
            market_row,
            decimal_odds=current_odds,
            implied_probability=(Decimal("100") / current_odds).quantize(
                _PERCENT_QUANTUM, rounding=ROUND_HALF_UP
            ),
            market_status=market_status,
            previous_odds=previous_odds,
            absolute_change=(
                abs(current_odds - previous_odds)
                if previous_odds is not None
                else Decimal("0")
            ),
            percentage_change=(
                _percentage_change(previous_odds, current_odds)
                if previous_odds is not None
                else Decimal("0")
            ),
            last_update=quote.updated_at if quote is not None else market_row.last_update,
            home_score=(
                match_state.home_score
                if match_state is not None
                else market_row.home_score
            ),
            away_score=(
                match_state.away_score
                if match_state is not None
                else market_row.away_score
            ),
            match_minute=(
                match_state.minute
                if match_state is not None
                else market_row.match_minute
            ),
            match_status=(
                match_state.status
                if match_state is not None
                else market_row.match_status
            ),
            home_red_cards=match_state.home_red_cards if match_state else 0,
            away_red_cards=match_state.away_red_cards if match_state else 0,
        )
        market_rows.append(market_row)
    return tuple(market_rows)


def load_odds_history(
    session: Session, selection_id: int, limit: int = 200
) -> tuple[OddsMovementInfo, ...]:
    rows = session.execute(
        select(
            OddsHistory,
            MatchRecord.external_id,
            Market.market_type,
            MarketSelection.selection_name,
        )
        .join(MarketSelection, MarketSelection.id == OddsHistory.selection_id)
        .join(Market, Market.id == MarketSelection.market_id)
        .join(MatchRecord, MatchRecord.id == Market.match_id)
        .where(OddsHistory.selection_id == selection_id)
        .order_by(OddsHistory.changed_at.desc(), OddsHistory.id.desc())
        .limit(limit)
    ).all()
    legacy = [
        OddsMovementInfo(
            history_id=history.id,
            selection_id=history.selection_id,
            match_id=external_id,
            market_type=market_type,
            selection_name=selection_name,
            previous_odds=history.previous_odds,
            current_odds=history.current_odds,
            absolute_change=abs(history.current_odds - history.previous_odds),
            percentage_change=_percentage_change(
                history.previous_odds, history.current_odds
            ),
            changed_at=history.changed_at,
            source=history.source,
            simulated=history.is_simulated,
            reason="Legacy simulated odds movement.",
        )
        for history, external_id, market_type, selection_name in rows
    ]
    simulated = [
        OddsMovementInfo(
            history_id=history.id,
            selection_id=history.selection_id,
            match_id=external_id,
            market_type=market_type,
            selection_name=selection_name,
            previous_odds=history.previous_odds,
            current_odds=history.current_odds,
            absolute_change=abs(history.current_odds - history.previous_odds),
            percentage_change=history.percentage_change.quantize(
                _PERCENT_QUANTUM, rounding=ROUND_HALF_UP
            ),
            changed_at=history.changed_at,
            source=f"EVENT: {history.cause_type}",
            simulated=True,
            reason=history.reason,
        )
        for history in load_simulation_odds_history(session, selection_id, limit)
        for external_id, market_type, selection_name in [
            session.execute(
                select(
                    MatchRecord.external_id,
                    Market.market_type,
                    MarketSelection.selection_name,
                )
                .select_from(MarketSelection)
                .join(Market, Market.id == MarketSelection.market_id)
                .join(MatchRecord, MatchRecord.id == Market.match_id)
                .where(MarketSelection.id == selection_id)
            ).one()
        ]
    ]
    combined = legacy + simulated
    combined.sort(key=lambda item: _datetime_sort_key(item.changed_at), reverse=True)
    return tuple(combined[:limit])


def load_trading_risk_alerts(session: Session) -> tuple[TradingRiskInfo, ...]:
    rows = session.execute(
        select(
            TradingRiskAlert,
            MatchRecord.external_id,
            Market.market_type,
            MarketSelection.selection_name,
        )
        .join(OddsHistory, OddsHistory.id == TradingRiskAlert.odds_history_id)
        .join(MarketSelection, MarketSelection.id == OddsHistory.selection_id)
        .join(Market, Market.id == MarketSelection.market_id)
        .join(MatchRecord, MatchRecord.id == Market.match_id)
        .order_by(
            TradingRiskAlert.acknowledged_at.is_not(None),
            case((TradingRiskAlert.severity == "ERROR", 1), else_=2),
            TradingRiskAlert.detected_at.desc(),
            TradingRiskAlert.id.desc(),
        )
    ).all()
    return tuple(
        TradingRiskInfo(
            alert_id=alert.id,
            match_id=match_id,
            market_type=market_type,
            selection_name=selection_name,
            severity=alert.severity,
            description=alert.description,
            detected_at=alert.detected_at,
            acknowledged_at=alert.acknowledged_at,
            acknowledgement_note=alert.acknowledgement_note,
        )
        for alert, match_id, market_type, selection_name in rows
    )


def load_trading_audit_log(
    session: Session, limit: int = 1000
) -> tuple[TradingAuditInfo, ...]:
    rows = session.execute(
        select(
            TradingAuditLog,
            MatchRecord.external_id,
            Market.market_type,
            MarketSelection.selection_name,
        )
        .outerjoin(Market, Market.id == TradingAuditLog.market_id)
        .outerjoin(MatchRecord, MatchRecord.id == Market.match_id)
        .outerjoin(
            MarketSelection, MarketSelection.id == TradingAuditLog.selection_id
        )
        .order_by(TradingAuditLog.created_at.desc(), TradingAuditLog.id.desc())
        .limit(limit)
    ).all()
    legacy = [
        TradingAuditInfo(
            audit_id=audit.id,
            match_id=match_id or "SYSTEM",
            market_type=market_type or "SYSTEM",
            selection_name=selection_name or "—",
            event_type=audit.event_type,
            details=_audit_details(audit),
            actor=audit.actor,
            created_at=audit.created_at,
            simulated=audit.is_simulated,
        )
        for audit, match_id, market_type, selection_name in rows
    ]
    simulated = [
        TradingAuditInfo(
            audit_id=-entry.audit_id,
            match_id=entry.match_id,
            market_type=entry.market_type,
            selection_name=entry.selection_name,
            event_type=entry.event_type,
            details=entry.details,
            actor=entry.actor,
            created_at=entry.created_at,
            simulated=True,
        )
        for entry in load_simulation_audit_log(session, limit)
    ]
    combined = legacy + simulated
    combined.sort(key=lambda item: _datetime_sort_key(item.created_at), reverse=True)
    return tuple(combined[:limit])


def record_trading_operator_action(
    session: Session,
    event_type: str,
    reason: str,
    old_value: str | None = None,
    new_value: str | None = None,
    actor: str = "Operator",
) -> None:
    allowed_events = {
        "SIMULATION_STARTED",
        "SIMULATION_PAUSED",
        "RISK_THRESHOLDS_CHANGED",
    }
    if event_type not in allowed_events:
        raise ValueError(f"Unsupported operator audit action {event_type}.")
    if not reason.strip():
        raise ValueError("An audit reason is required.")
    session.add(
        TradingAuditLog(
            market_id=None,
            event_type=event_type,
            old_value=old_value,
            new_value=new_value,
            reason=reason.strip(),
            actor=actor,
            is_simulated=True,
        )
    )
    session.flush()


def transition_market(
    session: Session,
    market_id: int,
    target_status: str,
    reason: str,
    actor: str = "Operator",
) -> bool:
    market = session.get(Market, market_id)
    if market is None:
        raise LookupError(f"Market {market_id} was not found.")
    cleaned_reason = reason.strip()
    if target_status == "SUSPENDED" and not cleaned_reason:
        raise ValueError("A reason is required to suspend a market.")
    if market.status == "CLOSED":
        raise ValueError("Closed markets cannot be reopened or suspended.")
    if target_status not in ("OPEN", "SUSPENDED"):
        raise ValueError(f"Unsupported market status {target_status}.")
    if market.status == target_status:
        return False
    expected_status = "OPEN" if target_status == "SUSPENDED" else "SUSPENDED"
    if market.status != expected_status:
        raise ValueError(
            f"Cannot move market from {market.status} to {target_status}."
        )
    old_status = market.status
    market.status = target_status
    market.updated_at = datetime.now(timezone.utc)
    control = session.get(SimulationMarketControl, market.id)
    if control is not None:
        control.manually_suspended = target_status == "SUSPENDED"
        control.suspension_reason = (
            cleaned_reason if target_status == "SUSPENDED" else None
        )
        control.updated_at = datetime.now(timezone.utc)
    session.add(
        TradingAuditLog(
            market_id=market.id,
            event_type=(
                "MARKET_SUSPENDED"
                if target_status == "SUSPENDED"
                else "MARKET_REOPENED"
            ),
            old_value=old_status,
            new_value=target_status,
            reason=cleaned_reason or "Operator reopened the market.",
            actor=actor,
            is_simulated=market.is_simulated,
        )
    )
    session.flush()
    return True


def acknowledge_trading_risk_alert(
    session: Session, alert_id: int, note: str | None = None
) -> bool:
    alert = session.get(TradingRiskAlert, alert_id)
    if alert is None:
        raise LookupError(f"Trading risk alert {alert_id} was not found.")
    if alert.acknowledged_at is not None:
        return False
    alert.acknowledged_at = datetime.now(timezone.utc)
    alert.acknowledgement_note = note.strip() if note and note.strip() else None
    history = session.get(OddsHistory, alert.odds_history_id)
    selection = session.get(MarketSelection, history.selection_id)
    market = session.get(Market, selection.market_id)
    session.add(
        TradingAuditLog(
            market_id=market.id,
            selection_id=selection.id,
            odds_history_id=history.id,
            event_type="RISK_ALERT_ACKNOWLEDGED",
            new_value=alert.severity,
            reason=alert.acknowledgement_note or "Trading risk alert acknowledged.",
            actor="Operator",
            is_simulated=alert.is_simulated,
        )
    )
    session.flush()
    return True


def _to_market_row(row) -> MarketRow:
    market, selection = row[0], row[1]
    previous_odds = row[11]
    current_odds = selection.current_odds
    return MarketRow(
        market_id=market.id,
        selection_id=selection.id,
        match_id=row[3],
        competition=row[8],
        fixture=f"{row[9]} vs {row[10]}",
        market_type=market.market_type,
        selection_key=selection.selection_key,
        selection_name=selection.selection_name,
        decimal_odds=current_odds,
        implied_probability=(
            Decimal("100") / current_odds
        ).quantize(_PERCENT_QUANTUM, rounding=ROUND_HALF_UP),
        market_status=market.status,
        previous_odds=previous_odds,
        absolute_change=(
            abs(current_odds - previous_odds)
            if previous_odds is not None
            else Decimal("0")
        ),
        percentage_change=(
            _percentage_change(previous_odds, current_odds)
            if previous_odds is not None
            else Decimal("0")
        ),
        last_update=market.updated_at,
        simulated=market.is_simulated,
        home_score=row[6],
        away_score=row[7],
        match_minute=row[5],
        match_status=row[4],
    )


def _percentage_change(previous: Decimal, current: Decimal) -> Decimal:
    return (
        ((current - previous) / previous) * Decimal("100")
    ).quantize(_PERCENT_QUANTUM, rounding=ROUND_HALF_UP)


def _datetime_sort_key(value: datetime) -> float:
    normalized = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
    return normalized.timestamp()


def _audit_details(audit: TradingAuditLog) -> str:
    old_value = f"{audit.old_value} -> " if audit.old_value else ""
    new_value = audit.new_value or ""
    return f"{old_value}{new_value} {audit.reason}".strip()
