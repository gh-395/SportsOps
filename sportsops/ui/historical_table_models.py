from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal

from sportsops.models.historical import (
    HistoricalImportBatchInfo,
    HistoricalMatchInfo,
    HistoricalTeamSummary,
)
from sportsops.ui.match_table_model import _format_timestamp


class HistoricalMatchTableModel(QAbstractTableModel):
    HEADERS = (
        "Date",
        "Time",
        "Competition",
        "Season",
        "Home Team",
        "Score",
        "Away Team",
        "Result",
    )
    SORT_KEYS = (
        "kickoff_date",
        "kickoff_time",
        "competition",
        "season",
        "home_team",
        "score",
        "away_team",
        "result",
    )
    sortRequested = Signal(str, bool)

    def __init__(
        self, matches: tuple[HistoricalMatchInfo, ...] = (), parent=None
    ) -> None:
        super().__init__(parent)
        self._matches = matches

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._matches)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._matches):
            return None
        match = self._matches[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            values = (
                match.kickoff_date.isoformat(),
                match.kickoff_time.strftime("%H:%M") if match.kickoff_time else "—",
                match.competition,
                match.season,
                match.home_team,
                match.score,
                match.away_team,
                match.result,
            )
            return values[index.column()]
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

    def sort(self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder):
        if 0 <= column < len(self.SORT_KEYS):
            self.sortRequested.emit(
                self.SORT_KEYS[column],
                order == Qt.SortOrder.DescendingOrder,
            )

    def match_at(self, row: int) -> HistoricalMatchInfo:
        return self._matches[row]

    def replace_matches(self, matches: tuple[HistoricalMatchInfo, ...]) -> None:
        self.beginResetModel()
        self._matches = matches
        self.endResetModel()


class HistoricalTeamSummaryTableModel(QAbstractTableModel):
    PERFORMANCE_HEADERS = (
        "Position",
        "Team",
        "Played",
        "Home W-D-L",
        "Away W-D-L",
        "Goals For",
        "Goals Against",
        "Goal Diff",
        "Points",
        "Avg GF",
        "Avg GA",
    )
    STANDINGS_HEADERS = (
        "Position",
        "Team",
        "Played",
        "Won",
        "Drawn",
        "Lost",
        "Goals For",
        "Goals Against",
        "Goal Diff",
        "Points",
    )

    def __init__(self, *, standings: bool, parent=None) -> None:
        super().__init__(parent)
        self._standings = standings
        self._rows: tuple[HistoricalTeamSummary, ...] = ()

    @property
    def headers(self) -> tuple[str, ...]:
        return self.STANDINGS_HEADERS if self._standings else self.PERFORMANCE_HEADERS

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.headers)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if (
            role != Qt.ItemDataRole.DisplayRole
            or not index.isValid()
            or not 0 <= index.row() < len(self._rows)
        ):
            return None
        row = self._rows[index.row()]
        if self._standings:
            values = (
                row.position,
                row.team,
                row.played,
                row.home_wins + row.away_wins,
                row.home_draws + row.away_draws,
                row.home_losses + row.away_losses,
                row.goals_for,
                row.goals_against,
                row.goal_difference,
                row.points,
            )
        else:
            values = (
                row.position,
                row.team,
                row.played,
                f"{row.home_wins}-{row.home_draws}-{row.home_losses}",
                f"{row.away_wins}-{row.away_draws}-{row.away_losses}",
                row.goals_for,
                row.goals_against,
                row.goal_difference,
                row.points,
                f"{row.average_goals_for:.2f}",
                f"{row.average_goals_against:.2f}",
            )
        return str(values[index.column()])

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
            and 0 <= section < len(self.headers)
        ):
            return self.headers[section]
        return None

    def replace_rows(self, rows: tuple[HistoricalTeamSummary, ...]) -> None:
        self.beginResetModel()
        self._rows = rows
        self.endResetModel()


class HistoricalImportBatchTableModel(QAbstractTableModel):
    HEADERS = ("Imported", "Source", "Rows", "Imported", "Duplicates", "Invalid")

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._batches: tuple[HistoricalImportBatchInfo, ...] = ()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._batches)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self._batches):
            return None
        batch = self._batches[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            values = (
                _format_timestamp(batch.imported_at),
                batch.source_name,
                batch.rows_seen,
                batch.imported_count,
                batch.duplicate_count,
                batch.invalid_count,
            )
            return str(values[index.column()])
        if role == Qt.ItemDataRole.ToolTipRole:
            return (
                f"{batch.source_url}\n{batch.reuse_terms}\n"
                f"CSV SHA-256: {batch.file_sha256}"
            )
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

    def batch_at(self, row: int) -> HistoricalImportBatchInfo:
        return self._batches[row]

    def replace_batches(
        self, batches: tuple[HistoricalImportBatchInfo, ...]
    ) -> None:
        self.beginResetModel()
        self._batches = batches
        self.endResetModel()
