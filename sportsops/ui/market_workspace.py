from decimal import Decimal

from PySide6.QtCore import QModelIndex, QSortFilterProxyModel, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)
from sqlalchemy.exc import SQLAlchemyError

from sportsops.database.service import DatabaseService
from sportsops.models.trading import MarketRow, RiskThresholds
from sportsops.services.market_simulation import MarketSimulationController
from sportsops.ui.market_models import (
    MarketFilterProxyModel,
    MarketTableModel,
    OddsHistoryTableModel,
    TradingAuditTableModel,
    TradingRiskFilterProxyModel,
    TradingRiskTableModel,
    SimulationEventTableModel,
    _format_timestamp,
    market_type_label,
)


class MarketWorkspace(QWidget):
    activityLogged = Signal(str, str)

    def __init__(self, database: DatabaseService, parent=None) -> None:
        super().__init__(parent)
        self.database = database
        self.simulation = MarketSimulationController(database)
        self._selected_market_row: MarketRow | None = None

        self.market_model = MarketTableModel(parent=self)
        self.market_proxy_model = MarketFilterProxyModel(self)
        self.market_proxy_model.setSourceModel(self.market_model)
        self.history_model = OddsHistoryTableModel(parent=self)
        self.history_proxy_model = QSortFilterProxyModel(self)
        self.history_proxy_model.setSourceModel(self.history_model)
        self.history_proxy_model.setSortRole(OddsHistoryTableModel.SORT_ROLE)
        self.history_proxy_model.setDynamicSortFilter(True)
        self.risk_model = TradingRiskTableModel(parent=self)
        self.risk_proxy_model = TradingRiskFilterProxyModel(self)
        self.risk_proxy_model.setSourceModel(self.risk_model)
        self.audit_model = TradingAuditTableModel(parent=self)
        self.audit_proxy_model = QSortFilterProxyModel(self)
        self.audit_proxy_model.setSourceModel(self.audit_model)
        self.audit_proxy_model.setSortRole(TradingAuditTableModel.SORT_ROLE)
        self.audit_proxy_model.setDynamicSortFilter(True)
        self.event_model = SimulationEventTableModel(parent=self)

        self.simulation_timer = QTimer(self)
        self.simulation_timer.setInterval(1000)
        self.simulation_timer.timeout.connect(self._simulation_tick)

        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(5)

        heading = QHBoxLayout()
        title = QLabel("MARKET MONITOR")
        title.setStyleSheet("font-size: 11pt; font-weight: bold; color: #8fc7e8;")
        heading.addWidget(title)
        simulation_label = QLabel("SIMULATED MARKETS ONLY")
        simulation_label.setStyleSheet("color: #e9b86e; font-weight: bold;")
        heading.addWidget(simulation_label)
        heading.addStretch(1)
        layout.addLayout(heading)

        controls = QHBoxLayout()
        self.market_search = QLineEdit()
        self.market_search.setPlaceholderText("Search match, team, competition, selection...")
        self.market_search.setClearButtonEnabled(True)
        self.market_search.textChanged.connect(self.market_proxy_model.set_search_text)
        controls.addWidget(self.market_search, 3)

        self.market_type_filter = QComboBox()
        self.market_type_filter.addItem("All markets", "All markets")
        self.market_type_filter.addItem("1X2", "1X2")
        self.market_type_filter.addItem("Over/Under 2.5", "OVER_UNDER_2_5")
        self.market_type_filter.addItem(
            "Both Teams To Score", "BOTH_TEAMS_TO_SCORE"
        )
        self.market_type_filter.currentIndexChanged.connect(
            lambda: self.market_proxy_model.set_market_type(
                self.market_type_filter.currentData()
            )
        )
        controls.addWidget(self.market_type_filter)

        self.market_status_filter = QComboBox()
        self.market_status_filter.addItems(
            ("All statuses", "OPEN", "SUSPENDED", "CLOSED")
        )
        self.market_status_filter.currentTextChanged.connect(
            self.market_proxy_model.set_status
        )
        controls.addWidget(self.market_status_filter)

        self.warning_threshold = _threshold_spin(10)
        self.critical_threshold = _threshold_spin(15)
        controls.addWidget(QLabel("Unexpected warn %"))
        controls.addWidget(self.warning_threshold)
        controls.addWidget(QLabel("Unexpected critical %"))
        controls.addWidget(self.critical_threshold)
        self.warning_threshold.valueChanged.connect(
            lambda value: self._update_thresholds("warning", value)
        )
        self.critical_threshold.valueChanged.connect(
            lambda value: self._update_thresholds("critical", value)
        )
        layout.addLayout(controls)

        simulation_controls = QHBoxLayout()
        self.seed_input = QSpinBox()
        self.seed_input.setRange(-(2**31), (2**31) - 1)
        self.seed_input.setValue(20261115)
        self.speed_filter = QComboBox()
        for speed in (1, 5, 10):
            self.speed_filter.addItem(f"{speed}x", speed)
        self.speed_filter.setCurrentIndex(1)
        self.speed_filter.currentIndexChanged.connect(
            self._simulation_speed_changed
        )
        self.start_button = QPushButton("Start / Resume")
        self.start_button.clicked.connect(self.start_simulation)
        self.pause_button = QPushButton("Pause")
        self.pause_button.clicked.connect(self.pause_simulation)
        self.reset_button = QPushButton("Reset Run")
        self.reset_button.clicked.connect(self.reset_simulation)
        self.simulation_status = QLabel("OFF")
        self.simulation_status.setStyleSheet("color: #aab3bc; font-weight: bold;")
        self.suspend_button = QPushButton("Suspend Selected")
        self.suspend_button.setEnabled(False)
        self.suspend_button.clicked.connect(self._request_suspend)
        self.reopen_button = QPushButton("Reopen Selected")
        self.reopen_button.setEnabled(False)
        self.reopen_button.clicked.connect(self._request_reopen)
        for widget in (
            QLabel("Seed"),
            self.seed_input,
            QLabel("Speed"),
            self.speed_filter,
            self.start_button,
            self.pause_button,
            self.reset_button,
            self.simulation_status,
            self.suspend_button,
            self.reopen_button,
        ):
            simulation_controls.addWidget(widget)
        simulation_controls.addStretch(1)
        simulation_controls.addWidget(
            QLabel("Virtual match minutes per 1-second timer tick")
        )
        layout.addLayout(simulation_controls)

        upper = QSplitter(Qt.Orientation.Horizontal, self)
        self.market_table = QTableView(upper)
        self.market_table.setObjectName("marketTable")
        self.market_table.setModel(self.market_proxy_model)
        self.market_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.market_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.market_table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.market_table.setAlternatingRowColors(True)
        self.market_table.setSortingEnabled(True)
        self.market_table.verticalHeader().setDefaultSectionSize(24)
        header = self.market_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate(
            (78, 105, 165, 94, 112, 98, 68, 78, 70, 72, 90, 90, 78)
        ):
            self.market_table.setColumnWidth(column, width)
        header.setStretchLastSection(True)
        self.market_table.selectionModel().currentRowChanged.connect(
            self._on_market_selected
        )
        upper.addWidget(self.market_table)

        details = QWidget(upper)
        details_layout = QVBoxLayout(details)
        details_layout.setContentsMargins(8, 5, 5, 5)
        details_title = QLabel("SELECTED MARKET")
        details_title.setStyleSheet("font-weight: bold; color: #8fc7e8;")
        details_layout.addWidget(details_title)
        self.market_detail = QLabel("Select a market selection")
        self.market_detail.setWordWrap(True)
        self.market_detail.setStyleSheet("font-size: 11pt; font-weight: bold;")
        details_layout.addWidget(self.market_detail)
        self.market_metadata = QLabel()
        self.market_metadata.setWordWrap(True)
        details_layout.addWidget(self.market_metadata)
        self.detail_tabs = QTabWidget(details)
        self.history_table = QTableView(details)
        self.history_table.setObjectName("oddsHistoryTable")
        self.history_table.setModel(self.history_proxy_model)
        self.history_table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.history_table.verticalHeader().hide()
        self.history_table.setSortingEnabled(True)
        self.history_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        self.history_table.horizontalHeader().setStretchLastSection(True)
        for column, width in enumerate((118, 58, 58, 55, 60, 75)):
            self.history_table.setColumnWidth(column, width)
        self.history_table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
        self.detail_tabs.addTab(self.history_table, "ODDS HISTORY")
        self.event_table = QTableView(details)
        self.event_table.setObjectName("simulationEventTable")
        self.event_table.setModel(self.event_model)
        self.event_table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.event_table.verticalHeader().hide()
        self.event_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        for column, width in enumerate((55, 100, 95, 250)):
            self.event_table.setColumnWidth(column, width)
        self.event_table.horizontalHeader().setStretchLastSection(True)
        self.detail_tabs.addTab(self.event_table, "MATCH TIMELINE")
        details_layout.addWidget(self.detail_tabs, 1)
        upper.addWidget(details)
        upper.setSizes([850, 330])
        self.market_splitter = upper
        layout.addWidget(upper, 3)

        self.bottom_tabs = QTabWidget(self)
        self.bottom_tabs.addTab(self._build_risk_panel(), "TRADING RISK")
        self.bottom_tabs.addTab(self._build_audit_panel(), "TRADING AUDIT")
        layout.addWidget(self.bottom_tabs, 2)

    def _build_risk_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(5, 4, 5, 4)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Severity"))
        self.risk_severity_filter = QComboBox()
        self.risk_severity_filter.addItems(
            ("All severities", "ERROR", "WARNING")
        )
        self.risk_severity_filter.currentTextChanged.connect(
            self._risk_severity_changed
        )
        controls.addWidget(self.risk_severity_filter)
        controls.addWidget(QLabel("Status"))
        self.risk_status_filter = QComboBox()
        self.risk_status_filter.addItems(
            ("All incidents", "OPEN", "ACKNOWLEDGED", "RESOLVED")
        )
        self.risk_status_filter.currentTextChanged.connect(
            self._risk_status_changed
        )
        controls.addWidget(self.risk_status_filter)
        controls.addWidget(QLabel("Run"))
        self.risk_run_filter = QComboBox()
        self.risk_run_filter.addItem("Current / last run", "current")
        self.risk_run_filter.addItem("All runs", "all")
        self.risk_run_filter.addItem("Historical only", "historical")
        self.risk_run_filter.currentIndexChanged.connect(
            self._risk_scope_changed
        )
        controls.addWidget(self.risk_run_filter)
        self.risk_note_input = QLineEdit()
        self.risk_note_input.setPlaceholderText(
            "Optional acknowledgement note..."
        )
        controls.addWidget(self.risk_note_input, 2)
        self.acknowledge_risk_button = QPushButton("Acknowledge selected")
        self.acknowledge_risk_button.clicked.connect(self._acknowledge_selected_risk)
        controls.addWidget(self.acknowledge_risk_button)
        self.view_occurrences_button = QPushButton("View occurrences")
        self.view_occurrences_button.clicked.connect(self._view_selected_occurrences)
        controls.addWidget(self.view_occurrences_button)
        self.risk_summary = QLabel("0 open incidents")
        controls.addWidget(self.risk_summary)
        layout.addLayout(controls)

        self.risk_empty_label = QLabel()
        self.risk_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.risk_empty_label.setStyleSheet("color: #aab3bc; padding: 5px;")
        self.risk_empty_label.hide()
        layout.addWidget(self.risk_empty_label)

        self.risk_table = QTableView(panel)
        self.risk_table.setObjectName("tradingRiskTable")
        self.risk_table.setModel(self.risk_proxy_model)
        self.risk_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.risk_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.risk_table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.risk_table.verticalHeader().hide()
        risk_header = self.risk_table.horizontalHeader()
        risk_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate(
            (115, 66, 78, 115, 95, 78, 300, 185, 185, 110, 150)
        ):
            self.risk_table.setColumnWidth(column, width)
        risk_header.setStretchLastSection(True)
        layout.addWidget(self.risk_table)
        return panel

    def _build_audit_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(5, 4, 5, 4)
        self.audit_table = QTableView(panel)
        self.audit_table.setObjectName("tradingAuditTable")
        self.audit_table.setModel(self.audit_proxy_model)
        self.audit_table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.audit_table.verticalHeader().hide()
        self.audit_table.setSortingEnabled(True)
        audit_header = self.audit_table.horizontalHeader()
        audit_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        for column, width in enumerate((120, 80, 120, 100, 155, 330, 100, 90)):
            self.audit_table.setColumnWidth(column, width)
        audit_header.setStretchLastSection(True)
        self.audit_table.sortByColumn(0, Qt.SortOrder.DescendingOrder)
        layout.addWidget(self.audit_table)
        return panel

    def refresh(self) -> None:
        selected_id = self._selected_selection_id()
        self.market_model.replace_rows(self.database.load_markets())
        self._refresh_risk_incidents()
        self.audit_model.replace_entries(self.database.load_trading_audit_log())
        self._sync_simulation_controls()
        if selected_id is not None and self._select_selection(selected_id):
            selected = next(
                row
                for row_index in range(self.market_model.rowCount())
                if (row := self.market_model.row_at(row_index)).selection_id
                == selected_id
            )
            self._render_market(selected)
            return
        if self.market_proxy_model.rowCount() > 0:
            self.market_table.setCurrentIndex(self.market_proxy_model.index(0, 0))
            self.market_table.selectRow(0)
        else:
            self._selected_market_row = None
            self.market_detail.setText("No market selection matches the filters.")
            self.market_metadata.clear()
            self.history_model.replace_movements(())
            self.event_model.replace_events(())

    def _refresh_risk_incidents(self) -> None:
        incidents = self.database.load_trading_risk_incidents(
            str(self.risk_run_filter.currentData())
        )
        self.risk_model.replace_alerts(incidents)
        self._refresh_risk_summary(incidents)

    def _visible_risk_incidents(self):
        return tuple(
            self.risk_model.alert_at(
                self.risk_proxy_model.mapToSource(
                    self.risk_proxy_model.index(row, 0)
                ).row()
            )
            for row in range(self.risk_proxy_model.rowCount())
        )

    def _refresh_risk_summary(self, scoped_incidents=None) -> None:
        visible_incidents = self._visible_risk_incidents()
        open_incidents = sum(
            incident.status == "OPEN" for incident in visible_incidents
        )
        occurrence_count = sum(
            incident.occurrence_count for incident in visible_incidents
        )
        incident_count = len(visible_incidents)
        incident_label = "incident" if incident_count == 1 else "incidents"
        scope_label = self.risk_run_filter.currentText()
        self.risk_summary.setText(
            f"{incident_count} {incident_label} | {open_incidents} open | "
            f"{occurrence_count} occurrences | {scope_label}"
        )

        if incident_count:
            self.risk_empty_label.hide()
            return
        if scoped_incidents:
            message = "No incidents match the selected severity and status filters."
        elif self.risk_run_filter.currentData() == "current":
            message = (
                "No incidents belong to the current / last run. Select All runs "
                "or Historical only to view older or unassigned incidents."
            )
        elif self.risk_run_filter.currentData() == "historical":
            message = "No historical or unassigned incidents are available."
        else:
            message = "No trading risk incidents have been recorded."
        self.risk_empty_label.setText(message)
        self.risk_empty_label.show()

    def _risk_severity_changed(self, severity: str) -> None:
        self.risk_proxy_model.set_severity(severity)
        self._refresh_risk_summary(self.risk_model.alerts)

    def _risk_status_changed(self, status: str) -> None:
        self.risk_proxy_model.set_status(status)
        self._refresh_risk_summary(self.risk_model.alerts)

    def _risk_scope_changed(self) -> None:
        try:
            self._refresh_risk_incidents()
        except SQLAlchemyError as error:
            self._show_database_error("load trading risk incidents", error)

    def _sync_simulation_controls(self) -> None:
        run = self.simulation.run
        self.pause_button.setEnabled(self.simulation.running)
        self.start_button.setEnabled(not self.simulation.running)
        self.reset_button.setEnabled(run is not None)
        self.speed_filter.setEnabled(
            run is None or run.status in ("RUNNING", "PAUSED")
        )
        if run is None:
            self.simulation_status.setText("OFF")
            self.simulation_status.setStyleSheet(
                "color: #aab3bc; font-weight: bold;"
            )
            return
        speed_index = self.speed_filter.findData(run.speed)
        if speed_index >= 0 and self.speed_filter.currentIndex() != speed_index:
            self.speed_filter.blockSignals(True)
            self.speed_filter.setCurrentIndex(speed_index)
            self.speed_filter.blockSignals(False)
        if self.seed_input.value() != run.seed:
            self.seed_input.setValue(run.seed)
        if self.simulation.running:
            label, color = "RUNNING", "#72d6a0"
        elif run.status == "COMPLETED":
            label, color = "COMPLETED", "#8fc7e8"
        else:
            label, color = "PAUSED — press Start to resume", "#e9b86e"
        self.simulation_status.setText(label)
        self.simulation_status.setStyleSheet(
            f"color: {color}; font-weight: bold;"
        )

    def _selected_selection_id(self) -> int | None:
        index = self.market_table.currentIndex()
        if not index.isValid():
            return None
        source_index = self.market_proxy_model.mapToSource(index)
        return self.market_model.row_at(source_index.row()).selection_id

    def _select_selection(self, selection_id: int) -> bool:
        for source_row in range(self.market_model.rowCount()):
            if self.market_model.row_at(source_row).selection_id == selection_id:
                proxy_index = self.market_proxy_model.mapFromSource(
                    self.market_model.index(source_row, 0)
                )
                if proxy_index.isValid():
                    self.market_table.setCurrentIndex(proxy_index)
                    self.market_table.selectRow(proxy_index.row())
                    return True
        return False

    def _on_market_selected(
        self, current: QModelIndex, previous: QModelIndex
    ) -> None:
        if not current.isValid():
            self._selected_market_row = None
            self.market_detail.setText("Select a market selection")
            self.market_metadata.clear()
            self.history_model.replace_movements(())
            self.event_model.replace_events(())
            self.suspend_button.setEnabled(False)
            self.reopen_button.setEnabled(False)
            return
        source_index = self.market_proxy_model.mapToSource(current)
        self._render_market(self.market_model.row_at(source_index.row()))

    def _render_market(self, row: MarketRow) -> None:
        self._selected_market_row = row
        clock = f"{row.match_minute}'" if row.match_minute is not None else "—"
        self.market_detail.setText(
            f"{row.fixture}\n{market_type_label(row.market_type)} | "
            f"{row.selection_name}"
        )
        self.market_metadata.setText(
            f"Match: {row.match_id}  |  Competition: {row.competition}\n"
            f"Score: {row.home_score if row.home_score is not None else '—'}-"
            f"{row.away_score if row.away_score is not None else '—'}  |  "
            f"{row.match_status or 'UNKNOWN'} {clock}  |  "
            f"Red cards: {row.home_red_cards}-{row.away_red_cards}\n"
            f"Odds: {row.decimal_odds:.3f}  |  Implied probability: "
            f"{row.implied_probability:.2f}%  |  Status: {row.market_status}\n"
            f"Last update: {_format_timestamp(row.last_update)}  |  "
            f"Source: {'SIMULATED DATA' if row.simulated else 'DATABASE'}"
        )
        self.history_model.replace_movements(
            self.database.load_odds_history(row.selection_id)
        )
        self.event_model.replace_events(
            self.database.load_simulation_events(row.match_id)
        )
        self.suspend_button.setEnabled(row.market_status == "OPEN")
        self.reopen_button.setEnabled(row.market_status == "SUSPENDED")

    def _update_thresholds(self, changed: str, value: float) -> None:
        if self.critical_threshold.value() < self.warning_threshold.value():
            if changed == "warning":
                self.critical_threshold.blockSignals(True)
                self.critical_threshold.setValue(value)
                self.critical_threshold.blockSignals(False)
            else:
                self.warning_threshold.blockSignals(True)
                self.warning_threshold.setValue(value)
                self.warning_threshold.blockSignals(False)
        previous = self.simulation.thresholds
        updated = RiskThresholds(
            warning_percent=Decimal(str(self.warning_threshold.value())),
            critical_percent=Decimal(str(self.critical_threshold.value())),
        )
        try:
            self.simulation.set_thresholds(updated)
        except (SQLAlchemyError, ValueError) as error:
            if isinstance(error, SQLAlchemyError):
                self._show_database_error("update simulation risk thresholds", error)
            else:
                self._show_status(str(error))
            return
        self.market_model.significant_threshold = self.warning_threshold.value()
        self.history_model.significant_threshold = self.warning_threshold.value()
        if self.market_model.rowCount():
            self.market_model.dataChanged.emit(
                self.market_model.index(0, 0),
                self.market_model.index(
                    self.market_model.rowCount() - 1,
                    len(self.market_model.HEADERS) - 1,
                ),
                [Qt.ItemDataRole.BackgroundRole],
            )
        if self.history_model.rowCount():
            self.history_model.dataChanged.emit(
                self.history_model.index(0, 0),
                self.history_model.index(
                    self.history_model.rowCount() - 1,
                    len(self.history_model.HEADERS) - 1,
                ),
                [Qt.ItemDataRole.BackgroundRole],
            )
        if updated != previous:
            try:
                self.database.record_trading_operator_action(
                    "RISK_THRESHOLDS_CHANGED",
                    "Operator changed simulated odds risk thresholds.",
                    f"{previous.warning_percent}/{previous.critical_percent}",
                    f"{updated.warning_percent}/{updated.critical_percent}",
                )
                self.audit_model.replace_entries(
                    self.database.load_trading_audit_log()
                )
            except SQLAlchemyError as error:
                self._show_database_error("record risk thresholds", error)
                return
        self.activityLogged.emit(
            "INFO",
            "Trading risk thresholds set to "
            f"{self.warning_threshold.value():.1f}% / "
            f"{self.critical_threshold.value():.1f}%.",
        )

    def start_simulation(self) -> None:
        try:
            self.simulation.start(
                self.seed_input.value(),
                int(self.speed_filter.currentData()),
            )
        except (SQLAlchemyError, ValueError, RuntimeError) as error:
            if isinstance(error, SQLAlchemyError):
                self._show_database_error("start event simulation", error)
            else:
                self._show_status(str(error))
            return
        if self.simulation.running:
            self.simulation_timer.start()
        self.refresh()
        self.activityLogged.emit(
            "INFO",
            "Event-driven football simulation started or resumed "
            f"(seed {self.simulation.run.seed}, {self.simulation.run.speed}x).",
        )

    def pause_simulation(self) -> None:
        if not self.simulation.running:
            return
        try:
            self.simulation.pause()
        except SQLAlchemyError as error:
            self._show_database_error("pause event simulation", error)
            return
        self.simulation_timer.stop()
        self.refresh()
        self.activityLogged.emit("INFO", "Event-driven simulation paused.")

    def reset_simulation(self) -> None:
        self.simulation_timer.stop()
        try:
            changed = self.simulation.reset()
            self.refresh()
        except SQLAlchemyError as error:
            self._show_database_error("reset event simulation", error)
            return
        self._sync_simulation_controls()
        self.activityLogged.emit(
            "INFO",
            "Event simulation reset; event and odds history were retained."
            if changed
            else "No active event simulation to reset.",
        )

    def _simulation_tick(self) -> None:
        try:
            changes = self.simulation.advance()
        except SQLAlchemyError as error:
            self.simulation_timer.stop()
            self.simulation.halt_after_error()
            self._show_database_error("advance event simulation", error)
            return
        self.refresh()
        if not self.simulation.running:
            self.simulation_timer.stop()
        if changes:
            self.activityLogged.emit(
                "INFO", f"Event simulation repriced {changes} market selections."
            )

    def _simulation_speed_changed(self) -> None:
        speed = int(self.speed_filter.currentData())
        try:
            self.simulation.set_speed(speed)
            if self.simulation.run is not None:
                self.audit_model.replace_entries(
                    self.database.load_trading_audit_log()
                )
        except (SQLAlchemyError, ValueError) as error:
            if isinstance(error, SQLAlchemyError):
                self._show_database_error("change simulation speed", error)
            else:
                self._show_status(str(error))

    def _request_suspend(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        reason, accepted = QInputDialog.getText(
            self,
            "Suspend simulated market",
            "Reason for suspension (required):",
        )
        if accepted:
            self.suspend_selected(reason)

    def _request_reopen(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        reason, accepted = QInputDialog.getText(
            self,
            "Reopen simulated market",
            "Optional reopening note:",
        )
        if accepted:
            self.reopen_selected(reason)

    def suspend_selected(self, reason: str) -> bool:
        if self._selected_market_row is None:
            self._show_status("Select a market first.")
            return False
        try:
            changed = self.database.set_simulation_market_status(
                self._selected_market_row.market_id, "SUSPENDED", reason
            )
            if changed:
                self.refresh()
        except (ValueError, LookupError) as error:
            self._show_status(str(error))
            return False
        except SQLAlchemyError as error:
            self._show_database_error("suspend market", error)
            return False
        if changed:
            self.activityLogged.emit(
                "WARNING",
                f"Market {self._selected_market_row.market_id} suspended: {reason.strip()}",
            )
        return changed

    def reopen_selected(self, reason: str = "") -> bool:
        if self._selected_market_row is None:
            self._show_status("Select a market first.")
            return False
        market_id = self._selected_market_row.market_id
        try:
            changed = self.database.set_simulation_market_status(
                market_id, "OPEN", reason
            )
            if changed:
                self.refresh()
        except (ValueError, LookupError) as error:
            self._show_status(str(error))
            return False
        except SQLAlchemyError as error:
            self._show_database_error("reopen market", error)
            return False
        if changed:
            self.activityLogged.emit(
                "INFO", f"Market {market_id} reopened."
            )
        return changed

    def _acknowledge_selected_risk(self) -> None:
        index = self.risk_table.currentIndex()
        if not index.isValid():
            self._show_status("Select a trading risk alert first.")
            return
        source_index = self.risk_proxy_model.mapToSource(index)
        incident = self.risk_model.alert_at(source_index.row())
        try:
            changed = self.database.acknowledge_trading_risk_incident(
                incident.incident_id, self.risk_note_input.text()
            )
            self.risk_note_input.clear()
            self.refresh()
        except LookupError as error:
            self._show_status(str(error))
            return
        except SQLAlchemyError as error:
            self._show_database_error("acknowledge trading risk incident", error)
            return
        self.activityLogged.emit(
            "INFO" if changed else "WARNING",
            f"Trading risk incident {incident.incident_id} "
            f"{'acknowledged' if changed else 'was already acknowledged'}.",
        )

    def _view_selected_occurrences(self) -> None:
        index = self.risk_table.currentIndex()
        if not index.isValid():
            self._show_status("Select a trading risk incident first.")
            return
        source_index = self.risk_proxy_model.mapToSource(index)
        incident = self.risk_model.alert_at(source_index.row())
        try:
            occurrences = self.database.load_risk_incident_occurrences(
                incident.incident_id
            )
        except SQLAlchemyError as error:
            self._show_database_error("load risk incident occurrences", error)
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(
            f"Risk incident {incident.incident_id} | "
            f"{incident.occurrence_count} occurrence(s)"
        )
        dialog.resize(900, 420)
        layout = QVBoxLayout(dialog)
        table = QTableWidget(len(occurrences), 6, dialog)
        table.setHorizontalHeaderLabels(
            ("Severity", "Detected", "Previous", "Current", "Move", "Details")
        )
        table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive
        )
        table.horizontalHeader().setStretchLastSection(True)
        for row, occurrence in enumerate(occurrences):
            values = (
                occurrence.severity,
                _format_timestamp(occurrence.occurred_at),
                f"{occurrence.previous_odds:.3f}"
                if occurrence.previous_odds is not None
                else "—",
                f"{occurrence.current_odds:.3f}"
                if occurrence.current_odds is not None
                else "—",
                f"{occurrence.percentage_change:+.2f}%"
                if occurrence.percentage_change is not None
                else "—",
                occurrence.description,
            )
            for column, value in enumerate(values):
                table.setItem(row, column, QTableWidgetItem(value))
        layout.addWidget(table)
        close_button = QPushButton("Close", dialog)
        close_button.clicked.connect(dialog.accept)
        layout.addWidget(close_button)
        dialog.exec()

    def _show_status(self, message: str) -> None:
        self.simulation_status.setText(message)
        self.simulation_status.setStyleSheet(
            "color: #e9b86e; font-weight: bold;"
        )

    def _show_database_error(self, operation: str, error: SQLAlchemyError) -> None:
        message = f"Could not {operation}: {error}"
        self.simulation_status.setText("DATABASE ERROR")
        self.simulation_status.setStyleSheet(
            "color: #ef8585; font-weight: bold;"
        )
        self.activityLogged.emit("ERROR", message)
        QMessageBox.critical(self, "Database operation failed", message)


def _threshold_spin(value: int) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(1.0, 100.0)
    spin.setDecimals(1)
    spin.setSuffix("%")
    spin.setValue(float(value))
    spin.setMaximumWidth(85)
    return spin


