from datetime import datetime
from dataclasses import dataclass


@dataclass(frozen=True)
class Match:
    match_id: str
    competition: str
    home_team: str
    away_team: str
    home_score: int | None
    away_score: int | None
    elapsed_minute: int | None
    status: str
    possession: str
    shots: str
    shots_on_target: str
    last_event: str
    simulated: bool = True
    last_update: datetime | None = None
    data_quality: str = "CLEAR"
    season: str = ""
    competition_country: str = ""
    home_team_country: str = ""
    away_team_country: str = ""
    kickoff_at: datetime | None = None
    data_origin: str = "SYNTHETIC"
    source_name: str = "SportsOps demo seed"
    source_reference: str | None = None

    @property
    def score(self) -> str:
        if self.home_score is None or self.away_score is None:
            return "—"
        return f"{self.home_score} - {self.away_score}"

    @property
    def minute(self) -> str:
        if self.elapsed_minute is not None:
            return f"{self.elapsed_minute}'"
        if self.status == "HALF_TIME":
            return "HT"
        if self.status == "FINISHED":
            return "FT"
        return "—"

    @property
    def data_source(self) -> str:
        return {
            "SIMULATED": "SIMULATED DATA",
            "SYNTHETIC": "SYNTHETIC DEMO (SIMULATED DATABASE)",
            "HISTORICAL": "HISTORICAL DATA",
        }.get(self.data_origin, self.source_name)


@dataclass(frozen=True)
class MatchEventInfo:
    minute: int
    event_type: str
    team: str
    description: str
    simulated: bool


@dataclass(frozen=True)
class MatchDetails:
    match: Match
    events: tuple[MatchEventInfo, ...]


@dataclass(frozen=True)
class AlertInfo:
    alert_id: int
    match_id: str
    severity: str
    description: str
    detected_at: datetime
    acknowledged_at: datetime | None
    resolved_at: datetime | None


@dataclass(frozen=True)
class ActivityEntry:
    timestamp: datetime
    severity: str
    message: str
