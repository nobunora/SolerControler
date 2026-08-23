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
from uuid import uuid4

from google.auth import default as google_auth_default
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

from app.domain.constants import FileConstants
from app.operations.sync import TABLE_SPECS
from app.operations.firestore import open_firestore
from app.backup.device import build_device_settings_snapshot


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

# These files are reproducible or transient and would make backups large without aiding recovery.
IGNORED_SUFFIXES = {".pyc", ".pyo", ".log", ".tmp", ".bak"}

SOURCE_BACKUP_NAME = "source.zip"
SOURCE_MANIFEST_NAME = "source_manifest.json"
DATA_BACKUP_NAME = "data_snapshot.json.gz"
DATA_MANIFEST_NAME = "data_manifest.json"
DATA_BACKUP_PREFIX = "data_snapshot"
DATA_MANIFEST_PREFIX = "data_manifest"
DEVICE_BACKUP_PREFIX = "device_settings"


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
    keys: list[tuple[int, str | float]] = []
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


def build_firestore_snapshot(
    client: Any | None = None,
    *,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    firestore_client = client or open_firestore()
    captured_at = captured_at or utc_now()
    collections: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, int] = {}
    for table_name in TABLE_SPECS:
        rows = [dict(doc.to_dict() or {}) | {"_doc_id": doc.id} for doc in firestore_client.collection(table_name).stream()]
        rows.sort(key=lambda row: _row_sort_key(table_name, row))
        collections[table_name] = rows
        counts[table_name] = len(rows)
    return {
        "schema_version": 1,
        "backend": "firestore",
        "captured_at_utc": captured_at.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
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


def build_drive_service() -> Any:
    credentials, _ = google_auth_default(scopes=[DRIVE_SCOPE])
    refresh_token = getattr(credentials, "refresh_token", None)
    refresh = getattr(credentials, "refresh", None)
    if not credentials.valid and credentials.expired and refresh_token and callable(refresh):
        refresh(Request())
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
    return download_drive_file_bytes(service, file_id=file_id).decode("utf-8")


def download_drive_file_bytes(service: Any, *, file_id: str) -> bytes:
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
            return content
        return str(content).encode("utf-8")
    return buf.getvalue()


def read_drive_json(service: Any, *, folder_id: str, file_name: str) -> dict[str, Any] | None:
    existing = find_drive_file(service, folder_id=folder_id, file_name=file_name)
    if not existing:
        return None
    text = download_drive_file_text(service, file_id=existing["id"])
    if not text.strip():
        return None
    try:
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else None
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
        response = (
            service.files()
            .update(
                fileId=existing["id"],
                media_body=media,
                fields="id, name, size, md5Checksum, modifiedTime, webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )
        return dict(response) if isinstance(response, dict) else {}
    response = (
        service.files()
        .create(
            body=metadata,
            media_body=media,
            fields="id, name, size, md5Checksum, modifiedTime, webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )
    return dict(response) if isinstance(response, dict) else {}


def upload_new_file(
    service: Any,
    *,
    folder_id: str,
    file_name: str,
    local_path: Path,
    mime_type: str,
) -> dict[str, Any]:
    """Create an immutable Drive artifact without replacing another generation."""
    media = MediaFileUpload(str(local_path), mimetype=mime_type, resumable=False)
    response = (
        service.files()
        .create(
            body={"name": file_name, "parents": [folder_id]},
            media_body=media,
            fields="id, name, size, md5Checksum, modifiedTime, webViewLink",
            supportsAllDrives=True,
        )
        .execute()
    )
    return dict(response) if isinstance(response, dict) else {}


def _is_drive_storage_quota_error(error: Exception) -> bool:
    return isinstance(error, HttpError) and error.resp.status == 403 and "storage quota" in str(error).lower()


def _read_drive_gzip_json(service: Any, *, folder_id: str, file_name: str) -> dict[str, Any] | None:
    existing = find_drive_file(service, folder_id=folder_id, file_name=file_name)
    if not existing:
        return None
    try:
        payload = json.loads(gzip.decompress(download_drive_file_bytes(service, file_id=existing["id"])).decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _legacy_generation_entry(
    *,
    snapshot: dict[str, Any],
    manifest: dict[str, Any] | None,
) -> dict[str, Any]:
    captured_at = str(snapshot.get("captured_at_utc", "unknown"))
    return {
        "generation_id": f"legacy-{captured_at.replace(':', '').replace('-', '')}",
        "captured_at_utc": captured_at,
        "counts": snapshot.get("counts", {}),
        "snapshot": snapshot,
        "source_manifest": manifest or {},
        "device_settings": None,
    }


def _build_cumulative_data_archive(
    *,
    service: Any,
    folder_id: str,
    current_snapshot: dict[str, Any],
    current_generation_id: str,
    current_manifest: dict[str, Any],
    device_snapshot: dict[str, Any] | None,
    out_path: Path,
    max_generations: int = 14,
) -> tuple[BackupArtifact, BackupArtifact, int]:
    previous_snapshot = _read_drive_gzip_json(service, folder_id=folder_id, file_name=DATA_BACKUP_NAME)
    previous_manifest = read_drive_json(service, folder_id=folder_id, file_name=DATA_MANIFEST_NAME)
    generations: list[dict[str, Any]] = []
    if previous_snapshot:
        if previous_snapshot.get("backup_type") == "data_generations":
            generations = [entry for entry in previous_snapshot.get("generations", []) if isinstance(entry, dict)]
        elif previous_snapshot.get("backend") == "firestore":
            generations.append(_legacy_generation_entry(snapshot=previous_snapshot, manifest=previous_manifest))
    generations.append(
        {
            "generation_id": current_generation_id,
            "captured_at_utc": current_snapshot["captured_at_utc"],
            "counts": current_snapshot["counts"],
            "snapshot": current_snapshot,
            "source_manifest": current_manifest,
            "device_settings": device_snapshot,
        }
    )
    generations = generations[-max_generations:]
    archive_payload = {
        "schema_version": 2,
        "backup_type": "data_generations",
        "backend": "firestore",
        "latest_generation_id": current_generation_id,
        "generations": generations,
    }
    archive_artifact = write_gzip_json(archive_payload, out_path)
    archive_manifest = {
        "schema_version": 3,
        "backup_type": "data_generations",
        "backend": "firestore",
        "latest_generation_id": current_generation_id,
        "generation_count": len(generations),
        "archive_name": DATA_BACKUP_NAME,
        "archive_sha256": archive_artifact.sha256,
        "archive_size_bytes": archive_artifact.size_bytes,
        "generations": [
            {
                "generation_id": entry.get("generation_id"),
                "captured_at_utc": entry.get("captured_at_utc"),
                "counts": entry.get("counts", {}),
            }
            for entry in generations
        ],
    }
    manifest_path = out_path.with_name(f"{DATA_MANIFEST_PREFIX}-archive-{current_generation_id}.json")
    manifest_artifact = write_json(archive_manifest, manifest_path)
    return archive_artifact, manifest_artifact, len(generations)


def _upload_data_generation(
    service: Any,
    *,
    folder_id: str,
    snapshot_name: str,
    snapshot_path: Path,
    manifest_name: str,
    manifest_path: Path,
    current_snapshot: dict[str, Any],
    current_generation_id: str,
    current_manifest: dict[str, Any],
    device_snapshot: dict[str, Any] | None,
    target_dir: Path,
) -> dict[str, Any]:
    try:
        upload_new_file(service, folder_id=folder_id, file_name=snapshot_name, local_path=snapshot_path, mime_type="application/gzip")
        upload_new_file(service, folder_id=folder_id, file_name=manifest_name, local_path=manifest_path, mime_type="application/json")
        return {"mode": "immutable_files", "generation_count": 1}
    except Exception as error:
        if not _is_drive_storage_quota_error(error):
            raise
        archive_path = target_dir / f"data_snapshot-archive-{current_generation_id}.json.gz"
        archive_artifact, archive_manifest_artifact, generation_count = _build_cumulative_data_archive(
            service=service,
            folder_id=folder_id,
            current_snapshot=current_snapshot,
            current_generation_id=current_generation_id,
            current_manifest=current_manifest,
            device_snapshot=device_snapshot,
            out_path=archive_path,
        )
        upload_or_update_file(
            service,
            folder_id=folder_id,
            file_name=DATA_BACKUP_NAME,
            local_path=archive_artifact.path,
            mime_type="application/gzip",
        )
        upload_or_update_file(
            service,
            folder_id=folder_id,
            file_name=DATA_MANIFEST_NAME,
            local_path=archive_manifest_artifact.path,
            mime_type="application/json",
        )
        return {"mode": "cumulative_existing_file", "generation_count": generation_count}


def make_backup_generation_id(captured_at: datetime | None = None) -> str:
    timestamp = (captured_at or utc_now()).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}-{uuid4().hex[:8]}"


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
    generation_id: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "backup_type": "data",
        "generation_id": generation_id,
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
    if service is not None and folder_id is not None:
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
    include_device_readback: bool | None = None,
) -> dict[str, Any]:
    base = repo_root()
    target_dir = out_dir or (base / "artifacts" / "backups" / "drive")
    target_dir.mkdir(parents=True, exist_ok=True)
    captured_at = utc_now()
    generation_id = make_backup_generation_id(captured_at)
    snapshot_payload = build_firestore_snapshot(client=client, captured_at=captured_at)
    snapshot_name = f"{DATA_BACKUP_PREFIX}-{generation_id}.json.gz"
    manifest_name = f"{DATA_MANIFEST_PREFIX}-{generation_id}.json"
    snapshot_path = target_dir / snapshot_name
    snapshot_artifact = write_gzip_json(snapshot_payload, snapshot_path)
    manifest = make_data_manifest(
        snapshot_artifact=snapshot_artifact,
        snapshot_payload=snapshot_payload,
        generation_id=generation_id,
    )
    manifest_path = target_dir / manifest_name
    write_json(manifest, manifest_path)

    if include_device_readback is None:
        include_device_readback = os.getenv("DRIVE_BACKUP_DEVICE_READBACK", "false").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    device_artifact: BackupArtifact | None = None
    device_snapshot: dict[str, Any] | None = None
    if include_device_readback:
        device_path = target_dir / f"{DEVICE_BACKUP_PREFIX}-{generation_id}.json"
        device_snapshot = build_device_settings_snapshot()
        device_artifact = write_json(device_snapshot, device_path)
        manifest["device_readback"] = {
            "name": device_artifact.name,
            "size_bytes": device_artifact.size_bytes,
            "sha256": device_artifact.sha256,
        }
        write_json(manifest, manifest_path)

    uploaded = bool(service is not None and folder_id)
    drive_storage: dict[str, Any] | None = None
    if service is not None and folder_id is not None:
        drive_storage = _upload_data_generation(
            service,
            folder_id=folder_id,
            snapshot_name=snapshot_name,
            snapshot_path=snapshot_path,
            manifest_name=manifest_name,
            manifest_path=manifest_path,
            current_snapshot=snapshot_payload,
            current_generation_id=generation_id,
            current_manifest=manifest,
            device_snapshot=device_snapshot,
            target_dir=target_dir,
        )
        if device_artifact is not None and drive_storage["mode"] == "immutable_files":
            upload_new_file(
                service,
                folder_id=folder_id,
                file_name=device_artifact.name,
                local_path=device_artifact.path,
                mime_type="application/json",
            )

    return {
        "backup_type": "data",
        "created": True,
        "uploaded": uploaded,
        "generation_id": generation_id,
        "drive_storage": drive_storage,
        "snapshot": {
            "name": snapshot_artifact.name,
            "size_bytes": snapshot_artifact.size_bytes,
            "sha256": snapshot_artifact.sha256,
        },
        "manifest_path": str(manifest_path),
        "counts": snapshot_payload["counts"],
        "device_readback": (
            {
                "name": device_artifact.name,
                "size_bytes": device_artifact.size_bytes,
                "sha256": device_artifact.sha256,
            }
            if device_artifact is not None
            else None
        ),
    }
