from dataclasses import dataclass
from datetime import date, datetime, time


@dataclass(frozen=True)
class HistoricalMatchInfo:
    historical_id: int
    kickoff_date: date
    kickoff_time: time | None
    competition: str
    season: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    source_name: str
    source_url: str
    reuse_terms: str
    imported_at: datetime

    @property
    def score(self) -> str:
        return f"{self.home_score} - {self.away_score}"

    @property
    def result(self) -> str:
        if self.home_score > self.away_score:
            return "HOME WIN"
        if self.home_score < self.away_score:
            return "AWAY WIN"
        return "DRAW"


@dataclass(frozen=True)
class HistoricalFilterOptions:
    competitions: tuple[str, ...]
    seasons: tuple[str, ...]
    teams: tuple[str, ...]


@dataclass(frozen=True)
class HistoricalTeamSummary:
    position: int
    team: str
    played: int
    home_wins: int
    home_draws: int
    home_losses: int
    away_wins: int
    away_draws: int
    away_losses: int
    goals_for: int
    goals_against: int
    goal_difference: int
    points: int
    average_goals_for: float
    average_goals_against: float


@dataclass(frozen=True)
class HistoricalImportBatchInfo:
    imported_at: datetime
    source_name: str
    source_url: str
    reuse_terms: str
    file_sha256: str
    rows_seen: int
    imported_count: int
    duplicate_count: int
    invalid_count: int


@dataclass(frozen=True)
class HistoricalImportIssue:
    row_number: int
    message: str


@dataclass(frozen=True)
class HistoricalImportResult:
    file_sha256: str
    rows_seen: int
    imported_count: int
    duplicate_count: int
    invalid_count: int
    already_imported: bool
    issues: tuple[HistoricalImportIssue, ...] = ()
