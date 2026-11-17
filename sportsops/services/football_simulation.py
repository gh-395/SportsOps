import math
import random
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP


BOOKMAKER_MARGIN = Decimal("0.05")
ODDS_QUANTUM = Decimal("0.001")
MINIMUM_ODDS = Decimal("1.010")
MAXIMUM_ODDS = Decimal("1000.000")
_MAX_POISSON_GOALS = 10


@dataclass(frozen=True)
class ScheduledFootballEvent:
    event_key: str
    minute: int
    event_type: str
    team_side: str | None
    description: str


def match_goal_rates(seed: int, match_id: int) -> tuple[Decimal, Decimal]:
    rng = random.Random((seed << 32) ^ match_id)
    home_rate = Decimal(str(round(rng.uniform(1.25, 1.75), 3)))
    away_rate = Decimal(str(round(rng.uniform(0.85, 1.35), 3)))
    return home_rate, away_rate


def generate_match_schedule(
    *,
    seed: int,
    match_id: int,
    match_key: str,
    home_team: str,
    away_team: str,
    start_minute: int,
    start_status: str,
    home_goal_rate: Decimal,
    away_goal_rate: Decimal,
) -> tuple[ScheduledFootballEvent, ...]:
    if not 0 <= start_minute <= 90:
        raise ValueError("A simulated match minute must be between 0 and 90.")
    if start_status not in ("SCHEDULED", "LIVE", "HALF_TIME"):
        raise ValueError(f"Unsupported starting match status: {start_status}.")

    rng = random.Random((seed << 32) ^ (match_id * 7919))
    remaining = max(0, 90 - start_minute) / 90
    candidates: list[tuple[int, int, str, str | None, str]] = []

    if start_status == "SCHEDULED":
        candidates.append((1, 0, "KICK_OFF", None, "Kick-off"))
    if start_minute < 45:
        candidates.append((45, 40, "HALF_TIME", None, "Half-time whistle"))
    if start_minute < 46 or start_status == "HALF_TIME":
        candidates.append((46, 5, "SECOND_HALF", None, "Second-half kick-off"))

    for side, team, rate in (
        ("HOME", home_team, home_goal_rate),
        ("AWAY", away_team, away_goal_rate),
    ):
        expected_goals = float(rate) * remaining
        goal_count = (
            _sample_poisson(rng, expected_goals) if start_minute < 89 else 0
        )
        for _ in range(goal_count):
            minute = rng.randint(max(1, start_minute + 1), 89)
            candidates.append(
                (minute, 20, "GOAL", side, f"{team} goal")
            )
        if start_minute < 89 and remaining and rng.random() < 0.13 * remaining:
            minute = rng.randint(max(1, start_minute + 1), 89)
            candidates.append(
                (minute, 10, "RED_CARD", side, f"{team} red card")
            )

    candidates.append((90, 90, "FULL_TIME", None, "Full-time whistle"))
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3] or ""))
    return tuple(
        ScheduledFootballEvent(
            event_key=f"{match_key}:{index}:{event_type}:{minute}",
            minute=minute,
            event_type=event_type,
            team_side=team_side,
            description=f"{description}, {minute}' (simulated)",
        )
        for index, (minute, _, event_type, team_side, description) in enumerate(
            candidates, start=1
        )
    )


def calculate_fair_probabilities(
    *,
    minute: int,
    home_score: int,
    away_score: int,
    home_red_cards: int,
    away_red_cards: int,
    home_goal_rate: Decimal,
    away_goal_rate: Decimal,
) -> dict[tuple[str, str], Decimal]:
    if not 0 <= minute <= 90:
        raise ValueError("A simulated match minute must be between 0 and 90.")
    if min(home_score, away_score, home_red_cards, away_red_cards) < 0:
        raise ValueError("Scores and red-card counts cannot be negative.")

    remaining_fraction = Decimal(90 - minute) / Decimal(90)
    home_adjustment = Decimal("1")
    away_adjustment = Decimal("1")
    if home_red_cards:
        home_adjustment *= Decimal("0.62")
        away_adjustment *= Decimal("1.35")
    if away_red_cards:
        away_adjustment *= Decimal("0.62")
        home_adjustment *= Decimal("1.35")
    home_lambda = float(home_goal_rate * remaining_fraction * home_adjustment)
    away_lambda = float(away_goal_rate * remaining_fraction * away_adjustment)
    home_distribution = _poisson_distribution(home_lambda)
    away_distribution = _poisson_distribution(away_lambda)

    result = {"HOME": 0.0, "DRAW": 0.0, "AWAY": 0.0}
    over = 0.0
    both_score = 0.0
    for home_goals, home_probability in enumerate(home_distribution):
        for away_goals, away_probability in enumerate(away_distribution):
            probability = home_probability * away_probability
            final_home = home_score + home_goals
            final_away = away_score + away_goals
            if final_home > final_away:
                result["HOME"] += probability
            elif final_home == final_away:
                result["DRAW"] += probability
            else:
                result["AWAY"] += probability
            if final_home + final_away > 2:
                over += probability
            if final_home > 0 and final_away > 0:
                both_score += probability

    return {
        ("1X2", key): _decimal_probability(value)
        for key, value in _normalize(result).items()
    } | {
        ("OVER_UNDER_2_5", "OVER"): _decimal_probability(over),
        ("OVER_UNDER_2_5", "UNDER"): _decimal_probability(1.0 - over),
        ("BOTH_TEAMS_TO_SCORE", "YES"): _decimal_probability(both_score),
        ("BOTH_TEAMS_TO_SCORE", "NO"): _decimal_probability(1.0 - both_score),
    }


def calculate_event_driven_odds(
    fair_probabilities: dict[tuple[str, str], Decimal],
) -> dict[tuple[str, str], Decimal]:
    quoted_odds: dict[tuple[str, str], Decimal] = {}
    market_totals: dict[str, Decimal] = {}
    for (market_type, selection_key), probability in fair_probabilities.items():
        if probability < 0 or probability > 1:
            raise ValueError("Fair probabilities must be between zero and one.")
        if probability == 0:
            odds = MAXIMUM_ODDS
        else:
            raw_odds = Decimal("1") / (
                probability * (Decimal("1") + BOOKMAKER_MARGIN)
            )
            if raw_odds >= MAXIMUM_ODDS:
                odds = MAXIMUM_ODDS
            else:
                odds = max(
                    MINIMUM_ODDS,
                    raw_odds.quantize(ODDS_QUANTUM, rounding=ROUND_HALF_UP),
                )
        quoted_odds[(market_type, selection_key)] = odds
        market_totals[market_type] = (
            market_totals.get(market_type, Decimal("0"))
            + Decimal("1") / odds
        )
    if any(total <= 0 for total in market_totals.values()):
        raise ArithmeticError("A market quote set has no implied probability.")
    return quoted_odds


def _sample_poisson(rng: random.Random, rate: float) -> int:
    if rate <= 0:
        return 0
    limit = math.exp(-rate)
    product = 1.0
    count = 0
    while product > limit:
        count += 1
        product *= rng.random()
    return count - 1


def _poisson_distribution(rate: float) -> tuple[float, ...]:
    probabilities = [math.exp(-rate)]
    for goals in range(1, _MAX_POISSON_GOALS + 1):
        probabilities.append(probabilities[-1] * rate / goals)
    total = sum(probabilities)
    return tuple(value / total for value in probabilities)


def _normalize(values: dict[str, float]) -> dict[str, float]:
    total = sum(values.values())
    if total <= 0:
        raise ArithmeticError("Outcome probabilities must have a positive sum.")
    return {key: value / total for key, value in values.items()}


def _decimal_probability(value: float) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.000000001"))
