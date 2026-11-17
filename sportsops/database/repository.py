from datetime import datetime, timezone

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, aliased

from sportsops.database.models import (
    Competition,
    DataQualityAlert,
    MatchEvent,
    MatchRecord,
    MatchStatistics,
    Season,
    Team,
)
from sportsops.models.match import AlertInfo, Match, MatchDetails, MatchEventInfo


def _match_query(external_id: str | None = None):
    home_team = aliased(Team, name="home_team")
    away_team = aliased(Team, name="away_team")
    last_event = (
        select(MatchEvent.description)
        .where(MatchEvent.match_id == MatchRecord.id)
        .order_by(MatchEvent.minute.desc(), MatchEvent.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    alert_quality = (
        select(
            DataQualityAlert.match_id,
            func.max(
                case(
                    (DataQualityAlert.severity == "ERROR", 2),
                    (DataQualityAlert.severity == "WARNING", 1),
                    else_=0,
                )
            ).label("quality_level"),
        )
        .where(DataQualityAlert.resolved_at.is_(None))
        .group_by(DataQualityAlert.match_id)
        .subquery()
    )
    query = (
        select(
            MatchRecord,
            Competition.name,
            Season.name,
            Competition.country,
            home_team.name,
            home_team.country,
            away_team.name,
            away_team.country,
            MatchStatistics.home_possession,
            MatchStatistics.away_possession,
            MatchStatistics.home_shots,
            MatchStatistics.away_shots,
            MatchStatistics.home_shots_on_target,
            MatchStatistics.away_shots_on_target,
            last_event,
            func.coalesce(alert_quality.c.quality_level, 0),
            MatchRecord.data_origin,
            MatchRecord.source_name,
            MatchRecord.source_reference,
        )
        .join(Season, Season.id == MatchRecord.season_id)
        .join(Competition, Competition.id == Season.competition_id)
        .join(home_team, home_team.id == MatchRecord.home_team_id)
        .join(away_team, away_team.id == MatchRecord.away_team_id)
        .outerjoin(MatchStatistics, MatchStatistics.match_id == MatchRecord.id)
        .outerjoin(alert_quality, alert_quality.c.match_id == MatchRecord.id)
        .order_by(MatchRecord.external_id, MatchRecord.id)
    )
    if external_id is not None:
        query = query.where(MatchRecord.external_id == external_id)
    return query


def _to_match(row) -> Match:
    record = row[0]
    return Match(
        match_id=record.external_id,
        competition=row[1],
        season=row[2],
        competition_country=row[3],
        home_team=row[4],
        home_team_country=row[5],
        away_team=row[6],
        away_team_country=row[7],
        home_score=record.home_score,
        away_score=record.away_score,
        elapsed_minute=record.elapsed_minute,
        status=record.status,
        possession=(
            f"{row[8]:.0f}% - {row[9]:.0f}%"
            if row[8] is not None and row[9] is not None
            else "—"
        ),
        shots=f"{row[10]} - {row[11]}" if row[10] is not None else "—",
        shots_on_target=(
            f"{row[12]} - {row[13]}" if row[12] is not None else "—"
        ),
        last_event=row[14] or "No events recorded",
        simulated=record.is_simulated,
        last_update=record.updated_at,
        data_quality={2: "ERROR", 1: "WARNING"}.get(row[15], "CLEAR"),
        kickoff_at=record.kickoff_at,
        data_origin=row[16],
        source_name=row[17],
        source_reference=row[18],
    )


def load_matches(session: Session) -> tuple[Match, ...]:
    return tuple(_to_match(row) for row in session.execute(_match_query()).all())


def load_match_details(session: Session, external_id: str) -> MatchDetails:
    row = session.execute(_match_query(external_id)).one_or_none()
    if row is None:
        raise LookupError(f"Match {external_id} was not found.")
    match = _to_match(row)
    events = session.execute(
        select(
            MatchEvent.minute,
            MatchEvent.event_type,
            Team.name,
            MatchEvent.description,
            MatchEvent.is_simulated,
        )
        .outerjoin(Team, Team.id == MatchEvent.team_id)
        .join(MatchRecord, MatchRecord.id == MatchEvent.match_id)
        .where(MatchRecord.external_id == external_id)
        .order_by(MatchEvent.minute, MatchEvent.id)
    ).all()
    return MatchDetails(
        match=match,
        events=tuple(
            MatchEventInfo(
                minute=event.minute,
                event_type=event.event_type,
                team=event.name or "—",
                description=event.description,
                simulated=event.is_simulated,
            )
            for event in events
        ),
    )


def load_alerts(session: Session) -> tuple[AlertInfo, ...]:
    rows = session.execute(
        select(
            DataQualityAlert,
            MatchRecord.external_id,
        )
        .outerjoin(MatchRecord, MatchRecord.id == DataQualityAlert.match_id)
        .order_by(
            DataQualityAlert.resolved_at.is_not(None),
            case(
                (DataQualityAlert.severity == "ERROR", 1),
                (DataQualityAlert.severity == "WARNING", 2),
                else_=3,
            ),
            DataQualityAlert.detected_at.desc(),
            DataQualityAlert.id,
        )
    ).all()
    return tuple(
        AlertInfo(
            alert_id=row[0].id,
            match_id=row[1] or "SYSTEM",
            severity=row[0].severity,
            description=row[0].message,
            detected_at=row[0].detected_at,
            acknowledged_at=row[0].acknowledged_at,
            resolved_at=row[0].resolved_at,
        )
        for row in rows
    )


def acknowledge_alert(session: Session, alert_id: int) -> bool:
    alert = session.get(DataQualityAlert, alert_id)
    if alert is None:
        raise LookupError(f"Data quality alert {alert_id} was not found.")
    if alert.acknowledged_at is not None:
        return False
    alert.acknowledged_at = datetime.now(timezone.utc)
    session.flush()
    return True
