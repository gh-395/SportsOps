import argparse
import csv
import hashlib
import json
import urllib.request
from datetime import date
from pathlib import Path


SEASONS = ("2018-19", "2019-20", "2020-21", "2021-22", "2022-23", "2023-24")
LEAGUES = (
    ("en.1", "English Premier League", "England"),
    ("de.1", "German Bundesliga", "Germany"),
    ("es.1", "Spanish La Liga", "Spain"),
    ("it.1", "Italian Serie A", "Italy"),
)
SOURCE_ROOT = "https://raw.githubusercontent.com/openfootball/football.json/master"
SOURCE_NAME = "OpenFootball football.json"
REUSE_TERMS = (
    "The OpenFootball football.json README dedicates its schema, data, and scripts "
    "to the public domain and states they may be used without restrictions. "
    "https://github.com/openfootball/football.json#license (reviewed 2026-11-15)."
)


def build_bundle(output_dir: Path, source_dir: Path | None) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    datasets = []
    total_importable = 0

    for season in SEASONS:
        start_year = int(season[:4])
        season_start = date(start_year, 7, 1)
        season_end = date(start_year + 1, 8, 31)
        for code, competition, country in LEAGUES:
            source_url = f"{SOURCE_ROOT}/{season}/{code}.json"
            source_path = (
                source_dir / f"{season}-{code}.json"
                if source_dir is not None
                else None
            )
            if source_path is None:
                with urllib.request.urlopen(source_url, timeout=30) as response:
                    source_bytes = response.read()
            else:
                source_bytes = source_path.read_bytes()

            source_hash = hashlib.sha256(source_bytes).hexdigest()
            payload = json.loads(source_bytes)
            matches = payload.get("matches")
            if not isinstance(matches, list):
                raise ValueError(f"{source_url} does not contain a matches list.")

            csv_path = output_dir / f"{season}_{code.replace('.', '-')}.csv"
            imported_count = 0
            incomplete_count = 0
            with csv_path.open("w", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                writer.writerow(
                    ("Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR")
                )
                for source_row, match in enumerate(matches, start=1):
                    score = match.get("score") or {}
                    full_time = score.get("ft")
                    if not isinstance(full_time, list) or len(full_time) != 2:
                        incomplete_count += 1
                        continue
                    home_score, away_score = full_time
                    if (
                        not isinstance(home_score, int)
                        or isinstance(home_score, bool)
                        or not isinstance(away_score, int)
                        or isinstance(away_score, bool)
                        or min(home_score, away_score) < 0
                    ):
                        raise ValueError(
                            f"Invalid full-time score at {source_url}, row {source_row}."
                        )
                    match_date = date.fromisoformat(match["date"])
                    if not season_start <= match_date <= season_end:
                        raise ValueError(
                            f"Out-of-season date at {source_url}, row {source_row}."
                        )
                    home_team = str(match.get("team1", "")).strip()
                    away_team = str(match.get("team2", "")).strip()
                    if not home_team or not away_team or home_team == away_team:
                        raise ValueError(
                            f"Invalid teams at {source_url}, row {source_row}."
                        )
                    result = (
                        "H"
                        if home_score > away_score
                        else "A"
                        if home_score < away_score
                        else "D"
                    )
                    writer.writerow(
                        (
                            match_date.isoformat(),
                            match.get("time", ""),
                            home_team,
                            away_team,
                            home_score,
                            away_score,
                            result,
                        )
                    )
                    imported_count += 1

            if imported_count == 0:
                raise ValueError(f"No completed matches found in {source_url}.")
            total_importable += imported_count
            datasets.append(
                {
                    "file": csv_path.name,
                    "competition": competition,
                    "country": country,
                    "season": season,
                    "season_start": season_start.isoformat(),
                    "season_end": season_end.isoformat(),
                    "source_name": SOURCE_NAME,
                    "source_url": source_url,
                    "reuse_terms": REUSE_TERMS,
                    "source_sha256": source_hash,
                    "source_rows": len(matches),
                    "completed_matches": imported_count,
                    "incomplete_rows_excluded": incomplete_count,
                }
            )

    manifest = {
        "dataset": "OpenFootball football.json historical results",
        "source_homepage": "https://github.com/openfootball/football.json",
        "license_notice": REUSE_TERMS,
        "seasons": list(SEASONS),
        "competitions": [league[1] for league in LEAGUES],
        "completed_match_count": total_importable,
        "datasets": datasets,
    }
    (output_dir / "bundle_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return total_importable


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the packaged OpenFootball CSV dataset from public JSON."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        help=(
            "Optional directory containing {season}-{league}.json files; "
            "otherwise the official raw GitHub files are downloaded."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "sportsops"
        / "data"
        / "historical",
    )
    arguments = parser.parse_args()
    count = build_bundle(arguments.output_dir, arguments.source_dir)
    print(f"Built {count:,} completed historical match records.")
    print(f"Output directory: {arguments.output_dir.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
