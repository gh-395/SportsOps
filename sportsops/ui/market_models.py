from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QColor

from sportsops.models.trading import (
    MarketRow,
    OddsMovementInfo,
    TradingAuditInfo,
)
from sportsops.models.simulation import (
    SimulationEventInfo,
    TradingRiskIncidentInfo,
)
from sportsops.ui.match_table_model import (
    _format_timestamp,
    _format_timestamp_compact,
    invalidate_proxy_filter,
    _timestamp_sort_key,
)


class MarketTableModel(QAbstractTableModel):
    HEADERS = (
        "Match",
        "Competition",
        "Fixture",
        "Score / Clock",
        "Market",
        "Selection",
        "Odds",
        "Implied %",
        "Δ Odds",
        "Δ %",
        "Status",
        "Last Update",
        "Source",
    )
    SORT_ROLE = Qt.ItemDataRole.UserRole + 2

    def __init__(self, rows: tuple[MarketRow, ...] = (), parent=None) -> None:
        super().__init__(parent)
        self._rows = rows
        self.significant_threshold = 10.0

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            score = (
                f"{row.home_score}-{row.away_score}"
                if row.home_score is not None and row.away_score is not None
                else "—"
            )
            clock = f"{row.match_minute}'" if row.match_minute is not None else "—"
            values = (
                row.match_id,
                row.competition,
                row.fixture,
                f"{score} | {clock}",
                market_type_label(row.market_type),
                row.selection_name,
                f"{row.decimal_odds:.3f}",
                f"{row.implied_probability:.2f}%",
                f"{row.absolute_change:.3f}" if row.previous_odds is not None else "—",
                (
                    f"{row.percentage_change:+.2f}%"
                    if row.previous_odds is not None
                    else "—"
                ),
                row.market_status,
                _format_timestamp(row.last_update),
                "SIMULATED" if row.simulated else "DATABASE",
            )
            return values[index.column()]
        if role == self.SORT_ROLE:
            return _market_sort_value(row, index.column())
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in (
            3, 6, 7, 8, 9, 10
        ):
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.BackgroundRole:
            if index.column() == 10:
                return QColor(
                    {
                        "OPEN": "#244b38",
                        "SUSPENDED": "#65542f",
                        "CLOSED": "#303740",
                    }[row.market_status]
                )
            if index.column() in (8, 9) and row.previous_odds is not None:
                return _movement_background(
                    float(row.percentage_change), self.significant_threshold
                )
        if role == Qt.ItemDataRole.ToolTipRole:
            return (
                f"{row.market_type} | {row.selection_name} | "
                f"{'SIMULATED DATA' if row.simulated else 'DATABASE DATA'} | "
                f"{row.match_status or 'UNKNOWN'} | red cards "
                f"{row.home_red_cards}-{row.away_red_cards}"
            )
        if role == Qt.ItemDataRole.UserRole:
            return row.selection_id
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return None

    def row_at(self, row: int) -> MarketRow:
        return self._rows[row]

    def replace_rows(self, rows: tuple[MarketRow, ...]) -> None:
        if tuple(item.selection_id for item in rows) == tuple(
            item.selection_id for item in self._rows
        ):
            previous = self._rows
            self._rows = rows
            roles = [
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.BackgroundRole,
                Qt.ItemDataRole.ToolTipRole,
                self.SORT_ROLE,
            ]
            for row, (old, new) in enumerate(zip(previous, rows)):
                if old != new:
                    self.dataChanged.emit(
                        self.index(row, 0),
                        self.index(row, len(self.HEADERS) - 1),
                        roles,
                    )
            return
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()


class MarketFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._search_text = ""
        self._market_type = "All markets"
        self._status = "All statuses"
        self.setSortRole(MarketTableModel.SORT_ROLE)
        self.setDynamicSortFilter(True)

    def set_search_text(self, text: str) -> None:
        self._search_text = text.casefold().strip()
        invalidate_proxy_filter(self)

    def set_market_type(self, market_type: str) -> None:
        self._market_type = market_type
        invalidate_proxy_filter(self)

    def set_status(self, status: str) -> None:
        self._status = status
        invalidate_proxy_filter(self)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        source = self.sourceModel()
        return source.data(left, MarketTableModel.SORT_ROLE) < source.data(
            right, MarketTableModel.SORT_ROLE
        )

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        row = self.sourceModel().row_at(source_row)
        if self._market_type != "All markets" and row.market_type != self._market_type:
            return False
        if self._status != "All statuses" and row.market_status != self._status:
            return False
        searchable = " ".join(
            (
                row.match_id,
                row.competition,
                row.fixture,
                row.market_type,
                row.selection_name,
                row.market_status,
            )
        ).casefold()
        return all(term in searchable for term in self._search_text.split())


class OddsHistoryTableModel(QAbstractTableModel):
    HEADERS = ("Updated", "Previous", "Current", "Δ Odds", "Δ %", "Source")
    SORT_ROLE = Qt.ItemDataRole.UserRole + 3

    def __init__(self, movements: tuple[OddsMovementInfo, ...] = (), parent=None) -> None:
        super().__init__(parent)
        self._movements = movements
        self.significant_threshold = 10.0

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._movements)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._movements):
            return None
        movement = self._movements[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            values = (
                _format_timestamp_compact(movement.changed_at),
                f"{movement.previous_odds:.3f}",
                f"{movement.current_odds:.3f}",
                f"{movement.absolute_change:.3f}",
                f"{movement.percentage_change:+.2f}%",
                movement.source,
            )
            return values[index.column()]
        if role == self.SORT_ROLE:
            values = (
                _timestamp_sort_key(movement.changed_at),
                movement.previous_odds,
                movement.current_odds,
                movement.absolute_change,
                movement.percentage_change,
                movement.source.casefold(),
            )
            return values[index.column()]
        if role == Qt.ItemDataRole.BackgroundRole:
            return _movement_background(
                float(movement.percentage_change), self.significant_threshold
            )
        if role == Qt.ItemDataRole.ToolTipRole:
            if index.column() == 0:
                return _format_timestamp(movement.changed_at)
            return movement.reason
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return None

    def replace_movements(self, movements: tuple[OddsMovementInfo, ...]) -> None:
        self.beginResetModel()
        self._movements = movements
        self.endResetModel()


class TradingRiskTableModel(QAbstractTableModel):
    HEADERS = (
        "Run",
        "Severity",
        "Match",
        "Market",
        "Selection",
        "Occurrences",
        "Description",
        "First Seen",
        "Last Seen",
        "Status",
        "Note",
    )

    def __init__(
        self,
        alerts: tuple[TradingRiskIncidentInfo, ...] = (),
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._alerts = alerts

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._alerts)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._alerts):
            return None
        alert = self._alerts[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            values = (
                (
                    "UNSCOPED"
                    if alert.run_id is None
                    else f"{alert.run_status or 'UNKNOWN'} {alert.run_id[:8]}"
                ),
                alert.severity,
                alert.match_id,
                market_type_label(alert.market_type),
                alert.selection_name,
                str(alert.occurrence_count),
                alert.description,
                _format_timestamp(alert.first_seen_at),
                _format_timestamp(alert.last_seen_at),
                alert.status,
                alert.acknowledgement_note or "—",
            )
            return values[index.column()]
        if role == Qt.ItemDataRole.BackgroundRole and index.column() == 1:
            return QColor("#67343a" if alert.severity == "ERROR" else "#65542f")
        if role == Qt.ItemDataRole.UserRole:
            return alert.incident_id
        if role == Qt.ItemDataRole.ToolTipRole and index.column() == 0:
            return alert.run_id or "Historical incident with no safe run association"
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return None

    def alert_at(self, row: int) -> TradingRiskIncidentInfo:
        return self._alerts[row]

    @property
    def alerts(self) -> tuple[TradingRiskIncidentInfo, ...]:
        return tuple(self._alerts)

    def replace_alerts(
        self, alerts: tuple[TradingRiskIncidentInfo, ...]
    ) -> None:
        self.beginResetModel()
        self._alerts = alerts
        self.endResetModel()


class TradingRiskFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._severity = "All severities"
        self._status = "All incidents"

    def set_severity(self, severity: str) -> None:
        self._severity = severity
        invalidate_proxy_filter(self)

    def set_status(self, status: str) -> None:
        self._status = status
        invalidate_proxy_filter(self)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        alert = self.sourceModel().alert_at(source_row)
        return (
            (self._severity == "All severities" or alert.severity == self._severity)
            and (self._status == "All incidents" or alert.status == self._status)
        )


class SimulationEventTableModel(QAbstractTableModel):
    HEADERS = ("Minute", "Event", "Team", "Description")

    def __init__(
        self, events: tuple[SimulationEventInfo, ...] = (), parent=None
    ) -> None:
        super().__init__(parent)
        self._events = events

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._events)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._events):
            return None
        event = self._events[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            values = (
                f"{event.minute}'",
                event.event_type,
                event.team,
                event.description,
            )
            return values[index.column()]
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() == 0:
            return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return None

    def replace_events(self, events: tuple[SimulationEventInfo, ...]) -> None:
        self.beginResetModel()
        self._events = events
        self.endResetModel()


class TradingAuditTableModel(QAbstractTableModel):
    HEADERS = ("Time", "Match", "Market", "Selection", "Action", "Details", "Actor", "Source")
    SORT_ROLE = Qt.ItemDataRole.UserRole + 4

    def __init__(self, entries: tuple[TradingAuditInfo, ...] = (), parent=None) -> None:
        super().__init__(parent)
        self._entries = entries

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._entries)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._entries):
            return None
        entry = self._entries[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            values = (
                _format_timestamp_compact(entry.created_at),
                entry.match_id,
                market_type_label(entry.market_type),
                entry.selection_name,
                entry.event_type,
                entry.details,
                entry.actor,
                "SIMULATED" if entry.simulated else "DATABASE",
            )
            return values[index.column()]
        if role == self.SORT_ROLE:
            values = (
                _timestamp_sort_key(entry.created_at),
                entry.match_id.casefold(),
                market_type_label(entry.market_type).casefold(),
                entry.selection_name.casefold(),
                entry.event_type.casefold(),
                entry.details.casefold(),
                entry.actor.casefold(),
                int(entry.simulated),
            )
            return values[index.column()]
        if role == Qt.ItemDataRole.ToolTipRole and index.column() == 0:
            return _format_timestamp(entry.created_at)
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self.HEADERS)
        ):
            return self.HEADERS[section]
        return None

    def replace_entries(self, entries: tuple[TradingAuditInfo, ...]) -> None:
        self.beginResetModel()
        self._entries = entries
        self.endResetModel()


def _market_sort_value(row: MarketRow, column: int):
    if column == 0:
        return row.match_id.casefold()
    if column == 1:
        return row.competition.casefold()
    if column == 2:
        return row.fixture.casefold()
    if column == 3:
        return (row.match_minute or 0, row.home_score or 0, row.away_score or 0)
    if column == 4:
        return row.market_type
    if column == 5:
        return row.selection_name.casefold()
    if column == 6:
        return row.decimal_odds
    if column == 7:
        return row.implied_probability
    if column == 8:
        return row.absolute_change
    if column == 9:
        return row.percentage_change
    if column == 10:
        return {"OPEN": 0, "SUSPENDED": 1, "CLOSED": 2}[row.market_status]
    if column == 11:
        return row.last_update.timestamp()
    return 0 if row.simulated else 1


def market_type_label(market_type: str) -> str:
    return {
        "1X2": "1X2",
        "OVER_UNDER_2_5": "Over/Under 2.5",
        "BOTH_TEAMS_TO_SCORE": "Both Teams To Score",
    }.get(market_type, market_type)


def _movement_background(change_percent: float, threshold: float) -> QColor | None:
    if abs(change_percent) < threshold:
        return None
    return QColor("#263f48" if change_percent > 0 else "#4a402f")
