import argparse
import re
import sys
from pathlib import Path

from sqlalchemy import Engine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sportsops.database.engine import create_database_engine, initialize_database


def _queries() -> list[tuple[int, str, str]]:
    script = (ROOT / "sql" / "analysis_queries.sql").read_text(encoding="utf-8")
    sections = re.split(r"(?m)^-- Query (\d+) - (.+?)\r?$", script)
    queries: list[tuple[int, str, str]] = []
    for offset in range(1, len(sections), 3):
        number = int(sections[offset])
        title = sections[offset + 1]
        query = sections[offset + 2].strip()
        if not query.endswith(";"):
            raise ValueError(f"SQL analysis query {number} is missing a semicolon.")
        executable = re.sub(r"(?m)^\s*--.*?$", "", query).lstrip().upper()
        if not executable.startswith(("SELECT", "WITH")):
            raise ValueError(f"SQL analysis query {number} is not read-only.")
        queries.append((number, title, query))
    if len(queries) != 23:
        raise ValueError(f"Expected 23 documented queries, found {len(queries)}.")
    return queries


def _execute(engine: Engine, external_database: bool) -> None:
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            if external_database:
                if engine.dialect.name == "postgresql":
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                elif engine.dialect.name == "sqlite":
                    connection.exec_driver_sql("PRAGMA query_only = ON")
            output_rows = {}
            for number, title, query in _queries():
                result = connection.exec_driver_sql(query)
                rows = result.fetchall()
                output_rows[number] = rows
                print(f"Query {number:02}: PASS | {len(rows)} row(s) | {title}")
            print("\nSample output (synthetic SportsOps demo rows; not history):")
            for number in (2, 23):
                print(f"Query {number:02}:")
                for row in output_rows[number]:
                    print(" | ".join(str(value) for value in row))
            if not output_rows[18]:
                print("Query 18: no historical rows imported in the demo database.")
        finally:
            transaction.rollback()


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Execute every read-only portfolio SQL query. Without a URL, "
            "an isolated in-memory SQLite database is populated with labeled "
            "SportsOps demo rows."
        )
    )
    parser.add_argument(
        "--database-url",
        help=(
            "Optional already-initialized database URL. Queries run in a "
            "read-only transaction; this command does not create schema."
        ),
    )
    args = parser.parse_args()

    engine = create_database_engine(args.database_url or "sqlite://")
    try:
        if not args.database_url:
            initialize_database(engine)
        _execute(engine, external_database=bool(args.database_url))
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
