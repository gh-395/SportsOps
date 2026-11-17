import os
from pathlib import Path
from uuid import uuid4

from sqlalchemy import Engine, create_engine, event, func, select
from sqlalchemy.orm import Session

from sportsops.database.models import Base, MatchRecord
from sportsops.database.migrations import migrate_schema
from sportsops.database.seed import seed_demo_data, seed_demo_markets
from sportsops.database.validation import record_issues, validate_database
from sportsops.database.simulation_repository import initialize_phase6_data


def default_database_url() -> str:
    configured_url = os.environ.get("SPORTSOPS_DATABASE_URL")
    if configured_url:
        return configured_url
    data_directory = _user_data_directory()
    data_directory.mkdir(parents=True, exist_ok=True)
    database_name = "sportsops_demo.sqlite3"
    active_profile = data_directory / "active_demo_database.txt"
    if active_profile.exists():
        database_name = active_profile.read_text(encoding="utf-8").strip()
        if (
            not database_name
            or Path(database_name).name != database_name
            or Path(database_name).suffix.lower() != ".sqlite3"
        ):
            raise RuntimeError(
                f"Invalid active demo database profile file: {active_profile}"
            )
    database_path = (data_directory / database_name).as_posix()
    return f"sqlite:///{database_path}"


def create_fresh_demo_database() -> Path:
    if os.environ.get("SPORTSOPS_DATABASE_URL"):
        raise RuntimeError(
            "A fresh demo profile can only be created when SportsOps uses its "
            "default local SQLite database. SPORTSOPS_DATABASE_URL is set."
        )

    data_directory = _user_data_directory()
    data_directory.mkdir(parents=True, exist_ok=True)
    database_path = data_directory / (
        f"sportsops_demo_reset_{uuid4().hex}.sqlite3"
    )
    with database_path.open("xb"):
        pass

    engine = create_database_engine(f"sqlite:///{database_path.as_posix()}")
    try:
        initialize_database(engine)
    finally:
        engine.dispose()

    active_profile = data_directory / "active_demo_database.txt"
    temporary_profile = data_directory / f".active_demo_database_{uuid4().hex}.tmp"
    temporary_profile.write_text(database_path.name, encoding="utf-8")
    os.replace(temporary_profile, active_profile)
    return database_path


def _user_data_directory() -> Path:
    configured_data_directory = os.environ.get("SPORTSOPS_DATA_DIR")
    if configured_data_directory:
        return Path(configured_data_directory)
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "SportsOps"
    return Path.home() / ".sportsops"


def create_database_engine(database_url: str | None = None) -> Engine:
    engine = create_engine(database_url or default_database_url())
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def enable_sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()
    return engine


def initialize_database(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    migrate_schema(engine)
    with Session(engine) as session:
        with session.begin():
            match_count = session.scalar(select(func.count()).select_from(MatchRecord))
            if match_count == 0:
                seed_demo_data(session)
            seed_demo_markets(session)
            initialize_phase6_data(session)
            record_issues(session, validate_database(session))
