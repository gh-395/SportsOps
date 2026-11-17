from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStackedWidget,
)

from sportsops.database.engine import create_fresh_demo_database
from sportsops.database.service import DatabaseService
from sportsops.ui.historical_workspace import HistoricalWorkspace
from sportsops.ui.market_workspace import MarketWorkspace


class MainWindow(QMainWindow):
    def __init__(
        self,
        database: DatabaseService,
        database_label: str,
        allow_demo_reset: bool = False,
    ) -> None:
        super().__init__()
        self.setWindowTitle("SportsOps | Sports Trading & Historical Data")
        self.resize(1540, 930)
        self.setMinimumSize(1080, 680)
        self.database = database
        self.database_label = database_label
        self.allow_demo_reset = allow_demo_reset

        self.market_workspace = MarketWorkspace(database, self)
        self.historical_workspace = HistoricalWorkspace(database, self)
        self._build_central_widget()
        self._build_menus()
        self.statusBar().showMessage(
            f"{database_label} | Markets are simulated | Historical results are source-derived"
        )

    def _build_central_widget(self) -> None:
        self.root_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.root_splitter.setObjectName("rootSplitter")
        self.navigation = QListWidget(self.root_splitter)
        self.navigation.setObjectName("workspaceNavigation")
        self.navigation.setMinimumWidth(185)
        self.navigation.setMaximumWidth(330)
        self.navigation.setSelectionMode(
            QListWidget.SelectionMode.SingleSelection
        )
        markets = QListWidgetItem("MARKETS WORKSPACE")
        markets.setToolTip("Sports trading operations — simulated markets only")
        historical = QListWidgetItem("HISTORICAL DATA")
        historical.setToolTip(
            "Football results database and SQL-backed analytics"
        )
        self.navigation.addItem(markets)
        self.navigation.addItem(historical)

        self.workspace_stack = QStackedWidget(self.root_splitter)
        self.workspace_stack.addWidget(self.market_workspace)
        self.workspace_stack.addWidget(self.historical_workspace)
        self.root_splitter.addWidget(self.navigation)
        self.root_splitter.addWidget(self.workspace_stack)
        self.root_splitter.setSizes([225, 1315])
        self.navigation.currentRowChanged.connect(
            self._on_navigation_changed
        )
        self.setCentralWidget(self.root_splitter)
        self.navigation.setCurrentRow(0)

    def _build_menus(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        exit_action = QAction("E&xit", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        reset_demo_action = QAction("Create fresh demo profile...", self)
        reset_demo_action.setToolTip(
            "Create a separate clean demo database without changing this database."
        )
        reset_demo_action.setEnabled(self.allow_demo_reset)
        reset_demo_action.triggered.connect(self._create_fresh_demo_profile)
        file_menu.addAction(reset_demo_action)

        view_menu = self.menuBar().addMenu("&View")
        find_action = QAction("&Find", self)
        find_action.setShortcut("Ctrl+F")
        find_action.triggered.connect(self._focus_search)
        view_menu.addAction(find_action)
        reset_layout_action = QAction("&Reset panel layout", self)
        reset_layout_action.setShortcut("Ctrl+0")
        reset_layout_action.triggered.connect(self._reset_splitters)
        view_menu.addAction(reset_layout_action)

        markets_menu = self.menuBar().addMenu("&Workspaces")
        markets_action = QAction("&Markets Workspace", self)
        markets_action.setShortcut("Ctrl+Shift+M")
        markets_action.triggered.connect(self._show_markets)
        markets_menu.addAction(markets_action)
        historical_action = QAction("&Historical Data", self)
        historical_action.setShortcut("Ctrl+Shift+H")
        historical_action.triggered.connect(self._show_historical)
        markets_menu.addAction(historical_action)

        help_menu = self.menuBar().addMenu("&Help")
        about_action = QAction("&About SportsOps", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    def _on_navigation_changed(self, row: int) -> None:
        if row == 0:
            self._show_markets()
        elif row == 1:
            self._show_historical()

    def _show_markets(self) -> None:
        if self.navigation.currentRow() != 0:
            self.navigation.setCurrentRow(0)
            return
        self.workspace_stack.setCurrentWidget(self.market_workspace)
        self.market_workspace.refresh()
        self.statusBar().showMessage(
            "MARKETS | Simulated odds only | No bookmaker or betting connection"
        )

    def _show_historical(self) -> None:
        if self.navigation.currentRow() != 1:
            self.navigation.setCurrentRow(1)
            return
        self.workspace_stack.setCurrentWidget(self.historical_workspace)
        self.historical_workspace.refresh()
        self.statusBar().showMessage(
            "HISTORICAL DATA | Imported football results | No simulated odds or markets"
        )

    def _focus_search(self) -> None:
        if self.workspace_stack.currentWidget() is self.market_workspace:
            self.market_workspace.market_search.setFocus()
        else:
            self.historical_workspace.search_input.setFocus()

    def _reset_splitters(self) -> None:
        self.root_splitter.setSizes([225, 1315])
        if hasattr(self.market_workspace, "market_splitter"):
            self.market_workspace.market_splitter.setSizes([850, 330])
        self.historical_workspace.workspace_splitter.setSizes([520, 285])

    def _create_fresh_demo_profile(self) -> None:
        if not self.allow_demo_reset:
            return
        answer = QMessageBox.question(
            self,
            "Create a fresh demo profile",
            "SportsOps will create a new, clean synthetic demo database and use "
            "it on the next launch.\n\n"
            "The current database will not be changed or deleted. Imported "
            "historical results and other data in it will remain in that "
            "database, but will not appear in the fresh demo profile.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            database_path = create_fresh_demo_database()
        except (OSError, RuntimeError, ValueError) as error:
            QMessageBox.critical(
                self,
                "Could not create demo profile",
                f"The current database was not changed.\n\n{error}",
            )
            return
        QMessageBox.information(
            self,
            "Demo profile ready",
            "A fresh demo database was created:\n"
            f"{database_path}\n\n"
            "Your previous database remains unchanged. SportsOps will now "
            "close; launch it again to use the fresh demo profile.",
        )
        self.close()

    def _show_about(self) -> None:
        QMessageBox.information(
            self,
            "About SportsOps",
            "SportsOps\nNative desktop application for simulated sports "
            "trading operations and historical football data.\n"
            "No live feeds or real betting services are connected.",
        )
