import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from sqlalchemy import and_, false, func, or_, select
from sqlalchemy.orm import Session, aliased

from sportsops.database.models import (
    Market,
    MarketSelection,
    MatchEvent,
    MatchRecord,
    OddsHistory,
    SimulationAuditLog,
    SimulationEvent,
    SimulationMarketControl,
    SimulationMarketState,
    SimulationMatchState,
    SimulationOddsHistory,
    SimulationRun,
    SimulationSelectionQuote,
    Team,
    TradingAuditLog,
    TradingRiskAlert,
    TradingRiskIncident,
    TradingRiskOccurrence,
)
from sportsops.models.simulation import (
    SimulationEventInfo,
    SimulationMatchInfo,
    SimulationRunInfo,
)
from sportsops.models.trading import RiskThresholds
from sportsops.services.football_simulation import (
    MAXIMUM_ODDS,
    MINIMUM_ODDS,
    ScheduledFootballEvent,
    calculate_event_driven_odds,
    calculate_fair_probabilities,
    generate_match_schedule,
    match_goal_rates,
)

_ODDS_QUANTUM = Decimal("0.001")
_PERCENT_QUANTUM = Decimal("0.0001")
_COOLDOWN = timedelta(seconds=60)
_EXPECTED_REPRICE_CAUSES = frozenset(
    {
        "CLOCK",
        "EVENT_REOPEN",
        "FULL_TIME",
        "GOAL",
        "HALF_TIME",
        "KICK_OFF",
        "MANUAL_REOPEN",
        "RED_CARD",
        "RUN_START",
        "SECOND_HALF",
    }
)


def initialize_phase6_data(session: Session) -> None:
    markets = session.scalars(select(Market).order_by(Market.id)).all()
    existing_controls = set(
        session.scalars(select(SimulationMarketControl.market_id))
    )
    for market in markets:
        if market.id in existing_controls:
            continue
        reason = None
        if market.status == "SUSPENDED":
            reason = "Existing Phase 4 suspension retained from the trading audit."
        session.add(
            SimulationMarketControl(
                market_id=market.id,
                manually_suspended=market.status == "SUSPENDED",
                suspension_reason=reason,
            )
        )
    session.flush()
    _backfill_legacy_risk_incidents(session)


def start_event_simulation(
    session: Session,
    seed: int,
    speed: int,
    thresholds: RiskThresholds,
) -> SimulationRun:
    if speed not in (1, 5, 10):
        raise ValueError("Simulation speed must be 1x, 5x, or 10x.")
    if not -(2**31) <= seed < 2**31:
        raise ValueError("Simulation seed must fit a signed 32-bit integer.")
    latest = _latest_run(session)
    if latest and latest.status in ("RUNNING", "PAUSED"):
        if latest.status == "PAUSED":
            latest.status = "RUNNING"
            latest.speed = speed
            _record_audit(
                session,
                "SIMULATION_RESUMED",
                "Operator resumed the deterministic football simulation.",
                run_id=latest.run_id,
                old_value="PAUSED",
                new_value="RUNNING",
            )
        return latest

    run = SimulationRun(
        run_id=uuid4().hex,
        seed=seed,
        speed=speed,
        status="RUNNING",
        warning_percent=thresholds.warning_percent,
        critical_percent=thresholds.critical_percent,
        started_at=_utc_now(),
    )
    session.add(run)
    session.flush()
    _record_audit(
        session,
        "SIMULATION_STARTED",
        f"Deterministic event simulation started with seed {seed} at {speed}x.",
        run_id=run.run_id,
        new_value=f"seed={seed}; speed={speed}x",
    )

    home = aliased(Team, name="simulation_home")
    away = aliased(Team, name="simulation_away")
    matches = session.execute(
        select(MatchRecord, home.name, away.name)
        .join(home, home.id == MatchRecord.home_team_id)
        .join(away, away.id == MatchRecord.away_team_id)
        .where(
            MatchRecord.is_simulated.is_(True),
            MatchRecord.status.in_(("SCHEDULED", "LIVE", "HALF_TIME")),
            select(Market.id)
            .where(
                Market.match_id == MatchRecord.id,
                Market.is_simulated.is_(True),
            )
            .exists(),
        )
        .order_by(MatchRecord.external_id)
    ).all()

    for match, home_name, away_name in matches:
        start_status = match.status
        start_minute = (
            45
            if start_status == "HALF_TIME"
            else (match.elapsed_minute or 0)
        )
        home_score = match.home_score or 0
        away_score = match.away_score or 0
        home_rate, away_rate = match_goal_rates(seed, match.id)
        schedule = generate_match_schedule(
            seed=seed,
            match_id=match.id,
            match_key=match.external_id,
            home_team=home_name,
            away_team=away_name,
            start_minute=start_minute,
            start_status=start_status,
            home_goal_rate=home_rate,
            away_goal_rate=away_rate,
        )
        match_state = SimulationMatchState(
            run_id=run.run_id,
            match_id=match.id,
            status=start_status,
            minute=start_minute,
            home_score=home_score,
            away_score=away_score,
            home_red_cards=0,
            away_red_cards=0,
            home_goal_rate=home_rate,
            away_goal_rate=away_rate,
            event_schedule=json.dumps(
                [_event_to_json(event) for event in schedule],
                separators=(",", ":"),
            ),
        )
        session.add(match_state)
        session.flush()
        markets_for_match = session.scalars(
            select(Market)
            .where(
                Market.match_id == match.id,
                Market.is_simulated.is_(True),
            )
            .order_by(Market.id)
        ).all()
        for market in markets_for_match:
            control = session.get(SimulationMarketControl, market.id)
            if control is None:
                control = SimulationMarketControl(
                    market_id=market.id,
                    manually_suspended=market.status == "SUSPENDED",
                )
                session.add(control)
                session.flush()
            if market.status == "CLOSED":
                status, suspension_kind = "CLOSED", "CLOSED"
            elif control.manually_suspended:
                status, suspension_kind = "SUSPENDED", "MANUAL"
            else:
                status, suspension_kind = "OPEN", "NONE"
            market_state = SimulationMarketState(
                run_id=run.run_id,
                market_id=market.id,
                status=status,
                suspension_kind=suspension_kind,
                suspension_reason=control.suspension_reason,
            )
            session.add(market_state)
        session.flush()
        if any(
            item.status == "OPEN"
            for item in session.scalars(
                select(SimulationMarketState).where(
                    SimulationMarketState.run_id == run.run_id,
                    SimulationMarketState.market_id.in_(
                        [item.id for item in markets_for_match]
                    ),
                )
            )
        ):
            _reprice_match(
                session,
                run,
                match_state,
                match,
                None,
                "RUN_START",
                "RUN_START",
                detect_risk=False,
            )
    return run


def simulate_risk_event(session: Session, run_id: str, selection_id: int) -> str:
    """Inject one explicitly labeled demo odds anomaly into an active simulation."""
    run = _get_run(session, run_id)
    if run.status != "RUNNING":
        raise ValueError("Start or resume a simulation before testing Trading Risk.")
    selection = session.get(MarketSelection, selection_id)
    if selection is None:
        raise ValueError("The selected market selection no longer exists.")
    market = session.get(Market, selection.market_id)
    if market is None or not market.is_simulated:
        raise ValueError("Risk tests are only supported for simulated markets.")
    state = session.scalar(
        select(SimulationMarketState).where(
            SimulationMarketState.run_id == run_id,
            SimulationMarketState.market_id == market.id,
        )
    )
    if state is None or state.status != "OPEN":
        raise ValueError("Select an open market in the active simulation.")
    quote = session.scalar(
        select(SimulationSelectionQuote).where(
            SimulationSelectionQuote.run_id == run_id,
            SimulationSelectionQuote.selection_id == selection_id,
        )
    )
    if quote is None:
        raise ValueError("No simulated quote exists for the selected market.")

    previous = quote.decimal_odds
    # Choose a movement above the critical threshold, within configured odds limits.
    magnitude = max(run.critical_percent + Decimal("5"), Decimal("20"))
    multiplier = Decimal("1") + magnitude / Decimal("100")
    upwards = (previous * multiplier).quantize(_ODDS_QUANTUM, rounding=ROUND_HALF_UP)
    downwards = (previous / multiplier).quantize(_ODDS_QUANTUM, rounding=ROUND_HALF_UP)
    candidates = [value for value in (upwards, downwards)
                  if MINIMUM_ODDS <= value <= MAXIMUM_ODDS and value != previous]
    if not candidates:
        raise ValueError("No valid demo movement is possible for this quote.")
    new_odds = max(candidates, key=lambda value: abs((value - previous) / previous))
    movement = (new_odds - previous) / previous * Decimal("100")
    severity = _risk_severity(movement, run.warning_percent,
                              run.critical_percent, "RISK_TEST")
    if severity is None:
        raise ValueError("Risk thresholds are too high for a valid demo movement.")
    _validate_simulation_quote(new_odds, quote.fair_probability, selection_id)
    reason = "DEMO RISK TEST: operator-injected unexpected odds movement."
    history = SimulationOddsHistory(
        run_id=run_id, market_id=market.id, selection_id=selection_id,
        event_id=None, previous_odds=previous, current_odds=new_odds,
        fair_probability=quote.fair_probability,
        percentage_change=movement.quantize(_PERCENT_QUANTUM, rounding=ROUND_HALF_UP),
        cause_type="RISK_TEST", reason=reason, changed_at=_utc_now(),
        idempotency_key=f"{run_id}:RISK_TEST:{uuid4().hex}:{selection_id}",
    )
    session.add(history)
    session.flush()
    quote.decimal_odds = new_odds
    quote.reason = reason
    quote.event_id = None
    quote.updated_at = history.changed_at
    _record_audit(
        session, "DEMO_RISK_TEST", reason, run_id=run_id,
        match_id=market.match_id, market_id=market.id, selection_id=selection_id,
        old_value=f"{previous:.3f}", new_value=f"{new_odds:.3f}",
    )
    _record_risk_occurrence(session, run, market.match_id, market,
                            selection, history, severity, movement)
    session.flush()
    return severity


def pause_event_simulation(session: Session, run_id: str) -> bool:
    run = _get_run(session, run_id)
    if run.status != "RUNNING":
        return False
    run.status = "PAUSED"
    _record_audit(
        session,
        "SIMULATION_PAUSED",
        "Operator paused the deterministic football simulation.",
        run_id=run.run_id,
        old_value="RUNNING",
        new_value="PAUSED",
    )
    return True


def set_event_simulation_speed(session: Session, run_id: str, speed: int) -> None:
    if speed not in (1, 5, 10):
        raise ValueError("Simulation speed must be 1x, 5x, or 10x.")
    run = _get_run(session, run_id)
    if run.status not in ("RUNNING", "PAUSED"):
        raise ValueError("Simulation speed can only change during an active run.")
    if run.speed == speed:
        return
    old_speed = run.speed
    run.speed = speed
    _record_audit(
        session,
        "SIMULATION_SPEED_CHANGED",
        "Operator changed the deterministic simulation speed.",
        run_id=run.run_id,
        old_value=f"{old_speed}x",
        new_value=f"{speed}x",
    )


def set_event_simulation_thresholds(
    session: Session, run_id: str, thresholds: RiskThresholds
) -> None:
    run = _get_run(session, run_id)
    if run.status not in ("RUNNING", "PAUSED"):
        raise ValueError("Risk thresholds can only change during an active run.")
    old_value = f"{run.warning_percent}/{run.critical_percent}"
    new_value = (
        f"{thresholds.warning_percent}/{thresholds.critical_percent}"
    )
    if old_value == new_value:
        return
    run.warning_percent = thresholds.warning_percent
    run.critical_percent = thresholds.critical_percent
    _record_audit(
        session,
        "SIMULATION_THRESHOLDS_CHANGED",
        "Operator changed this run's risk thresholds.",
        run_id=run.run_id,
        old_value=old_value,
        new_value=new_value,
        actor="Operator",
    )


def reset_event_simulation(session: Session, run_id: str) -> bool:
    run = _get_run(session, run_id)
    if run.status == "RESET":
        return False
    old_status = run.status
    run.status = "RESET"
    run.completed_at = _utc_now()
    _record_audit(
        session,
        "SIMULATION_RESET",
        "Simulation run was reset; its events, quotes, and audit history were retained.",
        run_id=run.run_id,
        old_value=old_status,
        new_value="RESET",
    )
    return True


def load_latest_simulation_run(session: Session) -> SimulationRunInfo | None:
    run = _latest_run(session)
    if run is None or run.status == "RESET":
        return None
    home = aliased(Team, name="run_home")
    away = aliased(Team, name="run_away")
    rows = session.execute(
        select(
            SimulationMatchState,
            MatchRecord.external_id,
            home.name,
            away.name,
        )
        .join(MatchRecord, MatchRecord.id == SimulationMatchState.match_id)
        .join(home, home.id == MatchRecord.home_team_id)
        .join(away, away.id == MatchRecord.away_team_id)
        .where(SimulationMatchState.run_id == run.run_id)
        .order_by(MatchRecord.external_id)
    ).all()
    match_infos = tuple(
        SimulationMatchInfo(
            match_id=external_id,
            fixture=f"{home_name} vs {away_name}",
            status=state.status,
            minute=state.minute,
            home_score=state.home_score,
            away_score=state.away_score,
            home_red_cards=state.home_red_cards,
            away_red_cards=state.away_red_cards,
            updated_at=state.updated_at,
        )
        for state, external_id, home_name, away_name in rows
    )
    return SimulationRunInfo(
        run_id=run.run_id,
        seed=run.seed,
        speed=run.speed,
        status=run.status,
        started_at=run.started_at or _utc_now(),
        matches=match_infos,
    )


def load_simulation_events(
    session: Session, external_match_id: str
) -> tuple[SimulationEventInfo, ...]:
    match = session.scalar(
        select(MatchRecord).where(MatchRecord.external_id == external_match_id)
    )
    if match is None:
        return ()
    seed_rows = session.execute(
        select(MatchEvent, Team.name)
        .outerjoin(Team, Team.id == MatchEvent.team_id)
        .where(MatchEvent.match_id == match.id)
        .order_by(MatchEvent.minute, MatchEvent.id)
    ).all()
    events = [
        SimulationEventInfo(
            event_id=-event.id,
            match_id=external_match_id,
            sequence=-event.id,
            minute=event.minute,
            event_type=event.event_type,
            team=team_name or "—",
            description=f"{event.description} (initial synthetic scenario)",
            occurred_at=match.updated_at,
        )
        for event, team_name in seed_rows
    ]
    run = _latest_run(session)
    if run is None or run.status == "RESET":
        return tuple(sorted(events, key=lambda item: (item.minute, item.sequence)))
    home = aliased(Team, name="event_home")
    away = aliased(Team, name="event_away")
    event_team = aliased(Team, name="event_team")
    rows = session.execute(
        select(SimulationEvent, event_team.name, home.name, away.name)
        .join(MatchRecord, MatchRecord.id == SimulationEvent.match_id)
        .outerjoin(event_team, event_team.id == SimulationEvent.team_id)
        .join(home, home.id == MatchRecord.home_team_id)
        .join(away, away.id == MatchRecord.away_team_id)
        .where(
            SimulationEvent.run_id == run.run_id,
            MatchRecord.external_id == external_match_id,
        )
        .order_by(SimulationEvent.sequence)
    ).all()
    events.extend(
        SimulationEventInfo(
            event_id=event.id,
            match_id=external_match_id,
            sequence=event.sequence,
            minute=event.minute,
            event_type=event.event_type,
            team=team_name or "—",
            description=event.description,
            occurred_at=event.occurred_at,
        )
        for event, team_name, _, _ in rows
    )
    return tuple(sorted(events, key=lambda item: (item.minute, item.sequence)))


def load_current_simulation_overlays(
    session: Session,
) -> tuple[
    dict[int, SimulationSelectionQuote],
    dict[int, SimulationMarketState],
    dict[int, SimulationMatchState],
]:
    run = _latest_run(session)
    if run is None or run.status == "RESET":
        return {}, {}, {}
    quotes = {
        quote.selection_id: quote
        for quote in session.scalars(
            select(SimulationSelectionQuote).where(
                SimulationSelectionQuote.run_id == run.run_id
            )
        )
    }
    market_states = {
        state.market_id: state
        for state in session.scalars(
            select(SimulationMarketState).where(
                SimulationMarketState.run_id == run.run_id
            )
        )
    }
    match_states = {
        state.match_id: state
        for state in session.scalars(
            select(SimulationMatchState).where(
                SimulationMatchState.run_id == run.run_id
            )
        )
    }
    return quotes, market_states, match_states


def load_simulation_market_controls(session: Session) -> dict[int, str]:
    rows = session.execute(
        select(Market, SimulationMarketControl).outerjoin(
            SimulationMarketControl,
            SimulationMarketControl.market_id == Market.id,
        )
    ).all()
    result = {}
    for market, control in rows:
        if market.status == "CLOSED":
            result[market.id] = "CLOSED"
        elif control is not None and control.manually_suspended:
            result[market.id] = "SUSPENDED"
        elif control is not None:
            result[market.id] = "OPEN"
        else:
            result[market.id] = market.status
    return result


def load_simulation_odds_history(
    session: Session, selection_id: int, limit: int
) -> tuple[SimulationOddsHistory, ...]:
    run = _latest_run(session)
    if run is None or run.status == "RESET":
        return ()
    return tuple(
        session.scalars(
            select(SimulationOddsHistory)
            .where(
                SimulationOddsHistory.run_id == run.run_id,
                SimulationOddsHistory.selection_id == selection_id,
            )
            .order_by(
                SimulationOddsHistory.changed_at.desc(),
                SimulationOddsHistory.id.desc(),
            )
            .limit(limit)
        ).all()
    )


def load_latest_simulation_history_by_selection(
    session: Session,
) -> dict[int, SimulationOddsHistory]:
    run = _latest_run(session)
    if run is None or run.status == "RESET":
        return {}
    ranked = (
        select(
            SimulationOddsHistory.id.label("history_id"),
            SimulationOddsHistory.selection_id.label("selection_id"),
            func.row_number()
            .over(
                partition_by=SimulationOddsHistory.selection_id,
                order_by=(
                    SimulationOddsHistory.changed_at.desc(),
                    SimulationOddsHistory.id.desc(),
                ),
            )
            .label("row_number"),
        )
        .where(SimulationOddsHistory.run_id == run.run_id)
        .subquery()
    )
    return {
        history.selection_id: history
        for history in session.scalars(
            select(SimulationOddsHistory)
            .join(ranked, ranked.c.history_id == SimulationOddsHistory.id)
            .where(ranked.c.row_number == 1)
        )
    }


def load_simulation_audit_log(
    session: Session, limit: int = 1000
) -> tuple:
    rows = session.execute(
        select(
            SimulationAuditLog,
            MatchRecord.external_id,
            Market.market_type,
            MarketSelection.selection_name,
        )
        .outerjoin(MatchRecord, MatchRecord.id == SimulationAuditLog.match_id)
        .outerjoin(Market, Market.id == SimulationAuditLog.market_id)
        .outerjoin(
            MarketSelection,
            MarketSelection.id == SimulationAuditLog.selection_id,
        )
        .order_by(
            SimulationAuditLog.created_at.desc(),
            SimulationAuditLog.id.desc(),
        )
        .limit(limit)
    ).all()
    from sportsops.models.simulation import SimulationAuditInfo

    return tuple(
        SimulationAuditInfo(
            audit_id=entry.id,
            match_id=external_id or "SYSTEM",
            market_type=market_type or "SYSTEM",
            selection_name=selection_name or "—",
            event_type=entry.event_type,
            details=_audit_details(entry),
            actor=entry.actor,
            created_at=entry.created_at,
        )
        for entry, external_id, market_type, selection_name in rows
    )


def advance_event_simulation(session: Session, run_id: str) -> int:
    run = _get_run(session, run_id)
    if run.status != "RUNNING":
        return 0
    states = session.scalars(
        select(SimulationMatchState)
        .where(
            SimulationMatchState.run_id == run.run_id,
            SimulationMatchState.status != "FINISHED",
        )
        .order_by(SimulationMatchState.match_id)
    ).all()
    price_changes = 0
    for state in states:
        match = session.get(MatchRecord, state.match_id)
        if match is None:
            raise LookupError(f"Simulation match {state.match_id} was not found.")
        price_changes += _reopen_due_event_markets(session, run, state, match)
        target_minute = min(90, state.minute + run.speed)
        schedule = tuple(
            _event_from_json(item) for item in json.loads(state.event_schedule)
        )
        while state.next_event_index < len(schedule):
            event = schedule[state.next_event_index]
            if event.minute > target_minute:
                break
            price_changes += _process_scheduled_event(
                session,
                run,
                state,
                match,
                event,
                state.next_event_index + 1,
            )
            state.next_event_index += 1
        if state.status != "FINISHED":
            state.minute = target_minute
            state.updated_at = _utc_now()
            if target_minute % 5 == 0:
                price_changes += _reprice_match(
                    session,
                    run,
                    state,
                    match,
                    None,
                    "CLOCK",
                    f"Clock advanced to {target_minute}'; "
                    f"score {state.home_score}-{state.away_score}.",
                )
    remaining = session.scalar(
        select(func.count())
        .select_from(SimulationMatchState)
        .where(
            SimulationMatchState.run_id == run.run_id,
            SimulationMatchState.status != "FINISHED",
        )
    )
    if remaining == 0:
        run.status = "COMPLETED"
        run.completed_at = _utc_now()
        _record_audit(
            session,
            "SIMULATION_COMPLETED",
            "All simulated matches reached full time; their markets were closed.",
            run_id=run.run_id,
            new_value="COMPLETED",
        )
    return price_changes


def transition_simulation_market(
    session: Session,
    market_id: int,
    target_status: str,
    reason: str,
    actor: str = "Operator",
) -> bool:
    market = session.get(Market, market_id)
    if market is None:
        raise LookupError(f"Market {market_id} was not found.")
    if not market.is_simulated:
        raise ValueError("Only simulated markets can be changed in the simulation.")
    cleaned_reason = reason.strip()
    if target_status == "SUSPENDED" and not cleaned_reason:
        raise ValueError("A reason is required to suspend a market.")
    if target_status not in ("OPEN", "SUSPENDED"):
        raise ValueError(f"Unsupported market status {target_status}.")
    if market.status == "CLOSED":
        raise ValueError("Closed markets cannot be reopened or suspended.")

    control = session.get(SimulationMarketControl, market_id)
    if control is None:
        control = SimulationMarketControl(
            market_id=market_id,
            manually_suspended=market.status == "SUSPENDED",
        )
        session.add(control)
        session.flush()
    was_suspended = control.manually_suspended

    run = _latest_run(session)
    market_state = None
    match_state = None
    match = session.get(MatchRecord, market.match_id)
    if run and run.status != "RESET":
        market_state = session.scalar(
            select(SimulationMarketState).where(
                SimulationMarketState.run_id == run.run_id,
                SimulationMarketState.market_id == market_id,
            )
        )
        match_state = session.scalar(
            select(SimulationMatchState).where(
                SimulationMatchState.run_id == run.run_id,
                SimulationMatchState.match_id == market.match_id,
            )
        )
        if market_state and market_state.status == "CLOSED":
            raise ValueError("Full-time markets cannot be changed.")
    if target_status == "SUSPENDED" and was_suspended:
        return False
    if target_status == "OPEN" and not was_suspended:
        if not (
            market_state is not None
            and market_state.status == "SUSPENDED"
            and market_state.suspension_kind == "EVENT"
        ):
            return False

    if target_status == "OPEN" and run and match_state and market_state:
        if match is None:
            raise LookupError(f"Match {market.match_id} was not found.")
        _reprice_match(
            session,
            run,
            match_state,
            match,
            None,
            "MANUAL_REOPEN",
            "Market repriced from the current simulated match state before reopening.",
            only_market_id=market_id,
            allow_manual_suspension=True,
        )

    control.manually_suspended = target_status == "SUSPENDED"
    control.suspension_reason = cleaned_reason if target_status == "SUSPENDED" else None
    control.updated_at = _utc_now()
    if market_state is not None:
        if target_status == "SUSPENDED":
            market_state.status = "SUSPENDED"
            market_state.suspension_kind = "MANUAL"
            market_state.suspension_reason = cleaned_reason
            market_state.reopen_after_minute = None
        else:
            market_state.status = "OPEN"
            market_state.suspension_kind = "NONE"
            market_state.suspension_reason = None
            market_state.reopen_after_minute = None
    _record_audit(
        session,
        "MARKET_MANUALLY_SUSPENDED"
        if target_status == "SUSPENDED"
        else "MARKET_MANUALLY_REOPENED",
        cleaned_reason
        if cleaned_reason
        else (
            "Operator reopened after repricing from the current match state."
            if target_status == "OPEN"
            else "Operator suspended the market."
        ),
        run_id=run.run_id if run and run.status != "RESET" else None,
        match_id=market.match_id,
        market_id=market_id,
        old_value="SUSPENDED" if was_suspended else "OPEN",
        new_value=target_status,
        actor=actor,
    )
    return True


def load_trading_risk_incidents(
    session: Session,
    scope: str = "current",
) -> tuple:
    if scope not in ("current", "all", "historical"):
        raise ValueError(f"Unsupported trading-risk scope: {scope}.")
    current_run = _latest_run(session)
    statement = (
        select(
            TradingRiskIncident,
            MatchRecord.external_id,
            Market.market_type,
            MarketSelection.selection_name,
            SimulationRun.status,
        )
        .join(MatchRecord, MatchRecord.id == TradingRiskIncident.match_id)
        .join(Market, Market.id == TradingRiskIncident.market_id)
        .join(MarketSelection, MarketSelection.id == TradingRiskIncident.selection_id)
        .outerjoin(SimulationRun, SimulationRun.run_id == TradingRiskIncident.run_id)
    )
    if scope == "current":
        if current_run is None:
            statement = statement.where(false())
        else:
            statement = statement.where(
                TradingRiskIncident.run_id == current_run.run_id
            )
    elif scope == "historical" and current_run is not None:
        statement = statement.where(
            or_(
                TradingRiskIncident.run_id.is_(None),
                TradingRiskIncident.run_id != current_run.run_id,
            )
        )
    rows = session.execute(
        statement.order_by(
            TradingRiskIncident.status == "ACKNOWLEDGED",
            TradingRiskIncident.highest_severity == "WARNING",
            TradingRiskIncident.last_seen_at.desc(),
            TradingRiskIncident.id.desc(),
        )
    ).all()
    from sportsops.models.simulation import TradingRiskIncidentInfo

    return tuple(
        TradingRiskIncidentInfo(
            incident_id=incident.id,
            match_id=external_id,
            market_type=market_type,
            selection_name=selection_name,
            rule_code=incident.rule_code,
            severity=incident.highest_severity,
            description=incident.description,
            occurrence_count=incident.occurrence_count,
            first_seen_at=incident.first_seen_at,
            last_seen_at=incident.last_seen_at,
            status=incident.status,
            acknowledged_at=incident.acknowledged_at,
            acknowledgement_note=incident.acknowledgement_note,
            run_id=incident.run_id,
            run_status=run_status,
        )
        for incident, external_id, market_type, selection_name, run_status in rows
    )


def acknowledge_trading_risk_incident(
    session: Session, incident_id: int, note: str | None = None
) -> bool:
    incident = session.get(TradingRiskIncident, incident_id)
    if incident is None:
        raise LookupError(f"Trading risk incident {incident_id} was not found.")
    if incident.status != "OPEN":
        return False
    cleaned_note = note.strip() if note and note.strip() else None
    now = _utc_now()
    incident.status = "ACKNOWLEDGED"
    incident.acknowledged_at = now
    incident.acknowledgement_note = cleaned_note
    incident.cooldown_until = now + _COOLDOWN
    _record_audit(
        session,
        "RISK_INCIDENT_ACKNOWLEDGED",
        cleaned_note or "Trading risk incident acknowledged.",
        match_id=incident.match_id,
        market_id=incident.market_id,
        new_value=incident.highest_severity,
        actor="Operator",
    )
    for occurrence in session.scalars(
        select(TradingRiskOccurrence).where(
            TradingRiskOccurrence.incident_id == incident.id,
            TradingRiskOccurrence.legacy_alert_id.is_not(None),
        )
    ):
        legacy_alert = session.get(TradingRiskAlert, occurrence.legacy_alert_id)
        if legacy_alert is None or legacy_alert.acknowledged_at is not None:
            continue
        legacy_alert.acknowledged_at = now
        legacy_alert.acknowledgement_note = cleaned_note
        history = session.get(OddsHistory, legacy_alert.odds_history_id)
        selection = session.get(MarketSelection, history.selection_id)
        session.add(
            # The legacy audit table remains append-only; each original alert
            # acknowledgement is recorded there without changing its occurrence.
            _legacy_ack_audit(legacy_alert, history, selection, cleaned_note)
        )
    session.flush()
    return True


def load_risk_incident_occurrences(
    session: Session, incident_id: int
):
    rows = session.execute(
        select(TradingRiskOccurrence)
        .where(TradingRiskOccurrence.incident_id == incident_id)
        .order_by(TradingRiskOccurrence.occurred_at, TradingRiskOccurrence.id)
    ).scalars().all()
    from sportsops.models.simulation import RiskOccurrenceInfo

    result = []
    for occurrence in rows:
        previous = current = percentage = None
        if occurrence.simulation_odds_history_id is not None:
            history = session.get(
                SimulationOddsHistory, occurrence.simulation_odds_history_id
            )
            previous, current, percentage = (
                history.previous_odds,
                history.current_odds,
                history.percentage_change,
            )
        elif occurrence.legacy_alert_id is not None:
            alert = session.get(TradingRiskAlert, occurrence.legacy_alert_id)
            history = session.get(OddsHistory, alert.odds_history_id)
            previous, current = history.previous_odds, history.current_odds
            percentage = (
                (current - previous) / previous * Decimal("100")
            ).quantize(_PERCENT_QUANTUM, rounding=ROUND_HALF_UP)
        result.append(
            RiskOccurrenceInfo(
                severity=occurrence.severity,
                description=occurrence.description,
                occurred_at=occurrence.occurred_at,
                previous_odds=previous,
                current_odds=current,
                percentage_change=percentage,
            )
        )
    return tuple(result)


def _process_scheduled_event(
    session: Session,
    run: SimulationRun,
    state: SimulationMatchState,
    match: MatchRecord,
    event: ScheduledFootballEvent,
    sequence: int,
) -> int:
    if event.event_type in ("GOAL", "RED_CARD", "HALF_TIME"):
        _suspend_event_markets(
            session,
            run.run_id,
            match.id,
            event.minute,
            event.description,
        )
    team_id = (
        match.home_team_id
        if event.team_side == "HOME"
        else match.away_team_id
        if event.team_side == "AWAY"
        else None
    )
    stored_event = SimulationEvent(
        run_id=run.run_id,
        match_id=match.id,
        event_key=event.event_key,
        sequence=sequence,
        minute=event.minute,
        event_type=event.event_type,
        team_id=team_id,
        description=event.description,
        occurred_at=_utc_now(),
    )
    session.add(stored_event)
    session.flush()
    state.minute = event.minute
    if event.event_type == "KICK_OFF":
        state.status = "LIVE"
    elif event.event_type == "GOAL":
        if event.team_side == "HOME":
            state.home_score += 1
        elif event.team_side == "AWAY":
            state.away_score += 1
    elif event.event_type == "RED_CARD":
        if event.team_side == "HOME":
            state.home_red_cards += 1
        elif event.team_side == "AWAY":
            state.away_red_cards += 1
    elif event.event_type == "HALF_TIME":
        state.status = "HALF_TIME"
    elif event.event_type == "SECOND_HALF":
        state.status = "LIVE"
    elif event.event_type == "FULL_TIME":
        state.status = "FINISHED"
    state.updated_at = _utc_now()
    _record_audit(
        session,
        "MATCH_EVENT",
        event.description,
        run_id=run.run_id,
        match_id=match.id,
        event_id=stored_event.id,
        old_value=f"{state.home_score}-{state.away_score}",
        new_value=f"{state.status} {state.minute}'",
        idempotency_key=f"{run.run_id}:match-event:{event.event_key}",
    )
    changed = 0
    if event.event_type in (
        "KICK_OFF",
        "GOAL",
        "RED_CARD",
        "HALF_TIME",
        "SECOND_HALF",
        "FULL_TIME",
    ):
        changed += _reprice_match(
            session,
            run,
            state,
            match,
            stored_event,
            event.event_type,
            f"{event.description}; score {state.home_score}-{state.away_score}; "
            f"red cards {state.home_red_cards}-{state.away_red_cards}.",
        )
    if event.event_type == "FULL_TIME":
        _close_match_markets(session, run.run_id, match.id, stored_event)
    return changed


def _suspend_event_markets(
    session: Session,
    run_id: str,
    match_id: int,
    minute: int,
    reason: str,
) -> None:
    market_rows = session.execute(
        select(SimulationMarketState, Market)
        .join(Market, Market.id == SimulationMarketState.market_id)
        .where(
            SimulationMarketState.run_id == run_id,
            Market.match_id == match_id,
            SimulationMarketState.status != "CLOSED",
        )
        .order_by(Market.id)
    ).all()
    for market_state, market in market_rows:
        if market_state.suspension_kind == "MANUAL":
            continue
        old_status = market_state.status
        market_state.status = "SUSPENDED"
        market_state.suspension_kind = "EVENT"
        market_state.suspension_reason = reason
        market_state.reopen_after_minute = min(90, minute + 1)
        if old_status != "SUSPENDED":
            _record_audit(
                session,
                "MARKET_EVENT_SUSPENDED",
                reason,
                run_id=run_id,
                match_id=match_id,
                market_id=market.id,
                old_value=old_status,
                new_value="SUSPENDED",
                idempotency_key=f"{run_id}:event-suspend:{market.id}:{minute}",
            )


def _reopen_due_event_markets(
    session: Session,
    run: SimulationRun,
    match_state: SimulationMatchState,
    match: MatchRecord,
) -> int:
    candidates = session.execute(
        select(SimulationMarketState, Market)
        .join(Market, Market.id == SimulationMarketState.market_id)
        .where(
            SimulationMarketState.run_id == run.run_id,
            Market.match_id == match.id,
            SimulationMarketState.status == "SUSPENDED",
            SimulationMarketState.suspension_kind == "EVENT",
            SimulationMarketState.reopen_after_minute <= match_state.minute,
        )
        .order_by(Market.id)
    ).all()
    changed = 0
    for market_state, market in candidates:
        control = session.get(SimulationMarketControl, market.id)
        if control and control.manually_suspended:
            market_state.suspension_kind = "MANUAL"
            market_state.suspension_reason = control.suspension_reason
            market_state.reopen_after_minute = None
            continue
        changed += _reprice_match(
            session,
            run,
            match_state,
            match,
            None,
            "EVENT_REOPEN",
            f"Market repriced from the current match state before reopening "
            f"at {match_state.minute}'.",
            only_market_id=market.id,
        )
        old_reason = market_state.suspension_reason or "Event suspension"
        market_state.status = "OPEN"
        market_state.suspension_kind = "NONE"
        market_state.suspension_reason = None
        market_state.reopen_after_minute = None
        _record_audit(
            session,
            "MARKET_EVENT_REOPENED",
            f"Repriced before reopening after: {old_reason}",
            run_id=run.run_id,
            match_id=match.id,
            market_id=market.id,
            old_value="SUSPENDED",
            new_value="OPEN",
        )
    return changed


def _close_match_markets(
    session: Session,
    run_id: str,
    match_id: int,
    event: SimulationEvent,
) -> None:
    rows = session.execute(
        select(SimulationMarketState, Market)
        .join(Market, Market.id == SimulationMarketState.market_id)
        .where(
            SimulationMarketState.run_id == run_id,
            Market.match_id == match_id,
            SimulationMarketState.status != "CLOSED",
        )
        .order_by(Market.id)
    ).all()
    for market_state, market in rows:
        old_status = market_state.status
        market_state.status = "CLOSED"
        market_state.suspension_kind = "CLOSED"
        market_state.suspension_reason = "Closed at simulated full time."
        market_state.reopen_after_minute = None
        _record_audit(
            session,
            "MARKET_CLOSED_FULL_TIME",
            "Market closed at full time; no later odds updates are permitted.",
            run_id=run_id,
            match_id=match_id,
            market_id=market.id,
            event_id=event.id,
            old_value=old_status,
            new_value="CLOSED",
            idempotency_key=f"{run_id}:full-time-close:{market.id}",
        )


def _reprice_match(
    session: Session,
    run: SimulationRun,
    state: SimulationMatchState,
    match: MatchRecord,
    event: SimulationEvent | None,
    cause_type: str,
    reason: str,
    *,
    detect_risk: bool = True,
    only_market_id: int | None = None,
    allow_manual_suspension: bool = False,
) -> int:
    fair_probabilities = calculate_fair_probabilities(
        minute=state.minute,
        home_score=state.home_score,
        away_score=state.away_score,
        home_red_cards=state.home_red_cards,
        away_red_cards=state.away_red_cards,
        home_goal_rate=state.home_goal_rate,
        away_goal_rate=state.away_goal_rate,
    )
    decimal_odds = calculate_event_driven_odds(fair_probabilities)
    rows = session.execute(
        select(SimulationMarketState, Market)
        .join(Market, Market.id == SimulationMarketState.market_id)
        .where(
            SimulationMarketState.run_id == run.run_id,
            Market.match_id == match.id,
            Market.is_simulated.is_(True),
            SimulationMarketState.status != "CLOSED",
            or_(
                SimulationMarketState.status == "OPEN",
                and_(
                    SimulationMarketState.status == "SUSPENDED",
                    SimulationMarketState.suspension_kind == "EVENT",
                ),
                *(
                    (
                        and_(
                            SimulationMarketState.status == "SUSPENDED",
                            SimulationMarketState.suspension_kind == "MANUAL",
                        ),
                    )
                    if allow_manual_suspension
                    else ()
                ),
            ),
        )
        .order_by(Market.id)
    ).all()
    if only_market_id is not None:
        rows = [row for row in rows if row[1].id == only_market_id]
    changed = 0
    for market_state, market in rows:
        if market_state.status == "SUSPENDED":
            if market_state.suspension_kind == "MANUAL" and not allow_manual_suspension:
                continue
            if market_state.suspension_kind not in ("EVENT", "MANUAL"):
                continue
        selections = session.scalars(
            select(MarketSelection)
            .where(MarketSelection.market_id == market.id)
            .order_by(MarketSelection.id)
        ).all()
        for selection in selections:
            key = (market.market_type, selection.selection_key)
            if key not in decimal_odds or key not in fair_probabilities:
                raise ValueError(
                    f"Missing model probability for {market.market_type}/{selection.selection_key}."
                )
            new_odds = decimal_odds[key]
            fair_probability = fair_probabilities[key]
            _validate_simulation_quote(new_odds, fair_probability, selection.id)
            quote = session.get(
                SimulationSelectionQuote, (run.run_id, selection.id)
            )
            previous_odds = (
                quote.decimal_odds if quote is not None else selection.current_odds
            )
            history = None
            if new_odds != previous_odds:
                changed_at = _utc_now()
                precise_change = (
                    (new_odds - previous_odds) / previous_odds * Decimal("100")
                )
                idempotency_key = (
                    f"{run.run_id}:{cause_type}:{event.id if event else state.minute}:"
                    f"{market.id}:{selection.id}"
                )
                history = SimulationOddsHistory(
                    run_id=run.run_id,
                    market_id=market.id,
                    selection_id=selection.id,
                    event_id=event.id if event else None,
                    previous_odds=previous_odds,
                    current_odds=new_odds,
                    fair_probability=fair_probability,
                    percentage_change=precise_change.quantize(
                        _PERCENT_QUANTUM, rounding=ROUND_HALF_UP
                    ),
                    cause_type=cause_type,
                    reason=reason,
                    changed_at=changed_at,
                    idempotency_key=idempotency_key,
                )
                session.add(history)
                session.flush()
                changed += 1
                _record_audit(
                    session,
                    "ODDS_REPRICED",
                    reason,
                    run_id=run.run_id,
                    match_id=match.id,
                    market_id=market.id,
                    selection_id=selection.id,
                    event_id=event.id if event else None,
                    old_value=f"{previous_odds:.3f}",
                    new_value=f"{new_odds:.3f}",
                    idempotency_key=f"{idempotency_key}:audit",
                )
                if detect_risk:
                    severity = _risk_severity(
                        precise_change,
                        run.warning_percent,
                        run.critical_percent,
                        cause_type,
                    )
                    if severity:
                        _record_risk_occurrence(
                            session,
                            run,
                            match.id,
                            market,
                            selection,
                            history,
                            severity,
                            precise_change,
                        )
            if quote is None:
                quote = SimulationSelectionQuote(
                    run_id=run.run_id,
                    selection_id=selection.id,
                    market_id=market.id,
                    decimal_odds=new_odds,
                    fair_probability=fair_probability,
                    reason=reason,
                    event_id=event.id if event else None,
                    updated_at=_utc_now(),
                )
                session.add(quote)
            else:
                quote.decimal_odds = new_odds
                quote.fair_probability = fair_probability
                quote.reason = reason
                quote.event_id = event.id if event else None
                quote.updated_at = _utc_now()
    session.flush()
    return changed


def _record_risk_occurrence(
    session: Session,
    run: SimulationRun,
    match_id: int,
    market: Market,
    selection: MarketSelection,
    history: SimulationOddsHistory,
    severity: str,
    movement: Decimal,
) -> None:
    rule_code = "UNEXPECTED_ODDS_MOVEMENT"
    now = _aware(history.changed_at)
    description = (
        f"Simulated odds moved {movement:+.2f}% from "
        f"{history.previous_odds:.3f} to {history.current_odds:.3f}; "
        f"cause: {history.cause_type}."
    )
    incident = session.scalar(
        select(TradingRiskIncident).where(
            TradingRiskIncident.run_id == run.run_id,
            TradingRiskIncident.match_id == match_id,
            TradingRiskIncident.market_id == market.id,
            TradingRiskIncident.selection_id == selection.id,
            TradingRiskIncident.rule_code == rule_code,
            TradingRiskIncident.status == "OPEN",
        )
    )
    if incident is None:
        recent_acknowledged = session.scalar(
            select(TradingRiskIncident)
            .where(
                TradingRiskIncident.run_id == run.run_id,
                TradingRiskIncident.match_id == match_id,
                TradingRiskIncident.market_id == market.id,
                TradingRiskIncident.selection_id == selection.id,
                TradingRiskIncident.rule_code == rule_code,
                TradingRiskIncident.status == "ACKNOWLEDGED",
                TradingRiskIncident.cooldown_until > now,
            )
            .order_by(TradingRiskIncident.acknowledged_at.desc())
            .limit(1)
        )
        incident = recent_acknowledged
    if incident is None:
        incident = TradingRiskIncident(
            run_id=run.run_id,
            match_id=match_id,
            market_id=market.id,
            selection_id=selection.id,
            rule_code=rule_code,
            highest_severity=severity,
            description=description,
            occurrence_count=1,
            first_seen_at=now,
            last_seen_at=now,
            status="OPEN",
        )
        session.add(incident)
        session.flush()
    else:
        incident.occurrence_count += 1
        incident.first_seen_at = min(_aware(incident.first_seen_at), now)
        incident.last_seen_at = max(_aware(incident.last_seen_at), now)
        incident.description = description
        if severity == "ERROR":
            incident.highest_severity = "ERROR"
    session.add(
        TradingRiskOccurrence(
            incident_id=incident.id,
            simulation_odds_history_id=history.id,
            severity=severity,
            description=description,
            occurred_at=now,
        )
    )


def _backfill_legacy_risk_incidents(session: Session) -> None:
    home = aliased(Team, name="risk_home")
    rows = session.execute(
        select(
            TradingRiskAlert,
            OddsHistory,
            MarketSelection,
            Market,
            MatchRecord,
        )
        .join(OddsHistory, OddsHistory.id == TradingRiskAlert.odds_history_id)
        .join(MarketSelection, MarketSelection.id == OddsHistory.selection_id)
        .join(Market, Market.id == MarketSelection.market_id)
        .join(MatchRecord, MatchRecord.id == Market.match_id)
        .order_by(TradingRiskAlert.detected_at, TradingRiskAlert.id)
    ).all()
    grouped = {}
    for alert, history, selection, market, match in rows:
        grouped.setdefault(
            (match.id, market.id, selection.id), []
        ).append((alert, history, selection, market, match))
    for (match_id, market_id, selection_id), records in grouped.items():
        incident = session.scalar(
            select(TradingRiskIncident).where(
                TradingRiskIncident.match_id == match_id,
                TradingRiskIncident.market_id == market_id,
                TradingRiskIncident.selection_id == selection_id,
                TradingRiskIncident.rule_code == "LEGACY_ODDS_MOVEMENT",
            )
        )
        if incident is None:
            severities = [row[0].severity for row in records]
            times = [_aware(row[0].detected_at) for row in records]
            open_records = [row[0] for row in records if row[0].acknowledged_at is None]
            incident = TradingRiskIncident(
                match_id=match_id,
                market_id=market_id,
                selection_id=selection_id,
                rule_code="LEGACY_ODDS_MOVEMENT",
                highest_severity="ERROR" if "ERROR" in severities else "WARNING",
                description=f"{len(records)} preserved legacy odds-movement alert(s).",
                occurrence_count=len(records),
                first_seen_at=min(times),
                last_seen_at=max(times),
                status="OPEN" if open_records else "ACKNOWLEDGED",
                acknowledged_at=(
                    None
                    if open_records
                    else max(_aware(row[0].acknowledged_at) for row in records)
                ),
                acknowledgement_note=next(
                    (
                        row[0].acknowledgement_note
                        for row in reversed(records)
                        if row[0].acknowledgement_note
                    ),
                    None,
                ),
            )
            session.add(incident)
            session.flush()
        linked_alert_ids = set(
            session.scalars(
                select(TradingRiskOccurrence.legacy_alert_id).where(
                    TradingRiskOccurrence.legacy_alert_id.is_not(None),
                    TradingRiskOccurrence.incident_id == incident.id,
                )
            )
        )
        for alert, _, _, _, _ in records:
            if alert.id in linked_alert_ids:
                continue
            session.add(
                TradingRiskOccurrence(
                    incident_id=incident.id,
                    legacy_alert_id=alert.id,
                    severity=alert.severity,
                    description=alert.description,
                    occurred_at=_aware(alert.detected_at),
                )
            )
    session.flush()


def _event_to_json(event: ScheduledFootballEvent) -> dict[str, object]:
    return {
        "event_key": event.event_key,
        "minute": event.minute,
        "event_type": event.event_type,
        "team_side": event.team_side,
        "description": event.description,
    }


def _event_from_json(value: dict[str, object]) -> ScheduledFootballEvent:
    return ScheduledFootballEvent(
        event_key=str(value["event_key"]),
        minute=int(value["minute"]),
        event_type=str(value["event_type"]),
        team_side=(str(value["team_side"]) if value["team_side"] is not None else None),
        description=str(value["description"]),
    )


def _legacy_ack_audit(
    alert: TradingRiskAlert,
    history: OddsHistory,
    selection: MarketSelection,
    note: str | None,
) -> TradingAuditLog:
    return TradingAuditLog(
        market_id=selection.market_id,
        selection_id=selection.id,
        odds_history_id=history.id,
        event_type="RISK_ALERT_ACKNOWLEDGED",
        new_value=alert.severity,
        reason=note or "Trading risk incident acknowledged.",
        actor="Operator",
        is_simulated=alert.is_simulated,
    )


def _risk_severity(
    movement: Decimal,
    warning_percent: Decimal,
    critical_percent: Decimal,
    cause_type: str,
) -> str | None:
    if cause_type in _EXPECTED_REPRICE_CAUSES:
        return None
    magnitude = abs(movement)
    if magnitude >= critical_percent:
        return "ERROR"
    if magnitude >= warning_percent:
        return "WARNING"
    return None


def _validate_simulation_quote(
    odds: Decimal, fair_probability: Decimal, selection_id: int
) -> None:
    if not MINIMUM_ODDS <= odds <= MAXIMUM_ODDS:
        raise ValueError(
            f"Simulated quote for selection {selection_id} is outside "
            "the configured odds limits."
        )
    if not Decimal("0") <= fair_probability <= Decimal("1"):
        raise ValueError(
            f"Simulated quote for selection {selection_id} has an invalid "
            "fair probability."
        )


def _record_audit(
    session: Session,
    event_type: str,
    reason: str,
    *,
    run_id: str | None = None,
    match_id: int | None = None,
    market_id: int | None = None,
    selection_id: int | None = None,
    event_id: int | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
    actor: str = "Simulation",
    idempotency_key: str | None = None,
) -> None:
    session.add(
        SimulationAuditLog(
            run_id=run_id,
            match_id=match_id,
            market_id=market_id,
            selection_id=selection_id,
            event_id=event_id,
            event_type=event_type,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
            actor=actor,
            created_at=_utc_now(),
            idempotency_key=idempotency_key or uuid4().hex,
        )
    )


def _latest_run(session: Session) -> SimulationRun | None:
    return session.scalar(
        select(SimulationRun)
        .order_by(SimulationRun.started_at.desc(), SimulationRun.run_id.desc())
        .limit(1)
    )


def _audit_details(audit: SimulationAuditLog) -> str:
    old_value = f"{audit.old_value} -> " if audit.old_value else ""
    new_value = audit.new_value or ""
    return f"{old_value}{new_value} {audit.reason}".strip()


def _get_run(session: Session, run_id: str) -> SimulationRun:
    run = session.get(SimulationRun, run_id)
    if run is None:
        raise LookupError(f"Simulation run {run_id} was not found.")
    return run


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
