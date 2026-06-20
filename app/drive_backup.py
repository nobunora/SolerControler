from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from google.auth import default as google_auth_default
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from app.constants import FileConstants
from app.db_sync import TABLE_SPECS
from app.firestore_ops import open_firestore


DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"

IGNORED_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".venv",
    "node_modules",
    "dist",
    "artifacts",
}

IGNORED_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.dev",
    ".env.development",
    ".env.production",
    "firebase-debug.log",
    "devserver.log",
    "devserver.err.log",
}

IGNORED_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp", ".bak"}

SOURCE_BACKUP_NAME = "source.zip"
SOURCE_MANIFEST_NAME = "source_manifest.json"
DATA_BACKUP_NAME = "data_snapshot.json.gz"
DATA_MANIFEST_NAME = "data_manifest.json"


@dataclass(frozen=True)
class BackupArtifact:
    name: str
    path: Path
    sha256: str
    size_bytes: int


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _normalized_relpath(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _should_ignore_path(root: Path, path: Path) -> bool:
    rel = path.relative_to(root)
    if any(part in IGNORED_DIRS for part in rel.parts):
        return True
    if path.name in IGNORED_FILE_NAMES:
        return True
    if path.name.startswith(".env") and path.name != ".env.example":
        return True
    if path.suffix.lower() in IGNORED_SUFFIXES:
        return True
    return False


def collect_source_files(root: Path | None = None) -> list[Path]:
    base = root or repo_root()
    files: list[Path] = []
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        if _should_ignore_path(base, path):
            continue
        files.append(path)
    files.sort(key=lambda p: _normalized_relpath(base, p))
    return files


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(FileConstants.DEFAULT_CHUNK_SIZE_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_source_tree(root: Path | None = None, files: Iterable[Path] | None = None) -> str:
    base = root or repo_root()
    entries = list(files) if files is not None else collect_source_files(base)
    digest = hashlib.sha256()
    for path in entries:
        rel = _normalized_relpath(base, path)
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(FileConstants.DEFAULT_CHUNK_SIZE_BYTES), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def write_source_zip(root: Path | None = None, out_path: Path | None = None) -> BackupArtifact:
    base = root or repo_root()
    files = collect_source_files(base)
    if out_path is None:
        tmp_dir = Path(tempfile.mkdtemp(prefix="drive-source-backup-"))
        out_path = tmp_dir / SOURCE_BACKUP_NAME
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            zf.write(path, arcname=_normalized_relpath(base, path))
    return BackupArtifact(
        name=SOURCE_BACKUP_NAME,
        path=out_path,
        sha256=hash_file(out_path),
        size_bytes=out_path.stat().st_size,
    )


def _row_sort_key(table_name: str, row: dict[str, Any]) -> tuple[Any, ...]:
    spec = TABLE_SPECS[table_name]
    keys = []
    for key in spec["key_cols"]:
        value = row.get(key)
        if key == "hour" and value is not None:
            try:
                value = int(value)
            except (TypeError, ValueError):
                pass
        if value is None:
            keys.append((2, ""))
        elif isinstance(value, bool):
            keys.append((0, int(value)))
        elif isinstance(value, (int, float)):
            keys.append((0, value))
        elif isinstance(value, str):
            try:
                keys.append((0, int(value)))
            except ValueError:
                keys.append((1, value))
        else:
            keys.append((1, str(value)))
    return tuple(keys)


def build_firestore_snapshot(client: Any | None = None) -> dict[str, Any]:
    firestore_client = client or open_firestore()
    collections: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, int] = {}
    for table_name in TABLE_SPECS:
        rows = [dict(doc.to_dict() or {}) | {"_doc_id": doc.id} for doc in firestore_client.collection(table_name).stream()]
        rows.sort(key=lambda row, table_name=table_name: _row_sort_key(table_name, row))
        collections[table_name] = rows
        counts[table_name] = len(rows)
    return {
        "schema_version": 1,
        "backend": "firestore",
        "captured_at_utc": utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "collections": collections,
        "counts": counts,
    }


def write_gzip_json(payload: dict[str, Any], out_path: Path) -> BackupArtifact:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    with gzip.open(out_path, "wb", compresslevel=9) as f:
        f.write(raw)
    return BackupArtifact(
        name=out_path.name,
        path=out_path,
        sha256=hash_file(out_path),
        size_bytes=out_path.stat().st_size,
    )


def write_json(payload: dict[str, Any], out_path: Path) -> BackupArtifact:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return BackupArtifact(
        name=out_path.name,
        path=out_path,
        sha256=hash_file(out_path),
        size_bytes=out_path.stat().st_size,
    )


def build_drive_service():
    credentials, _ = google_auth_default(scopes=[DRIVE_SCOPE])
    if not credentials.valid and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _escape_drive_query(value: str) -> str:
    return value.replace("'", r"\'")


def find_drive_file(service: Any, *, folder_id: str, file_name: str) -> dict[str, Any] | None:
    escaped_name = _escape_drive_query(file_name)
    query = f"name = '{escaped_name}' and '{folder_id}' in parents and trashed = false"
    result = (
        service.files()
        .list(
            q=query,
            spaces="drive",
            fields="files(id, name, modifiedTime, size, md5Checksum)",
            orderBy="modifiedTime desc",
            pageSize=10,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        .execute()
    )
    files = result.get("files", []) or []
    return files[0] if files else None


def download_drive_file_text(service: Any, *, file_id: str) -> str:
    request = service.files().get_media(fileId=file_id, supportsAllDrives=True)
    buf = io.BytesIO()
    downloader = None
    try:
        from googleapiclient.http import MediaIoBaseDownload

        downloader = MediaIoBaseDownload(buf, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
    except Exception:
        # Fallback to streaming via request.execute() if download helper is unavailable.
        content = request.execute()
        if isinstance(content, bytes):
            return content.decode("utf-8")
        return str(content)
    return buf.getvalue().decode("utf-8")


def read_drive_json(service: Any, *, folder_id: str, file_name: str) -> dict[str, Any] | None:
    existing = find_drive_file(service, folder_id=folder_id, file_name=file_name)
    if not existing:
        return None
    text = download_drive_file_text(service, file_id=existing["id"])
    if not text.strip():
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def upload_or_update_file(
    service: Any,
    *,
    folder_id: str,
    file_name: str,
    local_path: Path,
    mime_type: str,
) -> dict[str, Any]:
    existing = find_drive_file(service, folder_id=folder_id, file_name=file_name)
    media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=False)
    metadata = {"name": file_name, "parents": [folder_id]}
    if existing:
        return (
            service.files()
            .update(
                fileId=existing["id"],
                media_body=media,
                fields="id, name, size, md5Checksum, modifiedTime, webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
    return (
        service.files()
        .create(
            body=metadata,
            media_body=media,
            fields="id, name, size, md5Checksum, modifiedTime, webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )


def make_source_manifest(
    *,
    source_artifact: BackupArtifact,
    file_count: int,
    source_fingerprint: str,
    source_files: list[Path],
    repo_root_path: Path,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "backup_type": "source",
        "captured_at_utc": utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "repo_root": str(repo_root_path),
        "file_count": file_count,
        "source_fingerprint": source_fingerprint,
        "archive_name": source_artifact.name,
        "archive_sha256": source_artifact.sha256,
        "archive_size_bytes": source_artifact.size_bytes,
        "included_files": [_normalized_relpath(repo_root_path, path) for path in source_files],
    }


def make_data_manifest(
    *,
    snapshot_artifact: BackupArtifact,
    snapshot_payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "backup_type": "data",
        "captured_at_utc": snapshot_payload["captured_at_utc"],
        "backend": snapshot_payload["backend"],
        "archive_name": snapshot_artifact.name,
        "archive_sha256": snapshot_artifact.sha256,
        "archive_size_bytes": snapshot_artifact.size_bytes,
        "counts": snapshot_payload["counts"],
        "collections": list(snapshot_payload["collections"].keys()),
    }


def source_backup_needed(
    service: Any | None,
    *,
    folder_id: str | None,
    source_fingerprint: str,
) -> bool:
    if service is None or not folder_id:
        return True
    existing = read_drive_json(service, folder_id=folder_id, file_name=SOURCE_MANIFEST_NAME)
    if not existing:
        return True
    return str(existing.get("source_fingerprint", "")) != source_fingerprint


def export_source_backup(
    *,
    service: Any | None,
    folder_id: str | None,
    repo_root_path: Path | None = None,
    out_dir: Path | None = None,
) -> tuple[bool, dict[str, Any]]:
    base = repo_root_path or repo_root()
    files = collect_source_files(base)
    fingerprint = hash_source_tree(base, files)
    if not source_backup_needed(service, folder_id=folder_id, source_fingerprint=fingerprint):
        return False, {
            "backup_type": "source",
            "created": False,
            "reason": "unchanged",
            "source_fingerprint": fingerprint,
        }

    target_dir = out_dir or (base / "artifacts" / "backups" / "drive")
    target_dir.mkdir(parents=True, exist_ok=True)
    source_archive = write_source_zip(base, target_dir / SOURCE_BACKUP_NAME)
    source_manifest = make_source_manifest(
        source_artifact=source_archive,
        file_count=len(files),
        source_fingerprint=fingerprint,
        source_files=files,
        repo_root_path=base,
    )
    manifest_path = target_dir / SOURCE_MANIFEST_NAME
    write_json(source_manifest, manifest_path)

    uploaded = bool(service is not None and folder_id)
    if uploaded:
        upload_or_update_file(service, folder_id=folder_id, file_name=SOURCE_BACKUP_NAME, local_path=source_archive.path, mime_type="application/zip")
        upload_or_update_file(service, folder_id=folder_id, file_name=SOURCE_MANIFEST_NAME, local_path=manifest_path, mime_type="application/json")

    return True, {
        "backup_type": "source",
        "created": True,
        "uploaded": uploaded,
        "reason": "uploaded" if uploaded else "local-only",
        "source_fingerprint": fingerprint,
        "archive": {
            "name": source_archive.name,
            "size_bytes": source_archive.size_bytes,
            "sha256": source_archive.sha256,
        },
        "manifest_path": str(manifest_path),
    }


def export_data_backup(
    *,
    service: Any | None,
    folder_id: str | None,
    client: Any | None = None,
    out_dir: Path | None = None,
) -> dict[str, Any]:
    base = repo_root()
    target_dir = out_dir or (base / "artifacts" / "backups" / "drive")
    target_dir.mkdir(parents=True, exist_ok=True)
    snapshot_payload = build_firestore_snapshot(client=client)
    snapshot_path = target_dir / DATA_BACKUP_NAME
    snapshot_artifact = write_gzip_json(snapshot_payload, snapshot_path)
    manifest = make_data_manifest(snapshot_artifact=snapshot_artifact, snapshot_payload=snapshot_payload)
    manifest_path = target_dir / DATA_MANIFEST_NAME
    write_json(manifest, manifest_path)

    uploaded = bool(service is not None and folder_id)
    if uploaded:
        upload_or_update_file(service, folder_id=folder_id, file_name=DATA_BACKUP_NAME, local_path=snapshot_path, mime_type="application/gzip")
        upload_or_update_file(service, folder_id=folder_id, file_name=DATA_MANIFEST_NAME, local_path=manifest_path, mime_type="application/json")

    return {
        "backup_type": "data",
        "created": True,
        "uploaded": uploaded,
        "snapshot": {
            "name": snapshot_artifact.name,
            "size_bytes": snapshot_artifact.size_bytes,
            "sha256": snapshot_artifact.sha256,
        },
        "manifest_path": str(manifest_path),
        "counts": snapshot_payload["counts"],
    }
