import json
from datetime import date
from pathlib import Path

from sportsops.database.service import DatabaseService
from sportsops.models.historical import HistoricalImportResult


_BUNDLE_DIRECTORY = Path(__file__).resolve().parent / "historical"


def import_bundled_historical_data(
    database: DatabaseService,
    bundle_directory: Path = _BUNDLE_DIRECTORY,
) -> tuple[HistoricalImportResult, ...]:
    manifest_path = bundle_directory / "bundle_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    datasets = manifest.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise ValueError(f"Historical bundle manifest has no datasets: {manifest_path}")
    expected_total = manifest.get("completed_match_count")
    declared_total = sum(
        _expected_match_count(dataset, manifest_path) for dataset in datasets
    )
    if (
        not isinstance(expected_total, int)
        or isinstance(expected_total, bool)
        or expected_total != declared_total
    ):
        raise ValueError(
            f"Historical bundle match count does not match its datasets: "
            f"{manifest_path}"
        )

    results = []
    root = bundle_directory.resolve()
    for dataset in datasets:
        expected_count = _expected_match_count(dataset, manifest_path)
        filename = dataset.get("file")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError(f"Invalid historical bundle filename: {filename!r}")
        file_path = (root / filename).resolve()
        if file_path.parent != root:
            raise ValueError(f"Historical bundle path escapes its data directory: {filename}")
        result = database.import_historical_csv(
            file_path,
            competition_name=dataset["competition"],
            country=dataset["country"],
            season_name=dataset["season"],
            season_start=date.fromisoformat(dataset["season_start"]),
            season_end=date.fromisoformat(dataset["season_end"]),
            source_name=dataset["source_name"],
            source_url=dataset["source_url"],
            reuse_terms=dataset["reuse_terms"],
        )
        if result.invalid_count:
            raise ValueError(
                f"Bundled historical CSV {filename} has "
                f"{result.invalid_count} invalid row(s)."
            )
        if result.rows_seen != expected_count:
            raise ValueError(
                f"Bundled historical CSV {filename} has {result.rows_seen} rows; "
                f"manifest declares {expected_count}."
            )
        if result.imported_count + result.duplicate_count != expected_count:
            raise ValueError(
                f"Bundled historical CSV {filename} imported or deduplicated "
                f"{result.imported_count + result.duplicate_count} rows; "
                f"manifest declares {expected_count}."
            )
        results.append(result)
    return tuple(results)


def _expected_match_count(dataset: object, manifest_path: Path) -> int:
    if not isinstance(dataset, dict):
        raise ValueError(f"Invalid dataset entry in historical manifest: {manifest_path}")
    count = dataset.get("completed_matches")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ValueError(
            f"Invalid completed match count in historical manifest: {manifest_path}"
        )
    return count
