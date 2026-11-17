import os
import sys
from pathlib import Path

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import QMessageBox
from sqlalchemy.exc import SQLAlchemyError

from sportsops.database.engine import (
    create_database_engine,
    initialize_database,
)
from sportsops.database.service import DatabaseService
from sportsops.data.historical_bundle import import_bundled_historical_data
from sportsops.ui.main_window import MainWindow


def main() -> int:
    startup_error = _configure_data_directory()
    app = QApplication([sys.argv[0]])
    if startup_error:
        QMessageBox.critical(
            None,
            "SportsOps startup error",
            f"Could not configure the application data directory.\n\n{startup_error}",
        )
        return 2
    app.setApplicationName("SportsOps")
    app.setOrganizationName("SportsOps")
    app.setStyle("Fusion")




    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#1b1f24"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#e6e9ed"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#22272e"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#292f37"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#e6e9ed"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#303942"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#e6e9ed"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#275b7a"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#303942"))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#f0f2f4"))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor("#929da8"))
    palette.setColor(QPalette.ColorRole.Light, QColor("#46515d"))
    palette.setColor(QPalette.ColorRole.Mid, QColor("#363e47"))
    palette.setColor(QPalette.ColorRole.Dark, QColor("#171a1e"))

    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Text,
        QColor("#78838e"),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.ButtonText,
        QColor("#78838e"),
    )

    app.setPalette(palette)




    app.setStyleSheet(
        """
        QWidget {
            background-color: #1b1f24;
            color: #e6e9ed;
            font-size: 9pt;
        }
        QMainWindow, QMenuBar, QMenu, QStatusBar {
            background-color: #171a1e;
        }
        QMenuBar::item {
            padding: 5px 9px;
        }
        QMenuBar::item:selected, QMenu::item:selected {
            background-color: #303942;
        }
        QLineEdit, QComboBox, QListWidget, QTableView, QPlainTextEdit {
            background-color: #22272e;
            border: 1px solid #3a424c;
            selection-background-color: #275b7a;
        }
        QPushButton {
            background-color: #303942;
            border: 1px solid #46515d;
            padding: 4px 10px;
        }
        QPushButton:hover {
            background-color: #3a4855;
        }
        QPushButton:disabled {
            color: #78838e;
            background-color: #252a30;
        }
        QTabBar::tab {
            background-color: #252a30;
            border: 1px solid #3a424c;
            padding: 5px 12px;
        }
        QTabBar::tab:selected {
            color: #8fc7e8;
            background-color: #303942;
        }
        QHeaderView::section {
            background-color: #292f37;
            color: #c8d0d8;
            border: 0;
            border-right: 1px solid #3a424c;
            border-bottom: 1px solid #3a424c;
            padding: 5px 7px;
            font-weight: bold;
        }
        QListWidget::item, QTableView::item {
            padding: 4px;
        }
        QListWidget::item:selected {
            background-color: #275b7a;
        }
        QSplitter::handle {
            background-color: #363e47;
        }
        QToolTip {
            color: #f0f2f4;
            background-color: #303942;
            border: 1px solid #59636e;
        }
        """
    )

    engine = None
    try:
        engine = create_database_engine()
        initialize_database(engine)
        if engine.dialect.name == "sqlite":
            import_bundled_historical_data(DatabaseService(engine))
    except (KeyError, OSError, SQLAlchemyError, ValueError) as error:
        if engine is not None:
            engine.dispose()
        QMessageBox.critical(
            None,
            "SportsOps database startup error",
            f"SportsOps could not initialize its database or historical bundle.\n\n{error}",
        )
        return 2
    database_label = (
        "SQLite demo"
        if engine.dialect.name == "sqlite"
        else f"{engine.dialect.name.upper()} database"
    )
    window = MainWindow(
        DatabaseService(engine),
        database_label,
        allow_demo_reset=(
            engine.dialect.name == "sqlite"
            and not os.environ.get("SPORTSOPS_DATABASE_URL")
            and not os.environ.get("SPORTSOPS_DATA_DIR")
        ),
    )
    app.aboutToQuit.connect(engine.dispose)
    window.show()
    return app.exec()


def _configure_data_directory() -> str | None:
    arguments = sys.argv[1:]
    qt_arguments: list[str] = []
    data_directory: str | None = None
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--data-dir":
            if data_directory is not None or index + 1 == len(arguments):
                return "--data-dir must be supplied exactly once with a path."
            data_directory = arguments[index + 1]
            index += 2
            continue
        if argument.startswith("--data-dir="):
            if data_directory is not None or not argument.partition("=")[2]:
                return "--data-dir must be supplied exactly once with a path."
            data_directory = argument.partition("=")[2]
            index += 1
            continue
        qt_arguments.append(argument)
        index += 1

    if data_directory is not None:
        if os.environ.get("SPORTSOPS_DATABASE_URL"):
            return "--data-dir cannot be combined with SPORTSOPS_DATABASE_URL."
        os.environ["SPORTSOPS_DATA_DIR"] = str(
            Path(data_directory).expanduser().resolve()
        )
    sys.argv = [sys.argv[0], *qt_arguments]
    return None
