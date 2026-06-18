from __future__ import annotations

from pathlib import Path

from app.drive_backup import collect_source_files, hash_source_tree, _row_sort_key
from app.db_sync import TABLE_SPECS


def test_collect_source_files_ignores_artifacts_and_env(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    (tmp_path / "artifacts").mkdir()
    (tmp_path / ".git").mkdir()
    (tmp_path / "docs").mkdir()

    keep_files = [
        tmp_path / "README.md",
        tmp_path / ".env.example",
        tmp_path / "app" / "main.py",
        tmp_path / "docs" / "guide.md",
    ]
    ignored_files = [
        tmp_path / ".env",
        tmp_path / ".env.local",
        tmp_path / "artifacts" / "backup.zip",
        tmp_path / ".git" / "config",
    ]
    for path in keep_files + ignored_files:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(path.name, encoding="utf-8")

    files = collect_source_files(tmp_path)
    rels = [path.relative_to(tmp_path).as_posix() for path in files]

    assert "README.md" in rels
    assert ".env.example" in rels
    assert "app/main.py" in rels
    assert "docs/guide.md" in rels
    assert ".env" not in rels
    assert ".env.local" not in rels
    assert "artifacts/backup.zip" not in rels
    assert ".git/config" not in rels


def test_hash_source_tree_changes_when_file_changes(tmp_path: Path) -> None:
    a = tmp_path / "src" / "a.txt"
    b = tmp_path / "src" / "b.txt"
    a.parent.mkdir(parents=True, exist_ok=True)
    a.write_text("hello", encoding="utf-8")
    b.write_text("world", encoding="utf-8")

    first = hash_source_tree(tmp_path)
    a.write_text("hello updated", encoding="utf-8")
    second = hash_source_tree(tmp_path)

    assert first != second


def test_row_sort_key_handles_mixed_types() -> None:
    table_name = next(iter(TABLE_SPECS))
    spec = TABLE_SPECS[table_name]
    row = {key: ("1" if key != "hour" else "12") for key in spec["key_cols"]}
    key = _row_sort_key(table_name, row)

    assert isinstance(key, tuple)
    assert len(key) == len(spec["key_cols"])
