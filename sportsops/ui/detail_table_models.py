from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QColor

from sportsops.models.match import ActivityEntry, AlertInfo, MatchEventInfo
from sportsops.ui.match_table_model import (
    _format_timestamp,
    invalidate_proxy_filter,
)


class MatchEventTableModel(QAbstractTableModel):
    HEADERS = ("Minute", "Event", "Team", "Details", "Source")

    def __init__(self, events: tuple[MatchEventInfo, ...] = (), parent=None) -> None:
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
                "SIMULATED" if event.simulated else "DATABASE",
            )
            return values[index.column()]
        if role == Qt.ItemDataRole.BackgroundRole:
            if event.event_type == "GOAL":
                return QColor("#244b38")
            if "CARD" in event.event_type:
                return QColor("#5b482b")
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
        return None

    def replace_events(self, events: tuple[MatchEventInfo, ...]) -> None:
        if events == self._events:
            return
        if len(events) >= len(self._events) and events[:len(self._events)] == self._events:
            first = len(self._events)
            last = len(events) - 1
            if first <= last:
                self.beginInsertRows(QModelIndex(), first, last)
                self._events = events
                self.endInsertRows()
                return
        self.beginResetModel()
        self._events = events
        self.endResetModel()


class AlertTableModel(QAbstractTableModel):
    HEADERS = ("Severity", "Match ID", "Description", "Detected", "Status")

    def __init__(self, alerts: tuple[AlertInfo, ...] = (), parent=None) -> None:
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
                alert.severity,
                alert.match_id,
                alert.description,
                _format_timestamp(alert.detected_at),
                (
                    "RESOLVED" if alert.resolved_at
                    else "ACKNOWLEDGED" if alert.acknowledged_at
                    else "OPEN"
                ),
            )
            return values[index.column()]
        if role == Qt.ItemDataRole.BackgroundRole and index.column() == 0:
            return QColor({"ERROR": "#67343a", "WARNING": "#65542f"}.get(alert.severity, "#303740"))
        if role == Qt.ItemDataRole.UserRole:
            return alert.alert_id
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
        return None

    def alert_at(self, row: int) -> AlertInfo:
        return self._alerts[row]

    @property
    def alerts(self) -> tuple[AlertInfo, ...]:
        return tuple(self._alerts)

    def replace_alerts(self, alerts: tuple[AlertInfo, ...]) -> None:
        self.beginResetModel()
        self._alerts = alerts
        self.endResetModel()


class AlertFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._severity = "All severities"
        self._match_id = "All matches"

    def set_severity(self, severity: str) -> None:
        self._severity = severity
        invalidate_proxy_filter(self)

    def set_match_id(self, match_id: str) -> None:
        self._match_id = match_id
        invalidate_proxy_filter(self)

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        alert = self.sourceModel().alert_at(source_row)
        return (
            (self._severity == "All severities" or alert.severity == self._severity)
            and (self._match_id == "All matches" or alert.match_id == self._match_id)
        )


class ActivityTableModel(QAbstractTableModel):
    HEADERS = ("Time", "Severity", "Operational Activity")

    def __init__(self, parent=None, max_rows: int = 500) -> None:
        super().__init__(parent)
        self._entries: list[ActivityEntry] = []
        self._max_rows = max_rows

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
                _format_timestamp(entry.timestamp),
                entry.severity,
                entry.message,
            )
            return values[index.column()]
        if role == Qt.ItemDataRole.BackgroundRole and index.column() == 1:
            return QColor(
                {"ERROR": "#67343a", "WARNING": "#65542f"}.get(
                    entry.severity, "#303740"
                )
            )
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
        return None

    def append(self, entry: ActivityEntry) -> None:
        if len(self._entries) >= self._max_rows:
            self.beginRemoveRows(QModelIndex(), 0, 0)
            self._entries.pop(0)
            self.endRemoveRows()
        row = len(self._entries)
        self.beginInsertRows(QModelIndex(), row, row)
        self._entries.append(entry)
        self.endInsertRows()
