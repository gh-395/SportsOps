import csv
import os
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sportsops.database.engine import create_database_engine, initialize_database
from sportsops.database.models import (
    HistoricalImportBatch,
    HistoricalMatch,
    Market,
    MatchRecord,
)
from sportsops.database.service import DatabaseService
from sportsops.data.historical_bundle import import_bundled_historical_data
from sportsops.ui.main_window import MainWindow


class HistoricalImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.engine = create_database_engine("sqlite://")
        initialize_database(self.engine)
        self.database = DatabaseService(self.engine)
        self.temporary_directory = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()
        self.engine.dispose()

    def _write_csv(self, rows: list[dict[str, str]]) -> Path:
        path = Path(self.temporary_directory.name) / "historical.csv"
        with path.open("w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=(
                    "Date",
                    "Time",
                    "HomeTeam",
                    "AwayTeam",
                    "FTHG",
                    "FTAG",
                    "FTR",
                ),
            )
            writer.writeheader()
            writer.writerows(rows)
        return path

    def _import(self, path: Path):
        return self.database.import_historical_csv(
            path,
            competition_name="Example League",
            country="Example Country",
            season_name="2025/26",
            season_start=date(2025, 8, 1),
            season_end=date(2026, 6, 30),
            source_name="User-provided example CSV",
            source_url="https://example.test/data.csv",
            reuse_terms="Local test fixture generated in-memory for this test.",
        )

    def test_imports_realistic_csv_shapes_with_auditable_provenance(self) -> None:
        path = self._write_csv(
            [
                {
                    "Date": "15/08/25",
                    "Time": "20:00",
                    "HomeTeam": "  North   City ",
                    "AwayTeam": "South Town",
                    "FTHG": "2",
                    "FTAG": "1",
                    "FTR": "H",
                },
                {
                    "Date": "16/08/2025",
                    "Time": "",
                    "HomeTeam": "East Club",
                    "AwayTeam": "West Club",
                    "FTHG": "0",
                    "FTAG": "0",
                    "FTR": "D",
                },
            ]
        )
        result = self._import(path)
        self.assertEqual((result.rows_seen, result.imported_count), (2, 2))
        self.assertEqual((result.duplicate_count, result.invalid_count), (0, 0))
        matches = self.database.load_historical_matches()
        self.assertEqual(len(matches), 2)
        north_match = next(match for match in matches if match.home_team == "North City")
        self.assertEqual(north_match.source_name, "User-provided example CSV")
        self.assertEqual(north_match.source_url, "https://example.test/data.csv")
        self.assertEqual(
            north_match.reuse_terms,
            "Local test fixture generated in-memory for this test.",
        )
        self.assertEqual(north_match.result, "HOME WIN")
        east_match = next(match for match in matches if match.home_team == "East Club")
        self.assertEqual(east_match.result, "DRAW")
        self.assertIsNone(east_match.kickoff_time)
        with Session(self.engine) as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(MatchRecord)), 7
            )
            self.assertEqual(
                session.scalar(select(func.count()).select_from(Market)), 21
            )
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(HistoricalImportBatch)
                ),
                1,
            )

    def test_repeat_import_and_duplicate_fixtures_are_idempotent(self) -> None:
        path = self._write_csv(
            [
                {
                    "Date": "15/08/25",
                    "Time": "20:00",
                    "HomeTeam": "North City",
                    "AwayTeam": "South Town",
                    "FTHG": "2",
                    "FTAG": "1",
                    "FTR": "H",
                },
                {
                    "Date": "15/08/25",
                    "Time": "20:30",
                    "HomeTeam": " north   city ",
                    "AwayTeam": "south town",
                    "FTHG": "3",
                    "FTAG": "2",
                    "FTR": "H",
                },
            ]
        )
        first = self._import(path)
        self.assertEqual((first.imported_count, first.duplicate_count), (1, 1))
        repeated_file = self._import(path)
        self.assertTrue(repeated_file.already_imported)
        self.assertEqual(len(self.database.load_historical_matches()), 1)

        updated_path = Path(self.temporary_directory.name) / "second.csv"
        updated_path.write_bytes(path.read_bytes() + b"\n")
        second = self._import(updated_path)
        self.assertEqual((second.imported_count, second.duplicate_count), (0, 2))
        self.assertEqual(len(self.database.load_historical_matches()), 1)

    def test_invalid_rows_are_reported_and_do_not_create_matches(self) -> None:
        path = self._write_csv(
            [
                {
                    "Date": "15/08/25",
                    "Time": "20:00",
                    "HomeTeam": "North City",
                    "AwayTeam": "South Town",
                    "FTHG": "2",
                    "FTAG": "0",
                    "FTR": "A",
                },
                {
                    "Date": "15/08/25",
                    "Time": "20:00",
                    "HomeTeam": "Same FC",
                    "AwayTeam": "Same FC",
                    "FTHG": "-1",
                    "FTAG": "0",
                    "FTR": "H",
                },
                {
                    "Date": "15/08/25",
                    "Time": "20:00",
                    "HomeTeam": "North City",
                    "AwayTeam": "South Town",
                    "FTHG": "-1",
                    "FTAG": "0",
                    "FTR": "H",
                },
            ]
        )
        result = self._import(path)
        self.assertEqual(result.imported_count, 0)
        self.assertEqual(result.invalid_count, 3)
        self.assertTrue(any("FTR conflicts" in issue.message for issue in result.issues))
        self.assertTrue(any("must differ" in issue.message for issue in result.issues))
        self.assertTrue(any("whole-number score" in issue.message for issue in result.issues))
        self.assertEqual(self.database.load_historical_matches(), ())

    def test_csv_schema_metadata_and_season_range_are_required(self) -> None:
        path = Path(self.temporary_directory.name) / "missing.csv"
        path.write_text("Date,HomeTeam,AwayTeam\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "missing required columns"):
            self._import(path)

        valid = self._write_csv(
            [
                {
                    "Date": "15/08/25",
                    "Time": "",
                    "HomeTeam": "North City",
                    "AwayTeam": "South Town",
                    "FTHG": "1",
                    "FTAG": "0",
                    "FTR": "H",
                }
            ]
        )
        with self.assertRaisesRegex(ValueError, "reuse terms"):
            self.database.import_historical_csv(
                valid,
                competition_name="Example League",
                country="Example Country",
                season_name="2025/26",
                season_start=date(2025, 8, 1),
                season_end=date(2026, 6, 30),
                source_name="Example",
                source_url="https://example.test/data.csv",
                reuse_terms="",
            )
        out_of_season = self.database.import_historical_csv(
            valid,
            competition_name="Example League",
            country="Example Country",
            season_name="2025/26",
            season_start=date(2025, 9, 1),
            season_end=date(2026, 6, 30),
            source_name="Example",
            source_url="https://example.test/data.csv",
            reuse_terms="Test terms",
        )
        self.assertEqual(out_of_season.invalid_count, 1)
        self.assertTrue(
            any("outside the declared season" in issue.message
                for issue in out_of_season.issues)
        )
        self.assertEqual(self.database.load_historical_matches(), ())

    def test_historical_records_reject_invalid_scores_and_same_team(self) -> None:
        path = self._write_csv(
            [
                {
                    "Date": "15/08/25",
                    "Time": "",
                    "HomeTeam": "North City",
                    "AwayTeam": "South Town",
                    "FTHG": "1",
                    "FTAG": "0",
                    "FTR": "H",
                }
            ]
        )
        self._import(path)
        with Session(self.engine) as session:
            match = session.scalar(select(HistoricalMatch))
            match.home_score = -1
            with self.assertRaises(IntegrityError):
                session.flush()
            session.rollback()
            match = session.scalar(select(HistoricalMatch))
            match.away_team_id = match.home_team_id
            with self.assertRaises(IntegrityError):
                session.flush()
            session.rollback()


class HistoricalBundleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.engine = create_database_engine("sqlite://")
        initialize_database(self.engine)
        self.database = DatabaseService(self.engine)

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_bundle_import_is_repeatable_and_queries_are_sql_backed(self) -> None:
        first_import = import_bundled_historical_data(self.database)
        self.assertEqual(len(first_import), 24)
        self.assertEqual(sum(result.imported_count for result in first_import), 8676)
        self.assertEqual(sum(result.invalid_count for result in first_import), 0)

        page_one, total = self.database.load_historical_match_page(
            page=0, page_size=50
        )
        page_two, _ = self.database.load_historical_match_page(
            page=1, page_size=50
        )
        self.assertEqual(total, 8676)
        self.assertEqual(len(page_one), 50)
        self.assertEqual(len(page_two), 50)
        self.assertEqual(
            [match.kickoff_date for match in page_one],
            sorted((match.kickoff_date for match in page_one), reverse=True),
        )
        self.assertTrue(
            {match.historical_id for match in page_one}.isdisjoint(
                match.historical_id for match in page_two
            )
        )
        self.assertEqual(page_one[0].source_name, "OpenFootball football.json")
        self.assertIn("public domain", page_one[0].reuse_terms)

        league_filters = {
            "competition_name": "English Premier League",
            "season_name": "2022-23",
        }
        league_page, league_total = self.database.load_historical_match_page(
            **league_filters, page_size=500
        )
        self.assertEqual(league_total, 380)
        self.assertEqual(len(league_page), 380)

        city_page, city_total = self.database.load_historical_match_page(
            **league_filters,
            team_name="Manchester City",
            page_size=100,
        )
        self.assertEqual(city_total, 38)
        self.assertTrue(
            all(
                any(
                    "Manchester City" in team
                    for team in (match.home_team, match.away_team)
                )
                for match in city_page
            )
        )
        date_filter = city_page[0].kickoff_date
        same_day, same_day_total = self.database.load_historical_match_page(
            **league_filters,
            team_name="Manchester City",
            start_date=date_filter,
            end_date=date_filter,
            page_size=100,
        )
        self.assertEqual(same_day_total, len(same_day))
        self.assertTrue(all(match.kickoff_date == date_filter for match in same_day))

        result_counts = {}
        for result in ("HOME WIN", "DRAW", "AWAY WIN"):
            _, result_counts[result] = self.database.load_historical_match_page(
                **league_filters, result=result, page_size=500
            )
        self.assertEqual(sum(result_counts.values()), 380)

        standings = self.database.load_historical_team_summary(**league_filters)
        self.assertEqual(len(standings), 20)
        champion = standings[0]
        self.assertEqual(champion.team, "Manchester City FC")
        self.assertEqual(
            (champion.points, champion.goals_for, champion.goals_against),
            (89, 94, 33),
        )
        self.assertEqual((champion.home_wins, champion.away_wins), (17, 11))
        self.assertAlmostEqual(champion.average_goals_for, 94 / 38)

        batches = self.database.load_historical_import_batches()
        self.assertEqual(len(batches), 24)
        self.assertTrue(all(batch.source_url.startswith("https://") for batch in batches))

        window = MainWindow(self.database, "SQLite test")
        try:
            workspace = window.historical_workspace
            self.assertEqual(workspace.model.rowCount(), 250)
            self.assertTrue(workspace.descending)
            first_page_ids = {
                workspace.model.match_at(row).historical_id
                for row in range(workspace.model.rowCount())
            }
            self.assertTrue(workspace.next_button.isEnabled())
            workspace.next_button.click()
            self.assertEqual(workspace.page, 1)
            self.assertEqual(workspace.model.rowCount(), 250)
            second_page_ids = {
                workspace.model.match_at(row).historical_id
                for row in range(workspace.model.rowCount())
            }
            self.assertTrue(first_page_ids.isdisjoint(second_page_ids))
            workspace.previous_button.click()
            self.assertEqual(workspace.page, 0)
        finally:
            window.close()

        repeated_import = import_bundled_historical_data(self.database)
        self.assertEqual(len(repeated_import), 24)
        self.assertTrue(all(result.already_imported for result in repeated_import))
        _, unchanged_total = self.database.load_historical_match_page(page_size=1)
        self.assertEqual(unchanged_total, 8676)


class HistoricalWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.engine = create_database_engine("sqlite://")
        initialize_database(self.engine)
        self.database = DatabaseService(self.engine)
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.window = MainWindow(self.database, "SQLite test")

    def tearDown(self) -> None:
        self.window.close()
        self.temporary_directory.cleanup()
        self.engine.dispose()

    def test_historical_workspace_is_separate_and_searchable(self) -> None:
        workspace = self.window.historical_workspace
        self.assertEqual(workspace.model.rowCount(), 0)
        self.window.navigation.setCurrentRow(1)
        self.assertIs(self.window.workspace_stack.currentWidget(), workspace)
        self.assertEqual(self.window.navigation.count(), 2)

        path = Path(self.temporary_directory.name) / "historical.csv"
        path.write_text(
            "Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR\n"
            "15/08/25,20:00,North City,South Town,2,1,H\n"
            "16/08/25,15:00,East Club,West Club,0,0,D\n",
            encoding="utf-8",
        )
        self.database.import_historical_csv(
            path,
            competition_name="Example League",
            country="Example Country",
            season_name="2025/26",
            season_start=date(2025, 8, 1),
            season_end=date(2026, 6, 30),
            source_name="User-provided example CSV",
            source_url="https://example.test/data.csv",
            reuse_terms="Local test fixture generated in-memory for this test.",
        )
        workspace.refresh()
        workspace.competition_filter.setCurrentText("Example League")
        workspace.season_filter.setCurrentText("2025/26")
        workspace.team_filter.setCurrentText("North City")
        workspace.result_filter.setCurrentText("HOME WIN")
        self.assertEqual(workspace.model.rowCount(), 1)
        workspace.table.selectRow(0)
        match = workspace.model.match_at(0)
        self.assertEqual((match.home_team, match.away_team, match.score), (
            "North City", "South Town", "2 - 1"
        ))
        self.assertNotIn("Source", workspace.model.HEADERS)
        self.assertNotIn("example.test/data.csv", workspace.match_detail_metadata.text())
        self.assertFalse(hasattr(workspace, "match_detail_provenance"))
        self.assertFalse(hasattr(workspace, "match_detail_statistics"))
        self.assertEqual(workspace.performance_model.rowCount(), 1)
        self.assertEqual(workspace.performance_model.data(
            workspace.performance_model.index(0, 8)
        ), "3")
        self.assertEqual(workspace.import_model.rowCount(), 1)
        import_batch = workspace.import_model.index(0, 1)
        self.assertEqual(
            workspace.import_model.data(import_batch),
            "User-provided example CSV",
        )
        self.assertIn(
            "example.test/data.csv",
            workspace.import_model.data(import_batch, Qt.ItemDataRole.ToolTipRole),
        )
        self.assertIn(
            "Local test fixture generated in-memory for this test.",
            workspace.import_model.data(import_batch, Qt.ItemDataRole.ToolTipRole),
        )
        workspace.table.sortByColumn(4, Qt.SortOrder.AscendingOrder)
        self.assertEqual(workspace.sort_key, "home_team")
        self.assertEqual(len(self.database.load_markets()), 49)


class HistoricalFacetedFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.engine = create_database_engine("sqlite://")
        initialize_database(self.engine)
        self.database = DatabaseService(self.engine)
        self.temporary_directory = tempfile.TemporaryDirectory()
        self._import_fixtures()
        self.window = MainWindow(self.database, "SQLite test")
        self.workspace = self.window.historical_workspace

    def tearDown(self) -> None:
        self.window.close()
        self.temporary_directory.cleanup()
        self.engine.dispose()

    def _import_csv(
        self,
        filename: str,
        competition: str,
        season: str,
        country: str,
        rows: list[dict[str, str]],
    ) -> None:
        path = Path(self.temporary_directory.name) / filename
        with path.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=(
                    "Date",
                    "Time",
                    "HomeTeam",
                    "AwayTeam",
                    "FTHG",
                    "FTAG",
                    "FTR",
                ),
            )
            writer.writeheader()
            writer.writerows(rows)
        start_year = int(season[:4])
        self.database.import_historical_csv(
            path,
            competition_name=competition,
            country=country,
            season_name=season,
            season_start=date(start_year, 7, 1),
            season_end=date(start_year + 1, 6, 30),
            source_name=f"Test source {filename}",
            source_url=f"https://example.test/{filename}",
            reuse_terms="Local test data for dynamic filter assertions.",
        )

    def _import_fixtures(self) -> None:
        fixtures = (
            (
                "alpha-2023.csv",
                "Alpha League",
                "2023-24",
                "Alpha Country",
                "2023-08-01",
                "Alpha Star",
                "A North",
                "1",
                "0",
                "H",
            ),
            (
                "alpha-2024.csv",
                "Alpha League",
                "2024-25",
                "Alpha Country",
                "2024-08-01",
                "Alpha Star",
                "A South",
                "2",
                "0",
                "H",
            ),
            (
                "beta-2023.csv",
                "Beta League",
                "2023-24",
                "Beta Country",
                "2023-08-02",
                "Beta Star",
                "B North",
                "0",
                "0",
                "D",
            ),
            (
                "beta-2024.csv",
                "Beta League",
                "2024-25",
                "Beta Country",
                "2024-08-02",
                "Beta Star",
                "B South",
                "0",
                "2",
                "A",
            ),
        )
        for (
            filename,
            competition,
            season,
            country,
            match_date,
            home,
            away,
            home_score,
            away_score,
            result,
        ) in fixtures:
            self._import_csv(
                filename,
                competition,
                season,
                country,
                [
                    {
                        "Date": match_date,
                        "Time": "",
                        "HomeTeam": home,
                        "AwayTeam": away,
                        "FTHG": home_score,
                        "FTAG": away_score,
                        "FTR": result,
                    }
                ],
            )

    def _select(self, combo, value: str) -> None:
        index = combo.findData(value)
        self.assertGreaterEqual(index, 0, f"{value!r} should be available")
        combo.setCurrentIndex(index)

    def _values(self, combo) -> set[str | None]:
        return {combo.itemData(index) for index in range(combo.count())}

    def _reset(self) -> None:
        self.workspace.reset_filters_button.click()

    def test_each_facet_works_alone_and_omits_its_own_constraint(self) -> None:
        workspace = self.workspace
        self._select(workspace.competition_filter, "Alpha League")
        self.assertEqual(workspace.total_matches, 2)
        self.assertEqual(
            self._values(workspace.season_filter),
            {None, "2023-24", "2024-25"},
        )
        self.assertEqual(
            self._values(workspace.team_filter),
            {None, "Alpha Star", "A North", "A South"},
        )
        self.assertEqual(
            self._values(workspace.competition_filter),
            {None, "Alpha League", "Beta League"},
        )

        self._reset()
        self._select(workspace.season_filter, "2023-24")
        self.assertEqual(workspace.total_matches, 2)
        self.assertEqual(
            self._values(workspace.competition_filter),
            {None, "Alpha League", "Beta League"},
        )
        self.assertEqual(
            self._values(workspace.team_filter),
            {None, "Alpha Star", "A North", "Beta Star", "B North"},
        )

        self._reset()
        self._select(workspace.team_filter, "A North")
        self.assertEqual(workspace.total_matches, 1)
        self.assertEqual(
            self._values(workspace.competition_filter),
            {None, "Alpha League"},
        )
        self.assertEqual(
            self._values(workspace.season_filter), {None, "2023-24"}
        )
        self.assertIn("A North", self._values(workspace.team_filter))

    def test_every_two_facet_pair_and_all_three_facets_work_together(self) -> None:
        workspace = self.workspace
        self._select(workspace.competition_filter, "Alpha League")
        self._select(workspace.season_filter, "2023-24")
        self.assertEqual(workspace.total_matches, 1)
        self.assertEqual(
            self._values(workspace.team_filter),
            {None, "Alpha Star", "A North"},
        )

        self._reset()
        self._select(workspace.competition_filter, "Alpha League")
        self._select(workspace.team_filter, "Alpha Star")
        self.assertEqual(workspace.total_matches, 2)
        self.assertEqual(
            self._values(workspace.season_filter),
            {None, "2023-24", "2024-25"},
        )

        self._reset()
        self._select(workspace.season_filter, "2023-24")
        self._select(workspace.team_filter, "Alpha Star")
        self.assertEqual(workspace.total_matches, 1)
        self.assertEqual(
            self._values(workspace.competition_filter),
            {None, "Alpha League"},
        )

        self._reset()
        self._select(workspace.competition_filter, "Alpha League")
        self._select(workspace.season_filter, "2023-24")
        self._select(workspace.team_filter, "A North")
        self.assertEqual(workspace.total_matches, 1)
        self.assertEqual(
            workspace.performance_model.rowCount(), 1
        )
        self.assertEqual(workspace.standings_model.rowCount(), 1)

    def test_selection_order_is_supported_and_only_incompatible_facet_resets(self) -> None:
        workspace = self.workspace
        self._select(workspace.team_filter, "Alpha Star")
        self._select(workspace.season_filter, "2023-24")
        self._select(workspace.competition_filter, "Alpha League")
        self.assertEqual(workspace.total_matches, 1)

        self._reset()
        self._select(workspace.season_filter, "2023-24")
        self._select(workspace.competition_filter, "Alpha League")
        self._select(workspace.team_filter, "Alpha Star")
        self.assertEqual(workspace.total_matches, 1)

        self._reset()
        self._select(workspace.competition_filter, "Alpha League")
        self._select(workspace.season_filter, "2023-24")
        self._select(workspace.team_filter, "A North")
        workspace.competition_filter.addItem("Beta League", "Beta League")
        self._select(workspace.competition_filter, "Beta League")
        self.assertEqual(
            workspace.competition_filter.currentData(), "Beta League"
        )
        self.assertEqual(workspace.season_filter.currentData(), "2023-24")
        self.assertIsNone(workspace.team_filter.currentData())
        self.assertEqual(workspace.total_matches, 1)

    def test_team_facet_uses_exact_selected_team(self) -> None:
        self._import_csv(
            "similar-team.csv",
            "North League",
            "2023-24",
            "North Country",
            [
                {
                    "Date": "2023-08-03",
                    "Time": "",
                    "HomeTeam": "A North Extended",
                    "AwayTeam": "North Visitors",
                    "FTHG": "1",
                    "FTAG": "0",
                    "FTR": "H",
                }
            ],
        )
        self.workspace.refresh()
        self._select(self.workspace.team_filter, "A North")
        self.assertEqual(self.workspace.total_matches, 1)
        self.assertEqual(
            self._values(self.workspace.competition_filter),
            {None, "Alpha League"},
        )
        self.assertEqual(
            self.workspace.performance_model.rowCount(),
            1,
        )
        self.assertEqual(
            self.workspace.performance_model.data(
                self.workspace.performance_model.index(0, 1)
            ),
            "A North",
        )

    def test_zero_results_are_empty_and_reset_filters_clears_all_controls(self) -> None:
        workspace = self.workspace
        self._select(workspace.competition_filter, "Alpha League")
        self._select(workspace.season_filter, "2023-24")
        workspace.result_filter.setCurrentText("AWAY WIN")
        self.assertEqual(workspace.total_matches, 0)
        self.assertEqual(workspace.model.rowCount(), 0)
        self.assertEqual(workspace.performance_model.rowCount(), 0)
        self.assertEqual(workspace.standings_model.rowCount(), 0)
        self.assertIn("no matching completed matches", workspace.performance_label.text())

        workspace.search_input.setText("Alpha")
        workspace.start_date_enabled.setChecked(True)
        workspace.end_date_enabled.setChecked(True)
        workspace.page = 3
        workspace.reset_filters_button.click()
        self.assertIsNone(workspace.competition_filter.currentData())
        self.assertIsNone(workspace.season_filter.currentData())
        self.assertIsNone(workspace.team_filter.currentData())
        self.assertIsNone(workspace.result_filter.currentData())
        self.assertFalse(workspace.start_date_enabled.isChecked())
        self.assertFalse(workspace.end_date_enabled.isChecked())
        self.assertEqual(workspace.search_input.text(), "")
        self.assertEqual(workspace.page, 0)
        self.assertEqual(workspace.total_matches, 4)

    def test_filter_change_resets_pagination(self) -> None:
        rows = []
        first_date = date(2023, 8, 1)
        for offset in range(101):
            rows.append(
                {
                    "Date": (first_date + timedelta(days=offset)).isoformat(),
                    "Time": "",
                    "HomeTeam": "Paged Home",
                    "AwayTeam": "Paged Away",
                    "FTHG": "1",
                    "FTAG": "0",
                    "FTR": "H",
                }
            )
        self._import_csv(
            "paged-league.csv",
            "Paged League",
            "2023-24",
            "Paged Country",
            rows,
        )
        self.workspace.refresh()
        self.workspace.page_size_filter.setCurrentText("100")
        self.assertEqual(self.workspace.total_matches, 105)
        self.workspace.next_button.click()
        self.assertEqual(self.workspace.page, 1)
        self._select(self.workspace.competition_filter, "Alpha League")
        self.assertEqual(self.workspace.page, 0)
        self.assertEqual(self.workspace.total_matches, 2)
        self.assertEqual(self.workspace.page_label.text(), "Page 1 of 1")


if __name__ == "__main__":
    unittest.main()
