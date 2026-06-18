from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


DEFAULT_EXCLUDED_DIR_NAMES = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "backups",
    "pytest-cache",
    "pytest-tmp",
}


@dataclass(frozen=True)
class CsvMergeResult:
    output_path: Path
    source_files: list[Path]
    row_count: int
    duplicate_count: int
    fieldnames: list[str]
    includes_source_file: bool


def discover_csv_files(
    input_root: Path,
    *,
    csv_dir_name: str = "csv",
    excluded_dir_names: set[str] | None = None,
) -> list[Path]:
    root = input_root.resolve()
    excluded = excluded_dir_names or DEFAULT_EXCLUDED_DIR_NAMES
    discovered: list[Path] = []

    for current_root, dirnames, filenames in os.walk(root, topdown=True):
        current_path = Path(current_root)
        dirnames[:] = [name for name in dirnames if name not in excluded and not name.startswith(".")]
        if current_path.name != csv_dir_name:
            continue
        for filename in filenames:
            if Path(filename).suffix.lower() != ".csv":
                continue
            discovered.append((current_path / filename).resolve())

    discovered.sort(key=lambda path: path.as_posix())
    return discovered


def _read_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(f"CSV file has no header: {path}") from exc
    return header


def _normalize_row(row: dict[str, str | None], fieldnames: Sequence[str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for field in fieldnames:
        value = row.get(field, "")
        normalized[field] = "" if value is None else str(value)
    return normalized


def _row_signature(row: dict[str, str], fieldnames: Sequence[str]) -> tuple[str, ...]:
    return tuple(row.get(field, "") for field in fieldnames)


def merge_csv_files(
    input_files: Sequence[Path],
    output_path: Path,
    *,
    include_source_file: bool = False,
    source_root: Path | None = None,
) -> CsvMergeResult:
    files = [path.resolve() for path in input_files]
    if not files:
        raise ValueError("No CSV files were found to merge.")

    files.sort(key=lambda path: path.as_posix())
    expected_header = _read_header(files[0])
    output_fieldnames = ["source_file", *expected_header] if include_source_file else list(expected_header)

    output_path = output_path.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    row_count = 0
    duplicate_count = 0
    seen_rows: set[tuple[str, ...]] = set()
    with output_path.open("w", encoding="utf-8", newline="") as out_handle:
        writer = csv.DictWriter(out_handle, fieldnames=output_fieldnames, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()

        for path in files:
            header = _read_header(path)
            if header != expected_header:
                raise ValueError(
                    f"Header mismatch in {path}: expected {expected_header!r}, got {header!r}"
                )
            with path.open("r", encoding="utf-8-sig", newline="") as in_handle:
                reader = csv.DictReader(in_handle)
                if reader.fieldnames != expected_header:
                    raise ValueError(
                        f"Header mismatch in {path}: expected {expected_header!r}, got {reader.fieldnames!r}"
                    )
                for row in reader:
                    normalized = _normalize_row(row, expected_header)
                    signature = _row_signature(normalized, expected_header)
                    if signature in seen_rows:
                        duplicate_count += 1
                        continue
                    seen_rows.add(signature)
                    if include_source_file:
                        if source_root is None:
                            normalized["source_file"] = path.name
                        else:
                            normalized["source_file"] = path.resolve().relative_to(source_root.resolve()).as_posix()
                    writer.writerow(normalized)
                    row_count += 1

    return CsvMergeResult(
        output_path=output_path,
        source_files=list(files),
        row_count=row_count,
        duplicate_count=duplicate_count,
        fieldnames=output_fieldnames,
        includes_source_file=include_source_file,
    )
