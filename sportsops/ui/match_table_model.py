from datetime import datetime, timezone

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
)
from PySide6.QtGui import QColor

from sportsops.models.match import Match


def invalidate_proxy_filter(proxy: QSortFilterProxyModel) -> None:
    begin_change = getattr(proxy, "beginFilterChange", None)
    end_change = getattr(proxy, "endFilterChange", None)
    if begin_change is not None and end_change is not None:
        begin_change()
        end_change(QSortFilterProxyModel.Direction.Rows)
    else:
        proxy.invalidateFilter()


class MatchTableModel(QAbstractTableModel):
    HEADERS = (
        "Match ID",
        "Competition",
        "Home Team",
        "Away Team",
        "Score",
        "Match Minute",
        "Status",
        "Last Update",
        "Data Quality",
    )
    SORT_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(self, matches: tuple[Match, ...], parent=None) -> None:
        super().__init__(parent)
        self._matches = matches

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._matches)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._matches)):
            return None

        match = self._matches[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            values = (
                match.match_id,
                match.competition,
                match.home_team,
                match.away_team,
                match.score,
                match.minute,
                match.status.replace("_", " "),
                _format_timestamp(match.last_update),
                match.data_quality,
            )
            return values[index.column()]
        if role == self.SORT_ROLE:
            return _sort_value(match, index.column())
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() in (0, 4, 5, 6, 8):
            return int(Qt.AlignmentFlag.AlignCenter)
        if role == Qt.ItemDataRole.BackgroundRole:
            if index.column() == 8:
                colors = {"ERROR": "#67343a", "WARNING": "#65542f"}
                color = colors.get(match.data_quality)
                return QColor(color) if color else None
            status_colors = {
                "LIVE": "#203a43",
                "SCHEDULED": "#303740",
                "FINISHED": "#262b31",
                "HALF_TIME": "#443d2b",
                "INTERRUPTED": "#65343a",
            }
            if match.status in status_colors:
                return QColor(status_colors[match.status])
        if role == Qt.ItemDataRole.ToolTipRole:
            return (
                f"{match.competition} {match.season} | {match.home_team} vs "
                f"{match.away_team} | {match.data_source}"
            )
        return None

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if role != Qt.ItemDataRole.DisplayRole:
            return None
        if orientation == Qt.Orientation.Horizontal and 0 <= section < len(self.HEADERS):
            return self.HEADERS[section]
        if orientation == Qt.Orientation.Vertical:
            return str(section + 1)
        return None

    def match_at(self, row: int) -> Match:
        return self._matches[row]

    def replace_matches(self, matches: tuple[Match, ...]) -> None:
        if tuple(match.match_id for match in matches) == tuple(
            match.match_id for match in self._matches
        ):
            previous = self._matches
            self._matches = matches
            roles = [
                Qt.ItemDataRole.DisplayRole,
                Qt.ItemDataRole.BackgroundRole,
                Qt.ItemDataRole.ToolTipRole,
                self.SORT_ROLE,
            ]
            for row, (old_match, new_match) in enumerate(zip(previous, matches)):
                if old_match != new_match:
                    self.dataChanged.emit(
                        self.index(row, 0),
                        self.index(row, len(self.HEADERS) - 1),
                        roles,
                    )
            return
        self.beginResetModel()
        self._matches = matches
        self.endResetModel()


class MatchFilterProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._search_text = ""
        self._status = "All statuses"
        self._competition = "All competitions"
        self._team = "All teams"
        self._data_origin = "All match origins"
        self.setSortRole(MatchTableModel.SORT_ROLE)
        self.setDynamicSortFilter(True)

    def set_search_text(self, text: str) -> None:
        self._search_text = text.casefold().strip()
        invalidate_proxy_filter(self)

    def set_status(self, status: str) -> None:
        self._status = status
        invalidate_proxy_filter(self)

    def set_competition(self, competition: str) -> None:
        self._competition = competition
        invalidate_proxy_filter(self)

    def set_team(self, team: str) -> None:
        self._team = team
        invalidate_proxy_filter(self)

    def set_data_origin(self, data_origin: str) -> None:
        self._data_origin = data_origin
        invalidate_proxy_filter(self)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        source = self.sourceModel()
        return source.data(left, MatchTableModel.SORT_ROLE) < source.data(
            right, MatchTableModel.SORT_ROLE
        )

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        source = self.sourceModel()
        match = source.match_at(source_row)
        if self._status != "All statuses" and match.status.replace("_", " ") != self._status:
            return False
        if self._competition != "All competitions" and match.competition != self._competition:
            return False
        if self._team != "All teams" and self._team not in (match.home_team, match.away_team):
            return False
        if self._data_origin != "All match origins" and match.data_origin != self._data_origin:
            return False
        searchable_row = " ".join(
            (
                match.match_id,
                match.competition,
                match.home_team,
                match.away_team,
                match.status.replace("_", " "),
                match.data_quality,
                match.data_origin,
                match.source_name,
            )
        ).casefold()
        return all(term in searchable_row for term in self._search_text.split())


def _sort_value(match: Match, column: int):
    if column == 0:
        return match.match_id.casefold()
    if column == 1:
        return match.competition.casefold()
    if column == 2:
        return match.home_team.casefold()
    if column == 3:
        return match.away_team.casefold()
    if column == 4:
        return (
            match.home_score if match.home_score is not None else 32767,
            match.away_score if match.away_score is not None else 32767,
        )
    if column == 5:
        return match.elapsed_minute if match.elapsed_minute is not None else 10000
    if column == 6:
        priorities = {
            "LIVE": 0,
            "HALF_TIME": 1,
            "SCHEDULED": 2,
            "INTERRUPTED": 3,
            "FINISHED": 4,
        }
        return priorities.get(match.status, 5)
    if column == 7:
        return match.last_update.timestamp() if match.last_update else 0
    return {"ERROR": 0, "WARNING": 1, "CLEAR": 2}.get(match.data_quality, 3)


def _local_timestamp(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone()


def _format_timestamp(value) -> str:
    local_value = _local_timestamp(value)
    if local_value is None:
        return "—"
    return local_value.strftime("%Y-%m-%d %H:%M:%S %z")


def _format_timestamp_compact(value) -> str:
    local_value = _local_timestamp(value)
    if local_value is None:
        return "—"
    return local_value.strftime("%Y-%m-%d %H:%M")


def _timestamp_sort_key(value) -> float:
    local_value = _local_timestamp(value)
    if local_value is None:
        return float("-inf")
    return local_value.timestamp()
