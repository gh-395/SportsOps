import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sportsops.database.engine import create_database_engine, initialize_database
from sportsops.database.models import MarketSelection, MatchRecord
from sportsops.database.service import DatabaseService
from sportsops.ui.main_window import MainWindow


class MainWindowSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.engine = create_database_engine("sqlite://")
        initialize_database(self.engine)
        self.window = MainWindow(DatabaseService(self.engine), "SQLite test")

    def tearDown(self) -> None:
        self.window.close()
        self.engine.dispose()

    def test_only_two_workspaces_are_available_and_markets_is_default(self) -> None:
        self.assertEqual(self.window.navigation.count(), 2)
        self.assertEqual(
            [self.window.navigation.item(row).text() for row in range(2)],
            ["MARKETS WORKSPACE", "HISTORICAL DATA"],
        )
        self.assertEqual(self.window.navigation.currentRow(), 0)
        self.assertIs(
            self.window.workspace_stack.currentWidget(),
            self.window.market_workspace,
        )
        self.assertEqual(self.window.workspace_stack.count(), 2)
        self.assertEqual(self.window.market_workspace.market_model.rowCount(), 49)
        self.assertEqual(self.window.root_splitter.count(), 2)

    def test_navigation_keeps_historical_data_and_markets_separate(self) -> None:
        self.window.navigation.setCurrentRow(1)
        self.assertIs(
            self.window.workspace_stack.currentWidget(),
            self.window.historical_workspace,
        )
        self.assertEqual(self.window.historical_workspace.model.rowCount(), 0)
        self.assertEqual(self.window.historical_workspace.total_matches, 0)

        self.window.navigation.setCurrentRow(0)
        self.assertIs(
            self.window.workspace_stack.currentWidget(),
            self.window.market_workspace,
        )
        self.assertEqual(self.window.market_workspace.market_model.rowCount(), 49)
        self.assertEqual(self.window.navigation.count(), 2)

    def test_demo_match_and_market_records_remain_available_to_trading(self) -> None:
        with Session(self.engine) as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(MatchRecord)), 7
            )
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(MarketSelection)
                ),
                49,
            )
