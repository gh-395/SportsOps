from math import ceil

from PySide6.QtCore import QModelIndex, QSignalBlocker, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from sportsops.database.service import DatabaseService
from sportsops.models.historical import HistoricalMatchInfo
from sportsops.ui.historical_import_dialog import HistoricalImportDialog
from sportsops.ui.historical_table_models import (
    HistoricalImportBatchTableModel,
    HistoricalMatchTableModel,
    HistoricalTeamSummaryTableModel,
)


class HistoricalWorkspace(QWidget):
    PAGE_SIZES = (100, 250, 500)

    def __init__(self, database: DatabaseService, parent=None) -> None:
        super().__init__(parent)
        self.database = database
        self.page = 0
        self.page_size = 250
        self.sort_key = "kickoff_date"
        self.descending = True
        self.total_matches = 0
        self._facet_order = ["competition", "season", "team"]
        self.model = HistoricalMatchTableModel(parent=self)
        self.performance_model = HistoricalTeamSummaryTableModel(
            standings=False, parent=self
        )
        self.standings_model = HistoricalTeamSummaryTableModel(
            standings=True, parent=self
        )
        self.import_model = HistoricalImportBatchTableModel(self)
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(250)
        self.search_timer.timeout.connect(self.reload)
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(5)

        heading = QHBoxLayout()
        title = QLabel("HISTORICAL FOOTBALL DATABASE")
        title.setStyleSheet("font-size: 11pt; font-weight: bold; color: #8fc7e8;")
        heading.addWidget(title)
        self.dataset_label = QLabel(
            "SOURCE-DERIVED RESULTS | SEPARATE FROM SIMULATED TRADING"
        )
        self.dataset_label.setStyleSheet("color: #72d6a0; font-weight: bold;")
        heading.addWidget(self.dataset_label)
        heading.addStretch(1)
        self.import_button = QPushButton("Import CSV...")
        self.import_button.clicked.connect(self._open_import_dialog)
        heading.addWidget(self.import_button)
        layout.addLayout(heading)

        filter_row = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setObjectName("historicalSearch")
        self.search_input.setPlaceholderText(
            "Search team, competition, season, or source..."
        )
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._schedule_reload)
        filter_row.addWidget(self.search_input, 3)

        self.competition_filter = QComboBox()
        self.competition_filter.currentIndexChanged.connect(
            lambda: self._facet_changed("competition")
        )
        filter_row.addWidget(self.competition_filter, 2)

        self.season_filter = QComboBox()
        self.season_filter.currentIndexChanged.connect(
            lambda: self._facet_changed("season")
        )
        filter_row.addWidget(self.season_filter, 1)

        self.team_filter = QComboBox()
        self.team_filter.setObjectName("historicalTeamFilter")
        self.team_filter.currentIndexChanged.connect(
            lambda: self._facet_changed("team")
        )
        filter_row.addWidget(self.team_filter, 2)

        self.result_filter = QComboBox()
        self.result_filter.addItem("All results", None)
        for result in ("HOME WIN", "DRAW", "AWAY WIN"):
            self.result_filter.addItem(result, result)
        self.result_filter.currentIndexChanged.connect(self._filters_changed)
        filter_row.addWidget(self.result_filter, 1)
        layout.addLayout(filter_row)

        date_row = QHBoxLayout()
        self.start_date_enabled = QCheckBox("From")
        self.start_date_enabled.toggled.connect(self._filters_changed)
        date_row.addWidget(self.start_date_enabled)
        self.start_date_filter = QDateEdit()
        self.start_date_filter.setCalendarPopup(True)
        self.start_date_filter.setEnabled(False)
        self.start_date_filter.dateChanged.connect(self._filters_changed)
        date_row.addWidget(self.start_date_filter)
        self.end_date_enabled = QCheckBox("To")
        self.end_date_enabled.toggled.connect(self._filters_changed)
        date_row.addWidget(self.end_date_enabled)
        self.end_date_filter = QDateEdit()
        self.end_date_filter.setCalendarPopup(True)
        self.end_date_filter.setEnabled(False)
        self.end_date_filter.dateChanged.connect(self._filters_changed)
        date_row.addWidget(self.end_date_filter)
        self.start_date_enabled.toggled.connect(self.start_date_filter.setEnabled)
        self.end_date_enabled.toggled.connect(self.end_date_filter.setEnabled)
        date_row.addSpacing(12)
        date_row.addWidget(QLabel("Rows / page"))
        self.page_size_filter = QComboBox()
        for page_size in self.PAGE_SIZES:
            self.page_size_filter.addItem(str(page_size), page_size)
        self.page_size_filter.setCurrentText(str(self.page_size))
        self.page_size_filter.currentIndexChanged.connect(self._page_size_changed)
        date_row.addWidget(self.page_size_filter)
        self.reset_filters_button = QPushButton("Reset Filters")
        self.reset_filters_button.clicked.connect(self._clear_filters)
        date_row.addWidget(self.reset_filters_button)
        date_row.addStretch(1)
        self.match_count_label = QLabel("0 historical matches")
        date_row.addWidget(self.match_count_label)
        layout.addLayout(date_row)

        upper = QWidget(self)
        upper_layout = QVBoxLayout(upper)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        self.table = QTableView(upper)
        self.table.setObjectName("historicalMatchTable")
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((95, 55, 155, 78, 165, 65, 165, 85)):
            self.table.setColumnWidth(column, width)
        header.setStretchLastSection(True)
        self.table.selectionModel().currentRowChanged.connect(
            self._on_match_selected
        )
        self.model.sortRequested.connect(self._sort_changed)
        upper_layout.addWidget(self.table, 1)

        pager = QHBoxLayout()
        self.previous_button = QPushButton("Previous")
        self.previous_button.clicked.connect(self._previous_page)
        pager.addWidget(self.previous_button)
        self.next_button = QPushButton("Next")
        self.next_button.clicked.connect(self._next_page)
        pager.addWidget(self.next_button)
        self.page_label = QLabel("Page 0 of 0")
        pager.addWidget(self.page_label)
        pager.addStretch(1)
        upper_layout.addLayout(pager)

        self.detail_tabs = QTabWidget(self)
        self.detail_tabs.addTab(self._build_match_details(), "MATCH DETAILS")
        self.detail_tabs.addTab(self._build_team_analysis(), "TEAM PERFORMANCE")
        self.detail_tabs.addTab(self._build_standings(), "LEAGUE STANDINGS")
        self.detail_tabs.addTab(self._build_import_history(), "IMPORTS & PROVENANCE")

        self.workspace_splitter = QSplitter(Qt.Orientation.Vertical, self)
        self.workspace_splitter.setObjectName("historicalWorkspaceSplitter")
        self.workspace_splitter.addWidget(upper)
        self.workspace_splitter.addWidget(self.detail_tabs)
        self.workspace_splitter.setSizes([520, 285])
        layout.addWidget(self.workspace_splitter, 1)
        self.table.horizontalHeader().setSortIndicator(
            0, Qt.SortOrder.DescendingOrder
        )
        self.table.setSortingEnabled(True)

    def _build_match_details(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.match_detail_heading = QLabel("Select a historical match")
        self.match_detail_heading.setStyleSheet(
            "font-size: 12pt; font-weight: bold; color: #8fc7e8;"
        )
        self.match_detail_heading.setWordWrap(True)
        layout.addWidget(self.match_detail_heading)
        self.match_detail_metadata = QLabel(
            "Historical results are kept in a dedicated relational domain."
        )
        self.match_detail_metadata.setWordWrap(True)
        layout.addWidget(self.match_detail_metadata)
        layout.addStretch(1)
        return panel

    def _build_team_analysis(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.performance_label = QLabel(
            "Select a competition and season to calculate SQL-backed team "
            "home/away records and goal averages."
        )
        self.performance_label.setWordWrap(True)
        layout.addWidget(self.performance_label)
        table = QTableView(panel)
        table.setObjectName("historicalTeamPerformanceTable")
        table.setModel(self.performance_model)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table, 1)
        return panel

    def _build_standings(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.standings_label = QLabel(
            "League standings use three points for a win and one for a draw, "
            "ordered by points, goal difference, and goals scored."
        )
        self.standings_label.setWordWrap(True)
        layout.addWidget(self.standings_label)
        table = QTableView(panel)
        table.setObjectName("historicalStandingsTable")
        table.setModel(self.standings_model)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table, 1)
        return panel

    def _build_import_history(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.import_history_label = QLabel(
            "Imported source URLs, reuse notes, CSV fingerprints, and row counts "
            "are recorded for auditability. Hover a row for provenance details."
        )
        self.import_history_label.setWordWrap(True)
        layout.addWidget(self.import_history_label)
        table = QTableView(panel)
        table.setObjectName("historicalImportBatchTable")
        table.setModel(self.import_model)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        table.verticalHeader().hide()
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(table, 1)
        return panel

    def refresh(self) -> None:
        try:
            self._refresh_facet_options()
            self.import_model.replace_batches(
                self.database.load_historical_import_batches()
            )
            self.reload()
        except SQLAlchemyError as error:
            self._show_database_error("Could not load historical data", error)

    def reload(self) -> None:
        start_date = (
            self.start_date_filter.date().toPython()
            if self.start_date_enabled.isChecked()
            else None
        )
        end_date = (
            self.end_date_filter.date().toPython()
            if self.end_date_enabled.isChecked()
            else None
        )
        if start_date is not None and end_date is not None and start_date > end_date:
            self._show_database_error(
                "Invalid date range",
                ValueError("The From date must not be after the To date."),
            )
            return
        team_name = self.team_filter.currentData()
        try:
            matches, self.total_matches = self.database.load_historical_match_page(
                competition_name=self.competition_filter.currentData(),
                season_name=self.season_filter.currentData(),
                team_name_exact=team_name or None,
                search_text=self.search_input.text(),
                result=self.result_filter.currentData(),
                start_date=start_date,
                end_date=end_date,
                page=self.page,
                page_size=self.page_size,
                sort_key=self.sort_key,
                descending=self.descending,
            )
            self.model.replace_matches(matches)
            self._update_pagination()
            self._update_analytics(team_name, start_date, end_date)
            self._show_selected_match(None)
        except (SQLAlchemyError, ValueError) as error:
            self._show_database_error("Could not query historical data", error)

    def _update_analytics(self, team_name, start_date, end_date) -> None:
        competition = self.competition_filter.currentData()
        season = self.season_filter.currentData()
        try:
            rows = self.database.load_historical_team_summary(
                competition_name=competition,
                season_name=season,
                team_name_exact=team_name,
                start_date=start_date,
                end_date=end_date,
                result=self.result_filter.currentData(),
                search_text=self.search_input.text(),
            )
        except SQLAlchemyError as error:
            self._show_database_error("Could not calculate team performance", error)
            return
        self.performance_model.replace_rows(rows)
        scope_parts = [value for value in (competition, season, team_name) if value]
        scope = " | ".join(scope_parts) or "All historical results"
        if start_date or end_date:
            scope += " | filtered date range"
        if self.result_filter.currentData():
            scope += f" | {self.result_filter.currentData()}"
        if self.search_input.text().strip():
            scope += " | text search"
        if not rows:
            scope += " | no matching completed matches"
        self.performance_label.setText(
            f"{scope} | SQL aggregation of match results; no statistics are inferred."
        )
        if competition and season:
            self.standings_model.replace_rows(rows)
            self.standings_label.setText(
                f"{scope} | 3 points for a win, 1 for a draw; tie-breakers: "
                "goal difference, then goals scored."
            )
        else:
            self.standings_model.replace_rows(())
            self.standings_label.setText(
                "Select both a competition and season to calculate league "
                "standings. Team performance above reflects the active filters."
            )

    def _update_pagination(self) -> None:
        pages = max(1, ceil(self.total_matches / self.page_size))
        if self.total_matches:
            first = self.page * self.page_size + 1
            last = min((self.page + 1) * self.page_size, self.total_matches)
            self.match_count_label.setText(
                f"{first:,}-{last:,} of {self.total_matches:,} historical matches"
            )
        else:
            self.match_count_label.setText("0 historical matches")
        self.page_label.setText(f"Page {self.page + 1} of {pages}")
        self.previous_button.setEnabled(self.page > 0)
        self.next_button.setEnabled(self.page + 1 < pages)
        self.dataset_label.setText(
            f"{self.total_matches:,} FILTERED HISTORICAL RESULTS | "
            "SEPARATE FROM SIMULATED TRADING"
        )

    def _replace_combo(
        self,
        combo: QComboBox,
        all_label: str,
        values: tuple[str, ...],
        selected: str | None = None,
    ) -> None:
        blocker = QSignalBlocker(combo)
        combo.clear()
        combo.addItem(all_label, None)
        for value in values:
            combo.addItem(value, value)
        index = combo.findData(selected) if selected else 0
        combo.setCurrentIndex(index if index >= 0 else 0)
        del blocker

    def _current_facet_selections(self) -> dict[str, str | None]:
        return {
            "competition": self.competition_filter.currentData(),
            "season": self.season_filter.currentData(),
            "team": self.team_filter.currentData(),
        }

    def _refresh_facet_options(self, changed_facet: str | None = None) -> None:
        selections = self._current_facet_selections()
        if changed_facet is not None:
            order = [changed_facet] + [
                facet for facet in self._facet_order if facet != changed_facet
            ]
        else:
            order = list(self._facet_order)

        retained: dict[str, str] = {}
        filter_parameters = {
            "competition": "competition_name",
            "season": "season_name",
            "team": "team_name",
        }
        for facet in order:
            value = selections[facet]
            if value is None:
                continue
            options = self.database.load_historical_facet_options(
                facet,
                **{
                    filter_parameters[name]: selected
                    for name, selected in retained.items()
                }
            )
            if value in options:
                retained[facet] = value

        options = self.database.load_historical_filter_options(
            **{
                filter_parameters[name]: selected
                for name, selected in retained.items()
            }
        )
        self._replace_combo(
            self.competition_filter,
            "All competitions",
            options.competitions,
            retained.get("competition"),
        )
        self._replace_combo(
            self.season_filter,
            "All seasons",
            options.seasons,
            retained.get("season"),
        )
        self._replace_combo(
            self.team_filter,
            "All teams",
            options.teams,
            retained.get("team"),
        )
        self._facet_order = order

    def _facet_changed(self, facet: str) -> None:
        self._facet_order = [facet] + [
            item for item in self._facet_order if item != facet
        ]
        try:
            self._refresh_facet_options(changed_facet=facet)
        except SQLAlchemyError as error:
            self._show_database_error("Could not update historical filters", error)
            return
        self.page = 0
        self.reload()

    def _filters_changed(self, *_args) -> None:
        self.page = 0
        self.reload()

    def _schedule_reload(self, _text: str) -> None:
        self.page = 0
        self.search_timer.start()

    def _sort_changed(self, sort_key: str, descending: bool) -> None:
        self.sort_key = sort_key
        self.descending = descending
        self.page = 0
        self.reload()

    def _page_size_changed(self) -> None:
        value = self.page_size_filter.currentData()
        if value:
            self.page_size = int(value)
        self.page = 0
        self.reload()

    def _previous_page(self) -> None:
        if self.page > 0:
            self.page -= 1
            self.reload()

    def _next_page(self) -> None:
        if (self.page + 1) * self.page_size < self.total_matches:
            self.page += 1
            self.reload()

    def _clear_filters(self) -> None:
        controls = (
            self.search_input,
            self.competition_filter,
            self.season_filter,
            self.team_filter,
            self.result_filter,
            self.start_date_enabled,
            self.end_date_enabled,
        )
        blockers = [QSignalBlocker(control) for control in controls]
        self.search_input.clear()
        self.competition_filter.setCurrentIndex(0)
        self.season_filter.setCurrentIndex(0)
        self.team_filter.setCurrentIndex(0)
        self.result_filter.setCurrentIndex(0)
        self.start_date_enabled.setChecked(False)
        self.end_date_enabled.setChecked(False)
        blockers.clear()
        self.start_date_filter.setEnabled(self.start_date_enabled.isChecked())
        self.end_date_filter.setEnabled(self.end_date_enabled.isChecked())
        self.search_timer.stop()
        try:
            self._facet_order = ["competition", "season", "team"]
            self._refresh_facet_options()
        except SQLAlchemyError as error:
            self._show_database_error("Could not reset historical filters", error)
            return
        self.page = 0
        self.reload()

    def _on_match_selected(
        self, current: QModelIndex, _previous: QModelIndex
    ) -> None:
        if current.isValid() and current.row() < self.model.rowCount():
            self._show_selected_match(self.model.match_at(current.row()))

    def _show_selected_match(self, match: HistoricalMatchInfo | None) -> None:
        if match is None:
            self.match_detail_heading.setText("Select a historical match")
            self.match_detail_metadata.setText(
                "Historical results are kept in a dedicated relational domain."
            )
            return
        self.match_detail_heading.setText(
            f"{match.home_team}  {match.score}  {match.away_team}"
        )
        time_text = (
            match.kickoff_time.strftime("%H:%M")
            if match.kickoff_time is not None
            else "not provided"
        )
        self.match_detail_metadata.setText(
            f"{match.competition} | {match.season} | {match.kickoff_date.isoformat()} "
            f"{time_text} | Result: {match.result}"
        )

    def _open_import_dialog(self) -> None:
        dialog = HistoricalImportDialog(self)
        if dialog.exec() != HistoricalImportDialog.DialogCode.Accepted:
            return
        try:
            result = self.database.import_historical_csv(**dialog.import_details())
            self.refresh()
        except (OSError, SQLAlchemyError, ValueError) as error:
            QMessageBox.critical(
                self,
                "Historical import failed",
                f"No successful import could be confirmed.\n\n{error}",
            )
            return
        message = (
            f"Rows read: {result.rows_seen}\n"
            f"Imported: {result.imported_count}\n"
            f"Duplicates: {result.duplicate_count}\n"
            f"Invalid: {result.invalid_count}"
        )
        if result.invalid_count:
            issue_lines = "\n".join(
                f"Row {issue.row_number}: {issue.message}"
                for issue in result.issues[:8]
            )
            if len(result.issues) > 8:
                issue_lines += f"\n...and {len(result.issues) - 8} more issue(s)."
            QMessageBox.warning(
                self,
                "Historical import completed with validation issues",
                message + ("\n\n" + issue_lines if issue_lines else ""),
            )
        else:
            QMessageBox.information(
                self, "Historical import complete", message
            )

    def _show_database_error(self, title: str, error: Exception) -> None:
        QMessageBox.critical(self, title, str(error))
