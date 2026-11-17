import argparse
from datetime import date
from pathlib import Path

from sportsops.database.engine import create_database_engine, initialize_database
from sportsops.database.service import DatabaseService


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Import user-supplied historical football results from CSV. "
            "No dataset is downloaded by this command."
        )
    )
    parser.add_argument("csv_file", type=Path, help="Local CSV path")
    parser.add_argument("--competition", required=True)
    parser.add_argument("--country", required=True)
    parser.add_argument("--season", required=True)
    parser.add_argument(
        "--season-start", required=True, type=date.fromisoformat, metavar="YYYY-MM-DD"
    )
    parser.add_argument(
        "--season-end", required=True, type=date.fromisoformat, metavar="YYYY-MM-DD"
    )
    parser.add_argument("--source-name", required=True)
    parser.add_argument("--source-url", required=True)
    parser.add_argument(
        "--reuse-terms",
        required=True,
        help=(
            "Your recorded reuse permission/terms; the importer does not "
            "verify licensing."
        ),
    )
    arguments = parser.parse_args()

    engine = create_database_engine()
    try:
        initialize_database(engine)
        result = DatabaseService(engine).import_historical_csv(
            arguments.csv_file,
            competition_name=arguments.competition,
            country=arguments.country,
            season_name=arguments.season,
            season_start=arguments.season_start,
            season_end=arguments.season_end,
            source_name=arguments.source_name,
            source_url=arguments.source_url,
            reuse_terms=arguments.reuse_terms,
        )
    finally:
        engine.dispose()

    print(f"CSV SHA-256: {result.file_sha256}")
    print(f"Rows seen: {result.rows_seen}")
    print(f"Imported: {result.imported_count}")
    print(f"Duplicates skipped: {result.duplicate_count}")
    print(f"Invalid rows skipped: {result.invalid_count}")
    if result.already_imported:
        print("This exact CSV file was imported previously; no rows changed.")
    for issue in result.issues:
        print(f"Row {issue.row_number}: {issue.message}")
    return 2 if result.invalid_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
