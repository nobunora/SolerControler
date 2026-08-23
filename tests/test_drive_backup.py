from __future__ import annotations

import json
from pathlib import Path

from app.backup.drive import collect_source_files, export_data_backup, hash_source_tree, _row_sort_key
from app.operations.sync import TABLE_SPECS


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


class _EmptyFirestoreCollection:
    def stream(self):
        return []


class _EmptyFirestoreClient:
    def collection(self, _name: str) -> _EmptyFirestoreCollection:
        return _EmptyFirestoreCollection()


class _FakeRequest:
    def __init__(self, response: dict[str, str]) -> None:
        self.response = response

    def execute(self) -> dict[str, str]:
        return self.response


class _FakeDriveFiles:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.updated = 0

    def create(self, **kwargs: object) -> _FakeRequest:
        self.created.append(kwargs)
        return _FakeRequest({"id": str(len(self.created)), "name": str(kwargs["body"])})

    def update(self, **_kwargs: object) -> _FakeRequest:
        self.updated += 1
        return _FakeRequest({})


class _FakeDriveService:
    def __init__(self) -> None:
        self.file_api = _FakeDriveFiles()

    def files(self) -> _FakeDriveFiles:
        return self.file_api


def test_data_backup_creates_a_new_generation_each_time(tmp_path: Path) -> None:
    first = export_data_backup(service=None, folder_id=None, client=_EmptyFirestoreClient(), out_dir=tmp_path)
    second = export_data_backup(service=None, folder_id=None, client=_EmptyFirestoreClient(), out_dir=tmp_path)

    assert first["generation_id"] != second["generation_id"]
    assert first["snapshot"]["name"] != second["snapshot"]["name"]
    assert first["manifest_path"] != second["manifest_path"]
    assert len(list(tmp_path.glob("data_snapshot-*.json.gz"))) == 2
    assert len(list(tmp_path.glob("data_manifest-*.json"))) == 2
    manifest = json.loads(Path(second["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["schema_version"] == 2
    assert manifest["generation_id"] == second["generation_id"]


def test_data_backup_uploads_new_files_without_update(tmp_path: Path) -> None:
    service = _FakeDriveService()
    result = export_data_backup(
        service=service,
        folder_id="folder",
        client=_EmptyFirestoreClient(),
        out_dir=tmp_path,
    )

    names = [str(call["body"]["name"]) for call in service.file_api.created]
    assert result["uploaded"] is True
    assert len(names) == 2
    assert all(result["generation_id"] in name for name in names)
    assert service.file_api.updated == 0


def test_data_backup_can_include_device_readback(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "app.backup.drive.build_device_settings_snapshot",
        lambda: {"schema_version": 1, "source": "kpnet_readback", "settings": {"socChargeMode": "50"}},
    )

    result = export_data_backup(
        service=None,
        folder_id=None,
        client=_EmptyFirestoreClient(),
        out_dir=tmp_path,
        include_device_readback=True,
    )

    device = result["device_readback"]
    assert device is not None
    assert result["generation_id"] in device["name"]
    assert (tmp_path / device["name"]).exists()
