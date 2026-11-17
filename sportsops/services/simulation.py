from dataclasses import replace
from datetime import datetime, timezone

from sportsops.models.match import Match, MatchEventInfo


class SimulationController:
    _SCHEDULED_EVENTS = {
        "SIM-1001": {
            68: ("GOAL", "Arsenal", "Arsenal goal (simulated), 68'"),
            72: ("YELLOW CARD", "Brighton", "Brighton yellow card (simulated), 72'"),
        },
        "SIM-1002": {
            33: ("YELLOW CARD", "Aston Villa", "Aston Villa yellow card (simulated), 33'"),
            34: (
                "MATCH INTERRUPTED",
                "—",
                "Match temporarily interrupted (simulated), 34'",
            ),
        },
        "SIM-2002": {
            79: ("SUBSTITUTION", "Girona", "Girona substitution (simulated), 79'"),
        },
        "SIM-3002": {
            12: ("SHOT", "Napoli", "Napoli shot (simulated), 12'"),
        },
    }

    def __init__(self, matches: tuple[Match, ...]) -> None:
        self._original_matches = matches
        self._matches = {match.match_id: match for match in matches}
        self._new_events: dict[str, list[MatchEventInfo]] = {
            match.match_id: [] for match in matches
        }
        self._running = False
        self._started = False

    @property
    def running(self) -> bool:
        return self._running

    @property
    def started(self) -> bool:
        return self._started

    @property
    def matches(self) -> tuple[Match, ...]:
        return tuple(self._matches[match.match_id] for match in self._original_matches)

    def start(self) -> tuple[Match, ...]:
        if self._running:
            return self.matches
        self._running = True
        self._started = True
        now = datetime.now(timezone.utc)
        for match in self.matches:
            if match.status == "HALF_TIME":
                self._matches[match.match_id] = replace(
                    match,
                    status="LIVE",
                    elapsed_minute=45,
                    last_update=now,
                )
                self._new_events[match.match_id].append(
                    MatchEventInfo(
                        minute=46,
                        event_type="SECOND HALF",
                        team="—",
                        description="Second-half kick-off (simulated)",
                        simulated=True,
                    )
                )
        return self.matches

    def pause(self) -> None:
        self._running = False

    def reset(self) -> tuple[Match, ...]:
        self._running = False
        self._started = False
        self._matches = {match.match_id: match for match in self._original_matches}
        self._new_events = {match.match_id: [] for match in self._original_matches}
        return self.matches

    def events_for(self, match_id: str) -> tuple[MatchEventInfo, ...]:
        return tuple(self._new_events[match_id])

    def advance(self, now: datetime | None = None) -> tuple[Match, ...]:
        if not self._running:
            return self.matches
        timestamp = now or datetime.now(timezone.utc)
        for match in self.matches:
            if match.status != "LIVE":
                continue
            minute = (match.elapsed_minute or 0) + 1
            home_score = match.home_score or 0
            away_score = match.away_score or 0
            event = self._SCHEDULED_EVENTS.get(match.match_id, {}).get(minute)
            if event is not None:
                event_type, team, description = event
                self._new_events[match.match_id].append(
                    MatchEventInfo(
                        minute=minute,
                        event_type=event_type,
                        team=team,
                        description=description,
                        simulated=True,
                    )
                )
                if event_type == "GOAL":
                    if team == match.home_team:
                        home_score += 1
                    elif team == match.away_team:
                        away_score += 1
            updated_status = match.status
            if event is not None and event[0] == "MATCH INTERRUPTED":
                updated_status = "INTERRUPTED"
            elif minute >= 90:
                updated_status = "FINISHED"
            self._matches[match.match_id] = replace(
                match,
                elapsed_minute=minute,
                home_score=home_score,
                away_score=away_score,
                status=updated_status,
                last_event=event[2] if event else match.last_event,
                last_update=timestamp,
            )
        return self.matches
