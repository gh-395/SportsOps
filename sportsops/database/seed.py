from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from sportsops.data.seed_data import DEMO_MATCHES, MatchFixture, SEASON_END, SEASON_START
from sportsops.database.validation import record_issues, validate_fixture
from sportsops.database.models import (
    Competition,
    Market,
    MarketSelection,
    MatchEvent,
    MatchRecord,
    MatchStatistics,
    Season,
    Team,
    TradingAuditLog,
)


def _get_or_create_team(session: Session, name: str, country: str) -> Team:
    team = session.execute(
        select(Team).where(Team.name == name, Team.country == country)
    ).scalar_one_or_none()
    if team is None:
        team = Team(name=name, country=country)
        session.add(team)
        session.flush()
    return team


def _get_or_create_competition(session: Session, name: str, country: str) -> Competition:
    competition = session.execute(
        select(Competition).where(Competition.name == name)
    ).scalar_one_or_none()
    if competition is None:
        competition = Competition(name=name, country=country)
        session.add(competition)
        session.flush()
    return competition


def _get_or_create_season(
    session: Session, competition_id: int, name: str
) -> Season:
    season = session.execute(
        select(Season).where(
            Season.competition_id == competition_id,
            Season.name == name,
        )
    ).scalar_one_or_none()
    if season is None:
        season = Season(
            competition_id=competition_id,
            name=name,
            start_date=SEASON_START,
            end_date=SEASON_END,
        )
        session.add(season)
        session.flush()
    return season


def seed_demo_data(session: Session, fixtures: tuple[MatchFixture, ...] = DEMO_MATCHES) -> None:
    for fixture in fixtures:
        fixture_issues = validate_fixture(fixture)
        if fixture_issues:
            record_issues(session, fixture_issues)
            continue
        competition = _get_or_create_competition(
            session, fixture.competition, fixture.country
        )
        season = _get_or_create_season(session, competition.id, fixture.season)
        home_team = _get_or_create_team(session, fixture.home_team, fixture.country)
        away_team = _get_or_create_team(session, fixture.away_team, fixture.country)
        match = MatchRecord(
            external_id=fixture.external_id,
            season_id=season.id,
            home_team_id=home_team.id,
            away_team_id=away_team.id,
            kickoff_at=fixture.kickoff_at,
            status=fixture.status,
            elapsed_minute=fixture.elapsed_minute,
            home_score=fixture.home_score,
            away_score=fixture.away_score,
            is_simulated=True,
            data_origin="SYNTHETIC",
            source_name="SportsOps synthetic demo",
            source_reference="sportsops/data/seed_data.py",
        )
        session.add(match)
        session.flush()
        session.add(
            MatchStatistics(
                match_id=match.id,
                home_possession=fixture.home_possession,
                away_possession=fixture.away_possession,
                home_shots=fixture.home_shots,
                away_shots=fixture.away_shots,
                home_shots_on_target=fixture.home_shots_on_target,
                away_shots_on_target=fixture.away_shots_on_target,
            )
        )
        for event_fixture in fixture.events:
            event_team = None
            if event_fixture.team == fixture.home_team:
                event_team = home_team
            elif event_fixture.team == fixture.away_team:
                event_team = away_team
            session.add(
                MatchEvent(
                    match_id=match.id,
                    event_key=event_fixture.event_key,
                    minute=event_fixture.minute,
                    event_type=event_fixture.event_type,
                    team_id=event_team.id if event_team else None,
                    description=event_fixture.description,
                    is_simulated=True,
                )
            )


def seed_demo_markets(session: Session) -> None:
    home_team = aliased(Team, name="market_home_team")
    away_team = aliased(Team, name="market_away_team")
    matches = session.execute(
        select(MatchRecord, home_team.name, away_team.name)
        .join(home_team, home_team.id == MatchRecord.home_team_id)
        .join(away_team, away_team.id == MatchRecord.away_team_id)
        .where(MatchRecord.is_simulated.is_(True))
        .order_by(MatchRecord.external_id)
    ).all()
    for match, home_team, away_team in matches:
        variation = Decimal(int(match.external_id.rsplit("-", 1)[-1]) % 7) / Decimal(100)
        market_definitions = (
            (
                "1X2",
                (
                    ("HOME", home_team, Decimal("2.05") + variation),
                    ("DRAW", "Draw", Decimal("3.35") + variation * 2),
                    ("AWAY", away_team, Decimal("3.60") - variation * 3),
                ),
            ),
            (
                "OVER_UNDER_2_5",
                (
                    ("OVER", "Over 2.5", Decimal("1.88") + variation),
                    ("UNDER", "Under 2.5", Decimal("1.94") - variation),
                ),
            ),
            (
                "BOTH_TEAMS_TO_SCORE",
                (
                    ("YES", "Yes", Decimal("1.82") + variation),
                    ("NO", "No", Decimal("2.02") - variation),
                ),
            ),
        )
        for market_type, selections in market_definitions:
            market = session.execute(
                select(Market).where(
                    Market.match_id == match.id,
                    Market.market_type == market_type,
                )
            ).scalar_one_or_none()
            if market is None:
                market = Market(
                    match_id=match.id,
                    market_type=market_type,
                    status="CLOSED" if match.status == "FINISHED" else "OPEN",
                    is_simulated=True,
                )
                session.add(market)
                session.flush()
                session.add(
                    TradingAuditLog(
                        market_id=market.id,
                        event_type="MARKET_CREATED",
                        new_value=market.status,
                        reason="Initial simulated demo market.",
                        actor="SportsOps seed",
                        is_simulated=True,
                        idempotency_key=f"seed-market:{market.id}",
                    )
                )
            existing_keys = set(
                session.scalars(
                    select(MarketSelection.selection_key).where(
                        MarketSelection.market_id == market.id
                    )
                )
            )
            for selection_key, selection_name, odds in selections:
                if selection_key in existing_keys:
                    continue
                session.add(
                    MarketSelection(
                        market_id=market.id,
                        selection_key=selection_key,
                        selection_name=selection_name,
                        opening_odds=odds,
                        current_odds=odds,
                    )
                )
