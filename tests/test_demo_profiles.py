import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from sportsops.database.engine import (
    create_database_engine,
    create_fresh_demo_database,
    default_database_url,
    initialize_database,
)
from sportsops.database.models import (
    Competition,
    HistoricalImportBatch,
    HistoricalMatch,
    MatchRecord,
    Market,
    Season,
    Team,
)


class DemoProfileTests(unittest.TestCase):
    def test_data_directory_can_be_overridden_for_isolated_launches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_directory = Path(temporary_directory) / "isolated"
            with patch.dict(
                os.environ,
                {
                    "SPORTSOPS_DATA_DIR": str(data_directory),
                    "SPORTSOPS_DATABASE_URL": "",
                },
            ):
                database_path = Path(make_url(default_database_url()).database)
            self.assertEqual(database_path.parent, data_directory)

    def test_fresh_demo_profile_preserves_previous_database_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            with patch.dict(
                os.environ,
                {
                    "LOCALAPPDATA": temporary_directory,
                    "SPORTSOPS_DATABASE_URL": "",
                },
            ):
                original_url = default_database_url()
                original_path = Path(make_url(original_url).database)
                original_engine = create_database_engine(original_url)
                initialize_database(original_engine)
                with Session(original_engine) as session, session.begin():
                    competition = session.scalar(select(Competition))
                    season = session.scalar(select(Season))
                    teams = session.scalars(select(Team).limit(2)).all()
                    batch = HistoricalImportBatch(
                        competition_id=competition.id,
                        season_id=season.id,
                        source_name="Local test source",
                        source_url="https://example.test/results.csv",
                        reuse_terms="Test-only permitted fixture",
                        file_sha256="a" * 64,
                        rows_seen=1,
                        imported_count=1,
                        duplicate_count=0,
                        invalid_count=0,
                    )
                    session.add(batch)
                    session.flush()
                    session.add(
                        HistoricalMatch(
                            match_key="b" * 64,
                            source_batch_id=batch.id,
                            source_row_number=2,
                            season_id=season.id,
                            home_team_id=teams[0].id,
                            away_team_id=teams[1].id,
                            kickoff_date=date(2025, 9, 1),
                            home_score=1,
                            away_score=0,
                        )
                    )
                original_engine.dispose()
                original_contents = original_path.read_bytes()

                fresh_path = create_fresh_demo_database()

                self.assertTrue(original_path.is_file())
                self.assertEqual(original_path.read_bytes(), original_contents)
                self.assertEqual(
                    Path(make_url(default_database_url()).database),
                    fresh_path,
                )

                previous_engine = create_database_engine(original_url)
                fresh_engine = create_database_engine(default_database_url())
                try:
                    with Session(previous_engine) as session:
                        self.assertEqual(
                            session.scalar(
                                select(func.count()).select_from(HistoricalMatch)
                            ),
                            1,
                        )
                    with Session(fresh_engine) as session:
                        self.assertEqual(
                            session.scalar(
                                select(func.count()).select_from(MatchRecord)
                            ),
                            7,
                        )
                        self.assertEqual(
                            session.scalar(
                                select(func.count()).select_from(Market)
                            ),
                            21,
                        )
                        self.assertEqual(
                            session.scalar(
                                select(func.count()).select_from(HistoricalMatch)
                            ),
                            0,
                        )
                finally:
                    previous_engine.dispose()
                    fresh_engine.dispose()

    def test_fresh_demo_profile_refuses_custom_database_url(self) -> None:
        with patch.dict(
            os.environ,
            {"SPORTSOPS_DATABASE_URL": "sqlite:///custom.sqlite3"},
        ):
            with self.assertRaisesRegex(RuntimeError, "default local SQLite"):
                create_fresh_demo_database()

    def test_active_profile_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            data_directory = Path(temporary_directory) / "SportsOps"
            data_directory.mkdir()
            (data_directory / "active_demo_database.txt").write_text(
                "..\\outside.sqlite3",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "LOCALAPPDATA": temporary_directory,
                    "SPORTSOPS_DATABASE_URL": "",
                },
            ):
                with self.assertRaisesRegex(RuntimeError, "Invalid active demo"):
                    default_database_url()


if __name__ == "__main__":
    unittest.main()
