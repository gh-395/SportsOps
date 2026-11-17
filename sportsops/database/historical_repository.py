import csv
import hashlib
import io
import re
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path

from sqlalchemy import case, func, literal, or_, select, union_all
from sqlalchemy.orm import Session, aliased

from sportsops.database.models import (
    Competition,
    HistoricalImportBatch,
    HistoricalMatch,
    Season,
    Team,
)
from sportsops.models.historical import (
    HistoricalImportIssue,
    HistoricalImportResult,
    HistoricalFilterOptions,
    HistoricalImportBatchInfo,
    HistoricalMatchInfo,
    HistoricalTeamSummary,
)

_REQUIRED_HEADERS = ("Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG")
_MAX_CSV_BYTES = 20 * 1024 * 1024


@dataclass(frozen=True)
class _ParsedHistoricalRow:
    row_number: int
    kickoff_date: date
    kickoff_time: time | None
    home_team: str
    away_team: str
    home_score: int
    away_score: int


def load_historical_matches(
    session: Session,
) -> tuple[HistoricalMatchInfo, ...]:
    home_team = aliased(Team, name="historical_home_team")
    away_team = aliased(Team, name="historical_away_team")
    rows = session.execute(
        select(
            HistoricalMatch,
            Competition.name,
            Season.name,
            home_team.name,
            away_team.name,
            HistoricalImportBatch.source_name,
            HistoricalImportBatch.source_url,
            HistoricalImportBatch.reuse_terms,
            HistoricalImportBatch.imported_at,
        )
        .join(Season, Season.id == HistoricalMatch.season_id)
        .join(Competition, Competition.id == Season.competition_id)
        .join(home_team, home_team.id == HistoricalMatch.home_team_id)
        .join(away_team, away_team.id == HistoricalMatch.away_team_id)
        .join(
            HistoricalImportBatch,
            HistoricalImportBatch.id == HistoricalMatch.source_batch_id,
        )
        .order_by(
            HistoricalMatch.kickoff_date.desc(),
            HistoricalMatch.id.desc(),
        )
    ).all()
    return tuple(
        HistoricalMatchInfo(
            historical_id=match.id,
            kickoff_date=match.kickoff_date,
            kickoff_time=match.kickoff_time,
            competition=competition,
            season=season,
            home_team=home_name,
            away_team=away_name,
            home_score=match.home_score,
            away_score=match.away_score,
            source_name=source_name,
            source_url=source_url,
            reuse_terms=reuse_terms,
            imported_at=imported_at,
        )
        for (
            match,
            competition,
            season,
            home_name,
            away_name,
            source_name,
            source_url,
            reuse_terms,
            imported_at,
        ) in rows
    )


def load_historical_match_page(
    session: Session,
    *,
    competition_name: str | None = None,
    season_name: str | None = None,
    team_name: str | None = None,
    team_name_exact: str | None = None,
    search_text: str = "",
    result: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    page: int = 0,
    page_size: int = 200,
    sort_key: str = "kickoff_date",
    descending: bool = True,
) -> tuple[tuple[HistoricalMatchInfo, ...], int]:
    if page < 0 or not 1 <= page_size <= 500:
        raise ValueError("Page must be nonnegative and page size must be 1-500.")
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("Historical start date must not be after end date.")
    if result not in (None, "HOME WIN", "DRAW", "AWAY WIN"):
        raise ValueError(f"Unsupported historical result filter: {result}.")

    home_team = aliased(Team, name="page_home_team")
    away_team = aliased(Team, name="page_away_team")
    batch = aliased(HistoricalImportBatch, name="page_import_batch")
    joins = (
        (Season, Season.id == HistoricalMatch.season_id),
        (Competition, Competition.id == Season.competition_id),
        (home_team, home_team.id == HistoricalMatch.home_team_id),
        (away_team, away_team.id == HistoricalMatch.away_team_id),
        (batch, batch.id == HistoricalMatch.source_batch_id),
    )
    criteria = _match_filters(
        competition_name=competition_name,
        season_name=season_name,
        team_name=team_name,
        team_name_exact=team_name_exact,
        search_text=search_text,
        result=result,
        start_date=start_date,
        end_date=end_date,
        home_team=home_team,
        away_team=away_team,
        batch=batch,
    )

    count_query = select(func.count(HistoricalMatch.id)).select_from(HistoricalMatch)
    query = select(
        HistoricalMatch,
        Competition.name,
        Season.name,
        home_team.name,
        away_team.name,
        batch.source_name,
        batch.source_url,
        batch.reuse_terms,
        batch.imported_at,
    ).select_from(HistoricalMatch)
    for target, on_clause in joins:
        count_query = count_query.join(target, on_clause)
        query = query.join(target, on_clause)
    total = int(session.scalar(count_query.where(*criteria)) or 0)

    sort_columns = {
        "kickoff_date": (HistoricalMatch.kickoff_date,),
        "kickoff_time": (HistoricalMatch.kickoff_time,),
        "competition": (Competition.name,),
        "season": (Season.name,),
        "home_team": (home_team.name,),
        "score": (HistoricalMatch.home_score, HistoricalMatch.away_score),
        "away_team": (away_team.name,),
        "result": (
            case(
                (HistoricalMatch.home_score > HistoricalMatch.away_score, "HOME WIN"),
                (HistoricalMatch.home_score < HistoricalMatch.away_score, "AWAY WIN"),
                else_="DRAW",
            ),
        ),
        "source": (batch.source_name,),
    }
    if sort_key not in sort_columns:
        raise ValueError(f"Unsupported historical sort column: {sort_key}.")
    ordered_columns = tuple(
        column.desc() if descending else column.asc()
        for column in sort_columns[sort_key]
    )
    rows = session.execute(
        query.where(*criteria)
        .order_by(*ordered_columns, HistoricalMatch.id.desc())
        .offset(page * page_size)
        .limit(page_size)
    ).all()
    matches = tuple(
        HistoricalMatchInfo(
            historical_id=match.id,
            kickoff_date=match.kickoff_date,
            kickoff_time=match.kickoff_time,
            competition=competition,
            season=season,
            home_team=home_name,
            away_team=away_name,
            home_score=match.home_score,
            away_score=match.away_score,
            source_name=source_name,
            source_url=source_url,
            reuse_terms=reuse_terms,
            imported_at=imported_at,
        )
        for (
            match,
            competition,
            season,
            home_name,
            away_name,
            source_name,
            source_url,
            reuse_terms,
            imported_at,
        ) in rows
    )
    return matches, total


def load_historical_facet_options(
    session: Session,
    facet: str,
    *,
    competition_name: str | None = None,
    season_name: str | None = None,
    team_name: str | None = None,
) -> tuple[str, ...]:
    if facet not in ("competition", "season", "team"):
        raise ValueError(f"Unsupported historical filter facet: {facet}.")

    def option_query(column, side: str | None = None):
        query_tag = side or "facet"
        home_team = aliased(Team, name=f"{facet}_{query_tag}_home_team")
        away_team = aliased(Team, name=f"{facet}_{query_tag}_away_team")
        query = (
            select(column)
            .select_from(HistoricalMatch)
            .join(Season, Season.id == HistoricalMatch.season_id)
            .join(Competition, Competition.id == Season.competition_id)
            .join(home_team, home_team.id == HistoricalMatch.home_team_id)
            .join(away_team, away_team.id == HistoricalMatch.away_team_id)
        )
        criteria = []
        if facet != "competition" and competition_name:
            criteria.append(Competition.name == competition_name)
        if facet != "season" and season_name:
            criteria.append(Season.name == season_name)
        if facet != "team" and team_name:
            criteria.append(
                or_(
                    home_team.name == team_name,
                    away_team.name == team_name,
                )
            )
        if side == "home":
            query = query.with_only_columns(home_team.name)
        elif side == "away":
            query = query.with_only_columns(away_team.name)
        return query.where(*criteria)

    if facet == "competition":
        query = option_query(Competition.name).distinct().order_by(Competition.name)
    elif facet == "season":
        query = option_query(Season.name).distinct().order_by(Season.name)
    else:
        historical_teams = union_all(
            option_query(Team.name, side="home"),
            option_query(Team.name, side="away"),
        ).subquery("available_historical_teams")
        query = select(historical_teams.c.name).distinct().order_by(
            historical_teams.c.name
        )
    return tuple(session.scalars(query))


def load_historical_filter_options(
    session: Session,
    competition_name: str | None = None,
    season_name: str | None = None,
    team_name: str | None = None,
) -> HistoricalFilterOptions:
    return HistoricalFilterOptions(
        competitions=load_historical_facet_options(
            session,
            "competition",
            competition_name=competition_name,
            season_name=season_name,
            team_name=team_name,
        ),
        seasons=load_historical_facet_options(
            session,
            "season",
            competition_name=competition_name,
            season_name=season_name,
            team_name=team_name,
        ),
        teams=load_historical_facet_options(
            session,
            "team",
            competition_name=competition_name,
            season_name=season_name,
            team_name=team_name,
        ),
    )


def load_historical_team_summary(
    session: Session,
    *,
    competition_name: str | None = None,
    season_name: str | None = None,
    team_name: str | None = None,
    team_name_exact: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    result: str | None = None,
    search_text: str = "",
) -> tuple[HistoricalTeamSummary, ...]:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise ValueError("Historical start date must not be after end date.")
    if result not in (None, "HOME WIN", "DRAW", "AWAY WIN"):
        raise ValueError(f"Unsupported historical result filter: {result}.")
    criteria = []
    if competition_name:
        criteria.append(Competition.name == competition_name)
    if season_name:
        criteria.append(Season.name == season_name)
    if start_date is not None:
        criteria.append(HistoricalMatch.kickoff_date >= start_date)
    if end_date is not None:
        criteria.append(HistoricalMatch.kickoff_date <= end_date)
    if result == "HOME WIN":
        criteria.append(HistoricalMatch.home_score > HistoricalMatch.away_score)
    elif result == "DRAW":
        criteria.append(HistoricalMatch.home_score == HistoricalMatch.away_score)
    elif result == "AWAY WIN":
        criteria.append(HistoricalMatch.home_score < HistoricalMatch.away_score)
    for term in search_text.split():
        pattern = _contains_pattern(term)
        matching_teams = select(Team.id).where(Team.name.ilike(pattern, escape="\\"))
        matching_sources = select(HistoricalImportBatch.id).where(
            HistoricalImportBatch.source_name.ilike(pattern, escape="\\")
        )
        criteria.append(
            or_(
                HistoricalMatch.home_team_id.in_(matching_teams),
                HistoricalMatch.away_team_id.in_(matching_teams),
                Competition.name.ilike(pattern, escape="\\"),
                Season.name.ilike(pattern, escape="\\"),
                HistoricalMatch.source_batch_id.in_(matching_sources),
            )
        )

    source_joins = (
        (Season, Season.id == HistoricalMatch.season_id),
        (Competition, Competition.id == Season.competition_id),
    )

    def appearances(team_id, goals_for, goals_against, venue):
        return (
            select(
                team_id.label("team_id"),
                literal(venue).label("venue"),
                literal(1).label("played"),
                case((goals_for > goals_against, 1), else_=0).label("wins"),
                case((goals_for == goals_against, 1), else_=0).label("draws"),
                case((goals_for < goals_against, 1), else_=0).label("losses"),
                goals_for.label("goals_for"),
                goals_against.label("goals_against"),
                case(
                    (goals_for > goals_against, 3),
                    (goals_for == goals_against, 1),
                    else_=0,
                ).label("points"),
            )
            .select_from(HistoricalMatch)
            .join(*source_joins[0])
            .join(*source_joins[1])
            .where(*criteria)
        )

    appearance_rows = union_all(
        appearances(
            HistoricalMatch.home_team_id,
            HistoricalMatch.home_score,
            HistoricalMatch.away_score,
            "HOME",
        ),
        appearances(
            HistoricalMatch.away_team_id,
            HistoricalMatch.away_score,
            HistoricalMatch.home_score,
            "AWAY",
        ),
    ).subquery("historical_appearances")

    totals = (
        select(
            Team.id.label("team_id"),
            Team.name.label("team"),
            func.sum(appearance_rows.c.played).label("played"),
            func.sum(appearance_rows.c.wins).label("wins"),
            func.sum(appearance_rows.c.draws).label("draws"),
            func.sum(appearance_rows.c.losses).label("losses"),
            func.sum(
                case((appearance_rows.c.venue == "HOME", appearance_rows.c.wins), else_=0)
            ).label("home_wins"),
            func.sum(
                case((appearance_rows.c.venue == "HOME", appearance_rows.c.draws), else_=0)
            ).label("home_draws"),
            func.sum(
                case((appearance_rows.c.venue == "HOME", appearance_rows.c.losses), else_=0)
            ).label("home_losses"),
            func.sum(
                case((appearance_rows.c.venue == "AWAY", appearance_rows.c.wins), else_=0)
            ).label("away_wins"),
            func.sum(
                case((appearance_rows.c.venue == "AWAY", appearance_rows.c.draws), else_=0)
            ).label("away_draws"),
            func.sum(
                case((appearance_rows.c.venue == "AWAY", appearance_rows.c.losses), else_=0)
            ).label("away_losses"),
            func.sum(appearance_rows.c.goals_for).label("goals_for"),
            func.sum(appearance_rows.c.goals_against).label("goals_against"),
            (func.sum(appearance_rows.c.goals_for) - func.sum(
                appearance_rows.c.goals_against
            )).label("goal_difference"),
            func.sum(appearance_rows.c.points).label("points"),
            func.avg(appearance_rows.c.goals_for).label("average_goals_for"),
            func.avg(appearance_rows.c.goals_against).label("average_goals_against"),
        )
        .join(appearance_rows, appearance_rows.c.team_id == Team.id)
        .group_by(Team.id, Team.name)
        .cte("historical_team_totals")
    )
    order = (
        totals.c.points.desc(),
        totals.c.goal_difference.desc(),
        totals.c.goals_for.desc(),
        totals.c.team.asc(),
    )
    query = select(
        func.rank().over(order_by=order).label("position"),
        totals.c.team,
        totals.c.played,
        totals.c.home_wins,
        totals.c.home_draws,
        totals.c.home_losses,
        totals.c.away_wins,
        totals.c.away_draws,
        totals.c.away_losses,
        totals.c.goals_for,
        totals.c.goals_against,
        totals.c.goal_difference,
        totals.c.points,
        totals.c.average_goals_for,
        totals.c.average_goals_against,
    ).order_by(*order)
    if team_name or team_name_exact:
        query = query.join(Team, Team.id == totals.c.team_id)
        if team_name:
            query = query.where(
                Team.name.ilike(_contains_pattern(team_name), escape="\\")
            )
        if team_name_exact:
            query = query.where(Team.name == team_name_exact)
    rows = session.execute(query).all()
    return tuple(
        HistoricalTeamSummary(
            position=int(row.position),
            team=row.team,
            played=int(row.played),
            home_wins=int(row.home_wins),
            home_draws=int(row.home_draws),
            home_losses=int(row.home_losses),
            away_wins=int(row.away_wins),
            away_draws=int(row.away_draws),
            away_losses=int(row.away_losses),
            goals_for=int(row.goals_for),
            goals_against=int(row.goals_against),
            goal_difference=int(row.goal_difference),
            points=int(row.points),
            average_goals_for=float(row.average_goals_for),
            average_goals_against=float(row.average_goals_against),
        )
        for row in rows
    )


def load_historical_import_batches(
    session: Session,
) -> tuple[HistoricalImportBatchInfo, ...]:
    batches = session.scalars(
        select(HistoricalImportBatch).order_by(
            HistoricalImportBatch.imported_at.desc(),
            HistoricalImportBatch.id.desc(),
        )
    )
    return tuple(
        HistoricalImportBatchInfo(
            imported_at=batch.imported_at,
            source_name=batch.source_name,
            source_url=batch.source_url,
            reuse_terms=batch.reuse_terms,
            file_sha256=batch.file_sha256,
            rows_seen=batch.rows_seen,
            imported_count=batch.imported_count,
            duplicate_count=batch.duplicate_count,
            invalid_count=batch.invalid_count,
        )
        for batch in batches
    )


def _match_filters(
    *,
    competition_name: str | None,
    season_name: str | None,
    team_name: str | None,
    team_name_exact: str | None,
    search_text: str,
    result: str | None,
    start_date: date | None,
    end_date: date | None,
    home_team,
    away_team,
    batch,
):
    criteria = []
    if competition_name:
        criteria.append(Competition.name == competition_name)
    if season_name:
        criteria.append(Season.name == season_name)
    if team_name:
        pattern = _contains_pattern(team_name)
        criteria.append(
            or_(
                home_team.name.ilike(pattern, escape="\\"),
                away_team.name.ilike(pattern, escape="\\"),
            )
        )
    if team_name_exact:
        criteria.append(
            or_(
                home_team.name == team_name_exact,
                away_team.name == team_name_exact,
            )
        )
    if search_text.strip():
        pattern = _contains_pattern(search_text.strip())
        criteria.append(
            or_(
                home_team.name.ilike(pattern, escape="\\"),
                away_team.name.ilike(pattern, escape="\\"),
                Competition.name.ilike(pattern, escape="\\"),
                Season.name.ilike(pattern, escape="\\"),
                batch.source_name.ilike(pattern, escape="\\"),
            )
        )
    if start_date is not None:
        criteria.append(HistoricalMatch.kickoff_date >= start_date)
    if end_date is not None:
        criteria.append(HistoricalMatch.kickoff_date <= end_date)
    if result == "HOME WIN":
        criteria.append(HistoricalMatch.home_score > HistoricalMatch.away_score)
    elif result == "DRAW":
        criteria.append(HistoricalMatch.home_score == HistoricalMatch.away_score)
    elif result == "AWAY WIN":
        criteria.append(HistoricalMatch.home_score < HistoricalMatch.away_score)
    return tuple(criteria)


def _contains_pattern(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def import_historical_csv(
    session: Session,
    file_path: str | Path,
    *,
    competition_name: str,
    country: str,
    season_name: str,
    season_start: date,
    season_end: date,
    source_name: str,
    source_url: str,
    reuse_terms: str,
) -> HistoricalImportResult:
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"Historical CSV file was not found: {path}")
    if season_start > season_end:
        raise ValueError("Season start date must not be after its end date.")
    for label, value, max_length in (
        ("competition", competition_name, 100),
        ("country", country, 100),
        ("season", season_name, 20),
        ("source name", source_name, 120),
    ):
        if not value.strip() or len(value.strip()) > max_length:
            raise ValueError(f"{label.title()} must contain 1-{max_length} characters.")
    if len(source_url) > 2048 or not source_url.strip().startswith(("https://", "http://")):
        raise ValueError("Source URL must be an HTTP(S) URL of at most 2048 characters.")
    if not reuse_terms.strip():
        raise ValueError(
            "Record the source reuse terms or permission basis before importing."
        )

    raw_bytes = path.read_bytes()
    if len(raw_bytes) > _MAX_CSV_BYTES:
        raise ValueError("Historical CSV exceeds the 20 MiB import limit.")
    file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
    existing_batch = session.scalar(
        select(HistoricalImportBatch).where(
            HistoricalImportBatch.file_sha256 == file_sha256
        )
    )
    if existing_batch is not None:
        return HistoricalImportResult(
            file_sha256=file_sha256,
            rows_seen=existing_batch.rows_seen,
            imported_count=existing_batch.imported_count,
            duplicate_count=existing_batch.duplicate_count,
            invalid_count=existing_batch.invalid_count,
            already_imported=True,
        )

    try:
        decoded = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError(
            "CSV must be UTF-8 encoded (a UTF-8 BOM is supported)."
        ) from error
    reader = csv.DictReader(io.StringIO(decoded, newline=""))
    headers = tuple((header or "").strip() for header in (reader.fieldnames or ()))
    if not headers:
        raise ValueError("CSV has no header row.")
    if len(headers) != len(set(headers)):
        raise ValueError("CSV contains duplicate column names.")
    missing_headers = set(_REQUIRED_HEADERS) - set(headers)
    if missing_headers:
        raise ValueError(
            "CSV is missing required columns: " + ", ".join(sorted(missing_headers))
        )
    reader.fieldnames = list(headers)

    parsed_rows: list[_ParsedHistoricalRow] = []
    issues: list[HistoricalImportIssue] = []
    rows_seen = 0
    for row in reader:
        rows_seen += 1
        row_number = reader.line_num
        try:
            parsed_rows.append(
                _parse_row(
                    row,
                    row_number,
                    season_start,
                    season_end,
                )
            )
        except ValueError as error:
            issues.append(HistoricalImportIssue(row_number, str(error)))

    competition = session.scalar(
        select(Competition).where(Competition.name == competition_name.strip())
    )
    if competition is None:
        competition = Competition(
            name=competition_name.strip(),
            country=country.strip(),
        )
        session.add(competition)
        session.flush()
    elif competition.country != country.strip():
        raise ValueError(
            f"Competition {competition.name} already belongs to "
            f"{competition.country}, not {country.strip()}."
        )

    season = session.scalar(
        select(Season).where(
            Season.competition_id == competition.id,
            Season.name == season_name.strip(),
        )
    )
    if season is None:
        season = Season(
            competition_id=competition.id,
            name=season_name.strip(),
            start_date=season_start,
            end_date=season_end,
        )
        session.add(season)
        session.flush()
    elif season.start_date != season_start or season.end_date != season_end:
        raise ValueError(
            f"Season {season.name} already exists with different date boundaries."
        )

    batch = HistoricalImportBatch(
        competition_id=competition.id,
        season_id=season.id,
        source_name=source_name.strip(),
        source_url=source_url.strip(),
        reuse_terms=reuse_terms.strip(),
        file_sha256=file_sha256,
        rows_seen=rows_seen,
        imported_count=0,
        duplicate_count=0,
        invalid_count=len(issues),
    )
    session.add(batch)
    session.flush()
    known_keys = set(session.scalars(select(HistoricalMatch.match_key)))
    input_keys: set[str] = set()
    imported_count = 0
    duplicate_count = 0
    team_cache: dict[str, Team] = {}

    for row in parsed_rows:
        match_key = _match_key(
            competition.name,
            season.name,
            row.kickoff_date,
            row.home_team,
            row.away_team,
        )
        if match_key in known_keys or match_key in input_keys:
            duplicate_count += 1
            continue
        input_keys.add(match_key)
        home_team = _get_team(
            session, team_cache, row.home_team, country.strip()
        )
        away_team = _get_team(
            session, team_cache, row.away_team, country.strip()
        )
        session.add(
            HistoricalMatch(
                match_key=match_key,
                source_batch_id=batch.id,
                source_row_number=row.row_number,
                season_id=season.id,
                home_team_id=home_team.id,
                away_team_id=away_team.id,
                kickoff_date=row.kickoff_date,
                kickoff_time=row.kickoff_time,
                home_score=row.home_score,
                away_score=row.away_score,
            )
        )
        imported_count += 1

    batch.imported_count = imported_count
    batch.duplicate_count = duplicate_count
    session.flush()
    return HistoricalImportResult(
        file_sha256=file_sha256,
        rows_seen=rows_seen,
        imported_count=imported_count,
        duplicate_count=duplicate_count,
        invalid_count=len(issues),
        already_imported=False,
        issues=tuple(issues),
    )


def _parse_row(
    row: dict[str, str | None],
    row_number: int,
    season_start: date,
    season_end: date,
) -> _ParsedHistoricalRow:
    kickoff_date = _parse_date(row.get("Date"))
    if not season_start <= kickoff_date <= season_end:
        raise ValueError("Match date falls outside the declared season.")
    home_team = _clean_team(row.get("HomeTeam"), "HomeTeam")
    away_team = _clean_team(row.get("AwayTeam"), "AwayTeam")
    if home_team.casefold() == away_team.casefold():
        raise ValueError("HomeTeam and AwayTeam must differ.")
    home_score = _parse_score(row.get("FTHG"), "FTHG")
    away_score = _parse_score(row.get("FTAG"), "FTAG")
    result = (row.get("FTR") or "").strip().upper()
    expected_result = (
        "H" if home_score > away_score else "A" if home_score < away_score else "D"
    )
    if result and result not in ("H", "D", "A"):
        raise ValueError("FTR must be H, D, or A when provided.")
    if result and result != expected_result:
        raise ValueError("FTR conflicts with FTHG and FTAG.")
    kickoff_time = _parse_time(row.get("Time"))
    return _ParsedHistoricalRow(
        row_number=row_number,
        kickoff_date=kickoff_date,
        kickoff_time=kickoff_time,
        home_team=home_team,
        away_team=away_team,
        home_score=home_score,
        away_score=away_score,
    )


def _parse_date(value: str | None) -> date:
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValueError("Date is required.")
    for date_format in ("%d/%m/%y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, date_format).date()
        except ValueError:
            continue
    raise ValueError(f"Unsupported match date: {cleaned!r}.")


def _parse_time(value: str | None) -> time | None:
    cleaned = (value or "").strip()
    if not cleaned:
        return None
    for time_format in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(cleaned, time_format).time()
        except ValueError:
            continue
    raise ValueError(f"Unsupported kick-off time: {cleaned!r}.")


def _parse_score(value: str | None, field_name: str) -> int:
    cleaned = (value or "").strip()
    if not re.fullmatch(r"\d{1,2}", cleaned):
        raise ValueError(f"{field_name} must be a whole-number score from 0 to 99.")
    return int(cleaned)


def _clean_team(value: str | None, field_name: str) -> str:
    cleaned = " ".join((value or "").split())
    if not cleaned or len(cleaned) > 100:
        raise ValueError(f"{field_name} must contain 1-100 characters.")
    return cleaned


def _match_key(
    competition: str,
    season: str,
    kickoff_date: date,
    home_team: str,
    away_team: str,
) -> str:
    normalized = "|".join(
        (
            competition.casefold().strip(),
            season.casefold().strip(),
            kickoff_date.isoformat(),
            " ".join(home_team.casefold().split()),
            " ".join(away_team.casefold().split()),
        )
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _get_team(
    session: Session,
    cache: dict[str, Team],
    team_name: str,
    country: str,
) -> Team:
    key = f"{team_name.casefold()}|{country.casefold()}"
    if key not in cache:
        team = session.scalar(
            select(Team).where(Team.name == team_name, Team.country == country)
        )
        if team is None:
            team = Team(name=team_name, country=country)
            session.add(team)
            session.flush()
        cache[key] = team
    return cache[key]
