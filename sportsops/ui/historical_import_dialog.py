from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)


class HistoricalImportDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import Historical Football CSV")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        notice = QLabel(
            "Choose a local CSV file, confirm its competition and season, and "
            "record the source and reuse terms. Review rights before importing. "
            "The importer validates and deduplicates results; it does not add "
            "missing statistics or connect results to markets."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)

        form = QFormLayout()
        self.file_path = QLineEdit()
        self.file_path.setReadOnly(True)
        browse_row = QHBoxLayout()
        browse_row.addWidget(self.file_path, 1)
        browse_button = QPushButton("Browse...")
        browse_button.clicked.connect(self._browse)
        browse_row.addWidget(browse_button)
        form.addRow("CSV file", browse_row)

        self.competition = QLineEdit()
        self.country = QLineEdit()
        self.season = QLineEdit()
        self.source_name = QLineEdit()
        self.source_url = QLineEdit()
        self.reuse_terms = QLineEdit()
        self.reuse_terms.setPlaceholderText(
            "Describe the license or permission basis for this import"
        )
        form.addRow("Competition", self.competition)
        form.addRow("Country", self.country)
        form.addRow("Season", self.season)
        self.season_start = QDateEdit(QDate(1900, 1, 1))
        self.season_start.setCalendarPopup(True)
        self.season_end = QDateEdit(QDate(2100, 12, 31))
        self.season_end.setCalendarPopup(True)
        form.addRow("Season start", self.season_start)
        form.addRow("Season end", self.season_end)
        form.addRow("Source name", self.source_name)
        form.addRow("Source URL", self.source_url)
        form.addRow("Reuse terms / permission", self.reuse_terms)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def import_details(self) -> dict[str, object]:
        return {
            "file_path": self.file_path.text(),
            "competition_name": self.competition.text().strip(),
            "country": self.country.text().strip(),
            "season_name": self.season.text().strip(),
            "season_start": self.season_start.date().toPython(),
            "season_end": self.season_end.date().toPython(),
            "source_name": self.source_name.text().strip(),
            "source_url": self.source_url.text().strip(),
            "reuse_terms": self.reuse_terms.text().strip(),
        }

    def _browse(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select a historical results CSV", "", "CSV files (*.csv)"
        )
        if file_path:
            self.file_path.setText(file_path)

    def _validate_and_accept(self) -> None:
        details = self.import_details()
        required_fields = (
            ("CSV file", details["file_path"]),
            ("competition", details["competition_name"]),
            ("country", details["country"]),
            ("season", details["season_name"]),
            ("source name", details["source_name"]),
            ("source URL", details["source_url"]),
            ("reuse terms", details["reuse_terms"]),
        )
        missing = [label for label, value in required_fields if not value]
        if missing:
            QMessageBox.warning(
                self,
                "Import details required",
                "Complete the following fields before importing: "
                + ", ".join(missing),
            )
            return
        if details["season_start"] > details["season_end"]:
            QMessageBox.warning(
                self,
                "Invalid season range",
                "Season start date must not be after its end date.",
            )
            return
        self.accept()
