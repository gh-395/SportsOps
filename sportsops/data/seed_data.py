from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal


@dataclass(frozen=True)
class EventFixture:
    event_key: str
    minute: int
    event_type: str
    team: str | None
    description: str


@dataclass(frozen=True)
class MatchFixture:
    external_id: str
    competition: str
    country: str
    season: str
    home_team: str
    away_team: str
    kickoff_at: datetime
    status: str
    elapsed_minute: int | None
    home_score: int | None
    away_score: int | None
    home_possession: Decimal
    away_possession: Decimal
    home_shots: int
    away_shots: int
    home_shots_on_target: int
    away_shots_on_target: int
    events: tuple[EventFixture, ...]


SEASON_START = date(2025, 8, 1)
SEASON_END = date(2026, 6, 30)


def _event(
    event_key: str,
    minute: int,
    event_type: str,
    team: str | None,
    description: str,
) -> EventFixture:
    return EventFixture(event_key, minute, event_type, team, description)


DEMO_MATCHES = (
    MatchFixture(
        "SIM-1001", "Premier League", "England", "2025/26", "Arsenal",
        "Brighton", datetime(2026, 11, 14, 17, 30, tzinfo=timezone.utc),
        "LIVE", 67, 2, 1, Decimal("58.00"), Decimal("42.00"), 14, 9, 6, 4,
        (
            _event("1001-1", 12, "GOAL", "Arsenal", "Arsenal goal, 12'"),
            _event("1001-2", 38, "GOAL", "Brighton", "Brighton goal, 38'"),
            _event("1001-3", 64, "GOAL", "Arsenal", "Arsenal goal, 64'"),
        ),
    ),
    MatchFixture(
        "SIM-1002", "Premier League", "England", "2025/26", "Aston Villa",
        "West Ham", datetime(2026, 11, 14, 15, 0, tzinfo=timezone.utc),
        "LIVE", 32, 0, 0, Decimal("51.00"), Decimal("49.00"), 5, 4, 2, 1,
        (
            _event("1002-1", 29, "CORNER", "West Ham", "West Ham corner, 29'"),
        ),
    ),
    MatchFixture(
        "SIM-2001", "La Liga", "Spain", "2025/26", "Real Madrid",
        "Sevilla", datetime(2026, 11, 14, 18, 0, tzinfo=timezone.utc),
        "HALF_TIME", None, 1, 0, Decimal("62.00"), Decimal("38.00"), 8, 3, 4, 1,
        (
            _event("2001-1", 33, "GOAL", "Real Madrid", "Real Madrid goal, 33'"),
            _event("2001-2", 45, "HALF_TIME", None, "Half-time whistle, 45+2'"),
        ),
    ),
    MatchFixture(
        "SIM-2002", "La Liga", "Spain", "2025/26", "Real Sociedad",
        "Girona", datetime(2026, 11, 14, 16, 15, tzinfo=timezone.utc),
        "LIVE", 78, 1, 2, Decimal("47.00"), Decimal("53.00"), 10, 12, 4, 6,
        (
            _event("2002-1", 21, "GOAL", "Real Sociedad", "Real Sociedad goal, 21'"),
            _event("2002-2", 52, "GOAL", "Girona", "Girona goal, 52'"),
            _event("2002-3", 75, "GOAL", "Girona", "Girona goal, 75'"),
        ),
    ),
    MatchFixture(
        "SIM-3001", "Serie A", "Italy", "2025/26", "Inter", "Atalanta",
        datetime(2026, 11, 13, 19, 45, tzinfo=timezone.utc),
        "FINISHED", None, 3, 1, Decimal("55.00"), Decimal("45.00"), 16, 8, 8, 3,
        (
            _event("3001-1", 18, "GOAL", "Inter", "Inter goal, 18'"),
            _event("3001-2", 40, "GOAL", "Atalanta", "Atalanta goal, 40'"),
            _event("3001-3", 64, "GOAL", "Inter", "Inter goal, 64'"),
            _event("3001-4", 83, "GOAL", "Inter", "Inter goal, 83'"),
            _event("3001-5", 90, "FULL_TIME", None, "Full-time whistle, 90+4'"),
        ),
    ),
    MatchFixture(
        "SIM-3002", "Serie A", "Italy", "2025/26", "Napoli", "Lazio",
        datetime(2026, 11, 14, 20, 45, tzinfo=timezone.utc),
        "LIVE", 11, 0, 0, Decimal("60.00"), Decimal("40.00"), 2, 1, 1, 0,
        (
            _event("3002-1", 1, "KICK_OFF", None, "Kick-off, 1'"),
        ),
    ),
    MatchFixture(
        "SIM-1003", "Premier League", "England", "2025/26", "Liverpool",
        "Chelsea", datetime(2026, 11, 15, 16, 30, tzinfo=timezone.utc),
        "SCHEDULED", None, None, None, Decimal("50.00"), Decimal("50.00"),
        0, 0, 0, 0, (),
    ),
)
