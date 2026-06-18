from __future__ import annotations

import csv
from pathlib import Path

from app.csv_merge import discover_csv_files, merge_csv_files


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def test_discover_csv_files_finds_only_csv_dirs(tmp_path: Path) -> None:
    _write_csv(tmp_path / "artifacts" / "run1" / "csv" / "a.csv", ["date", "value"], [["2026-06-01", "1"]])
    _write_csv(tmp_path / "artifacts" / "run2" / "csv" / "b.csv", ["date", "value"], [["2026-06-02", "2"]])
    _write_csv(tmp_path / "artifacts" / "history.csv", ["date", "value"], [["2026-06-03", "3"]])

    files = discover_csv_files(tmp_path / "artifacts")
    rels = [path.relative_to(tmp_path).as_posix() for path in files]

    assert "artifacts/run1/csv/a.csv" in rels
    assert "artifacts/run2/csv/b.csv" in rels
    assert "artifacts/history.csv" not in rels


def test_merge_csv_files_with_source_file_column(tmp_path: Path) -> None:
    source_root = tmp_path / "artifacts"
    file_a = source_root / "run1" / "csv" / "a.csv"
    file_b = source_root / "run2" / "csv" / "b.csv"
    _write_csv(file_a, ["date", "value"], [["2026-06-01", "1"], ["2026-06-02", "2"]])
    _write_csv(file_b, ["date", "value"], [["2026-06-02", "2"], ["2026-06-03", "3"]])

    output = tmp_path / "merged.csv"
    result = merge_csv_files([file_b, file_a], output, include_source_file=True, source_root=source_root)

    assert result.row_count == 3
    assert result.duplicate_count == 1
    assert result.output_path == output.resolve()

    with output.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert rows[0]["source_file"] == "run1/csv/a.csv"
    assert rows[0]["date"] == "2026-06-01"
    assert rows[1]["date"] == "2026-06-02"
    assert rows[2]["source_file"] == "run2/csv/b.csv"
    assert rows[2]["value"] == "3"


def test_merge_csv_files_rejects_header_mismatch(tmp_path: Path) -> None:
    source_root = tmp_path / "artifacts"
    file_a = source_root / "run1" / "csv" / "a.csv"
    file_b = source_root / "run2" / "csv" / "b.csv"
    _write_csv(file_a, ["date", "value"], [["2026-06-01", "1"]])
    _write_csv(file_b, ["date", "other"], [["2026-06-02", "2"]])

    try:
        merge_csv_files([file_a, file_b], tmp_path / "merged.csv", source_root=source_root)
    except ValueError as exc:
        assert "Header mismatch" in str(exc)
    else:
        raise AssertionError("Expected a header mismatch error")
