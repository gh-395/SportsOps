import logging
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from sportsops.data.seed_data import MatchFixture
from sportsops.database.models import (
    DataQualityAlert,
    MatchEvent,
    MatchRecord,
    MatchStatistics,
    Team,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class QualityIssue:
    rule_code: str
    severity: str
    message: str
    external_id: str | None = None


def validate_fixture(fixture: MatchFixture) -> tuple[QualityIssue, ...]:
    issues: list[QualityIssue] = []
    if not fixture.external_id or not fixture.external_id.strip():
        issues.append(QualityIssue("MISSING_MATCH_ID", "ERROR", "Match ID is required."))
    required = (
        ("competition", fixture.competition),
        ("season", fixture.season),
        ("home team", fixture.home_team),
        ("away team", fixture.away_team),
        ("status", fixture.status),
    )
    for label, value in required:
        if not value or not value.strip():
            issues.append(
                QualityIssue("MISSING_MATCH_DATA", "ERROR", f"Missing {label}.", fixture.external_id)
            )

    if fixture.home_team and fixture.home_team == fixture.away_team:
        issues.append(
            QualityIssue("INCONSISTENT_TEAMS", "ERROR", "Home and away teams must differ.", fixture.external_id)
        )
    if fixture.status not in {"SCHEDULED", "LIVE", "HALF_TIME", "FINISHED"}:
        issues.append(
            QualityIssue("INVALID_STATUS", "ERROR", f"Unknown match status: {fixture.status}.", fixture.external_id)
        )
    if (fixture.home_score is None) != (fixture.away_score is None):
        issues.append(
            QualityIssue("INVALID_SCORE", "ERROR", "Both scores must be present together.", fixture.external_id)
        )
    if any(score is not None and score < 0 for score in (fixture.home_score, fixture.away_score)):
        issues.append(
            QualityIssue("INVALID_SCORE", "ERROR", "Scores cannot be negative.", fixture.external_id)
        )
    if fixture.status != "SCHEDULED" and fixture.home_score is None:
        issues.append(
            QualityIssue("MISSING_SCORE", "ERROR", "A non-scheduled match needs both scores.", fixture.external_id)
        )
    if fixture.status == "SCHEDULED" and fixture.home_score is not None:
        issues.append(
            QualityIssue("INCONSISTENT_STATUS_SCORE", "WARNING", "A scheduled match already has a score.", fixture.external_id)
        )
    if fixture.elapsed_minute is not None and not 0 <= fixture.elapsed_minute <= 150:
        issues.append(
            QualityIssue("INVALID_MATCH_MINUTE", "ERROR", "Elapsed minute must be between 0 and 150.", fixture.external_id)
        )
    if fixture.home_possession is None or fixture.away_possession is None:
        issues.append(
            QualityIssue("MISSING_STATISTICS", "WARNING", "Possession statistics are missing.", fixture.external_id)
        )
    elif (
        fixture.home_possession < 0
        or fixture.away_possession < 0
        or fixture.home_possession > 100
        or fixture.away_possession > 100
    ):
        issues.append(
            QualityIssue("INVALID_STATISTICS", "ERROR", "Possession values must be between 0 and 100.", fixture.external_id)
        )
    elif abs(fixture.home_possession + fixture.away_possession - Decimal("100")) > Decimal("0.10"):
        issues.append(
            QualityIssue("INCONSISTENT_POSSESSION", "WARNING", "Home and away possession should total 100%.", fixture.external_id)
        )
    if any(
        shots < 0 or on_target < 0 or on_target > shots
        for shots, on_target in (
            (fixture.home_shots, fixture.home_shots_on_target),
            (fixture.away_shots, fixture.away_shots_on_target),
        )
    ):
        issues.append(
            QualityIssue("INVALID_STATISTICS", "ERROR", "Shot counts must be nonnegative and on-target shots cannot exceed total shots.", fixture.external_id)
        )

    if not fixture.events and fixture.status != "SCHEDULED":
        issues.append(
            QualityIssue("MISSING_EVENTS", "WARNING", "Match has no recorded events.", fixture.external_id)
        )

    event_keys: set[str] = set()
    event_fingerprints: set[tuple[int, str, str | None, str]] = set()
    for event in fixture.events:
        fingerprint = (event.minute, event.event_type, event.team, event.description)
        if event.event_key in event_keys or fingerprint in event_fingerprints:
            issues.append(
                QualityIssue("DUPLICATE_EVENT", "WARNING", f"Duplicate event detected at minute {event.minute}.", fixture.external_id)
            )
        event_keys.add(event.event_key)
        event_fingerprints.add(fingerprint)
        if event.team and event.team not in {fixture.home_team, fixture.away_team}:
            issues.append(
                QualityIssue("INCONSISTENT_EVENT_TEAM", "ERROR", f"Event references non-participant team {event.team}.", fixture.external_id)
            )
        if not 0 <= event.minute <= 150:
            issues.append(
                QualityIssue("INVALID_EVENT_MINUTE", "ERROR", f"Event minute {event.minute} is outside 0-150.", fixture.external_id)
            )
    return tuple(issues)


def validate_database(session: Session) -> tuple[QualityIssue, ...]:
    issues: list[QualityIssue] = []

    duplicates = session.execute(
        select(
            MatchEvent.match_id,
            MatchRecord.external_id,
            MatchEvent.minute,
            MatchEvent.event_type,
            MatchEvent.team_id,
            MatchEvent.description,
            func.count(MatchEvent.id).label("event_count"),
        )
        .join(MatchRecord, MatchEvent.match_id == MatchRecord.id)
        .group_by(
            MatchEvent.match_id,
            MatchRecord.external_id,
            MatchEvent.minute,
            MatchEvent.event_type,
            MatchEvent.team_id,
            MatchEvent.description,
        )
        .having(func.count(MatchEvent.id) > 1)
    ).all()
    for row in duplicates:
        issues.append(
            QualityIssue(
                "DUPLICATE_EVENT",
                "WARNING",
                f"Match contains {row.event_count} identical events at minute {row.minute}.",
                row.external_id,
            )
        )

    invalid_scores = session.execute(
        select(MatchRecord.external_id).where(
            or_(
                MatchRecord.home_score < 0,
                MatchRecord.away_score < 0,
                (MatchRecord.home_score.is_(None)) != (MatchRecord.away_score.is_(None)),
                (
                    (MatchRecord.status != "SCHEDULED")
                    & MatchRecord.home_score.is_(None)
                ),
            )
        )
    ).scalars()
    for external_id in invalid_scores:
        issues.append(
            QualityIssue("INVALID_SCORE", "ERROR", "Match has missing or invalid scores.", external_id)
        )

    missing_statistics = session.execute(
        select(MatchRecord.external_id)
        .outerjoin(MatchStatistics, MatchStatistics.match_id == MatchRecord.id)
        .where(MatchStatistics.id.is_(None))
    ).scalars()
    for external_id in missing_statistics:
        issues.append(
            QualityIssue("MISSING_STATISTICS", "WARNING", "Match has no statistics record.", external_id)
        )

    missing_events = session.execute(
        select(MatchRecord.external_id)
        .outerjoin(MatchEvent, MatchEvent.match_id == MatchRecord.id)
        .group_by(MatchRecord.id, MatchRecord.external_id)
        .having(func.count(MatchEvent.id) == 0)
        .where(MatchRecord.status != "SCHEDULED")
    ).scalars()
    for external_id in missing_events:
        issues.append(
            QualityIssue("MISSING_EVENTS", "WARNING", "Match has no recorded events.", external_id)
        )

    inconsistent_stats = session.execute(
        select(MatchRecord.external_id)
        .join(MatchStatistics, MatchStatistics.match_id == MatchRecord.id)
        .where(
            or_(
                func.abs(
                    MatchStatistics.home_possession + MatchStatistics.away_possession
                    - 100
                )
                > 0.10,
                MatchStatistics.home_shots_on_target > MatchStatistics.home_shots,
                MatchStatistics.away_shots_on_target > MatchStatistics.away_shots,
            )
        )
    ).scalars()
    for external_id in inconsistent_stats:
        issues.append(
            QualityIssue(
                "INCONSISTENT_STATISTICS",
                "WARNING",
                "Possession or shot statistics are inconsistent.",
                external_id,
            )
        )

    inconsistent_events = session.execute(
        select(MatchRecord.external_id, Team.name)
        .join(MatchEvent, MatchEvent.match_id == MatchRecord.id)
        .join(Team, Team.id == MatchEvent.team_id)
        .where(
            and_(
                MatchEvent.team_id.is_not(None),
                MatchEvent.team_id != MatchRecord.home_team_id,
                MatchEvent.team_id != MatchRecord.away_team_id,
            )
        )
    ).all()
    for external_id, team_name in inconsistent_events:
        issues.append(
            QualityIssue(
                "INCONSISTENT_EVENT_TEAM",
                "ERROR",
                f"Event references non-participant team {team_name}.",
                external_id,
            )
        )
    return tuple(issues)


def record_issues(session: Session, issues: tuple[QualityIssue, ...]) -> int:
    created = 0
    for issue in issues:
        match_id = None
        if issue.external_id:
            match_id = session.execute(
                select(MatchRecord.id).where(MatchRecord.external_id == issue.external_id)
            ).scalar_one_or_none()
        match_filter = (
            DataQualityAlert.match_id.is_(None)
            if match_id is None
            else DataQualityAlert.match_id == match_id
        )
        existing = session.execute(
            select(DataQualityAlert.id).where(
                DataQualityAlert.rule_code == issue.rule_code,
                match_filter,
                DataQualityAlert.message == issue.message,
                DataQualityAlert.resolved_at.is_(None),
            )
        ).scalar_one_or_none()
        if existing is None:
            session.add(
                DataQualityAlert(
                    match_id=match_id,
                    rule_code=issue.rule_code,
                    severity=issue.severity,
                    message=issue.message,
                )
            )
            created += 1
            logger.log(
                logging.ERROR if issue.severity == "ERROR" else logging.WARNING,
                "Data quality alert %s (%s): %s",
                issue.rule_code,
                issue.external_id or "unmatched",
                issue.message,
            )
    return created
