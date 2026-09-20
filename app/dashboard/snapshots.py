from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.dashboard.data import load_dashboard_slice
from app.dashboard.models import DashboardSlice


SNAPSHOT_SCHEMA_VERSION = 1
BOOTSTRAP_KIND = "bootstrap"
HISTORY_KIND = "history"
_SNAPSHOT_FILENAMES = {
    BOOTSTRAP_KIND: "bootstrap.json.gz",
    HISTORY_KIND: "history.json.gz",
}
_DAILY_SERIES = (
    "pv_daily",
    "energy_daily",
    "cost_daily",
    "battery_daily",
    "battery_flow_daily",
)
_SNAPSHOT_CACHE: dict[str, tuple[float, "SnapshotArtifact"]] = {}


@dataclass(frozen=True)
class SnapshotArtifact:
    kind: str
    raw: bytes
    gzip_bytes: bytes
    etag: str

    @property
    def content_length(self) -> int:
        return len(self.gzip_bytes)


def _parse_gs_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "gs" or not parsed.netloc:
        raise ValueError(f"invalid GCS URI: {uri}")
    return parsed.netloc, parsed.path.lstrip("/")


def dashboard_snapshot_prefix() -> str:
    explicit = os.getenv("DASHBOARD_SNAPSHOT_GCS_PREFIX", "").strip()
    if explicit:
        return explicit.rstrip("/")
    archive = os.getenv("NIGHT_PLAN_ARCHIVE_GCS_PREFIX", "").strip()
    if archive:
        return f"{archive.rstrip('/')}/dashboard_snapshots"
    return ""


def dashboard_snapshot_local_dir() -> Path:
    explicit = os.getenv("DASHBOARD_SNAPSHOT_LOCAL_DIR", "").strip()
    if explicit:
        return Path(explicit)
    return Path(os.getenv("ARTIFACTS_DIR", "artifacts")) / "dashboard_snapshots"


def snapshot_gcs_uri(kind: str) -> str:
    if kind not in _SNAPSHOT_FILENAMES:
        raise ValueError(f"unsupported dashboard snapshot kind: {kind}")
    prefix = dashboard_snapshot_prefix()
    if not prefix:
        return ""
    return f"{prefix}/{_SNAPSHOT_FILENAMES[kind]}"


def _payload_from_slice(value: DashboardSlice) -> dict[str, Any]:
    return {
        **value.data.__dict__,
        "meta": dict(value.meta),
    }


def _snapshot_meta(meta: dict[str, Any], *, kind: str) -> dict[str, Any]:
    return {
        **meta,
        "snapshot_kind": kind,
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
    }


def _latest_hourly_date(payload: dict[str, Any]) -> str | None:
    rows = payload.get("forecast_hourly") or []
    dates = sorted({str(row.get("date")) for row in rows if row.get("date")})
    schedule = payload.get("latest_schedule") or {}
    plan_date = str(schedule.get("plan_date") or "")
    if plan_date and plan_date in dates:
        return plan_date
    return dates[-1] if dates else None


def build_bootstrap_payload(value: DashboardSlice) -> dict[str, Any]:
    payload = _payload_from_slice(value)
    latest_hourly = _latest_hourly_date(payload)
    if latest_hourly:
        payload["forecast_hourly"] = [
            row for row in payload.get("forecast_hourly", []) if str(row.get("date") or "") == latest_hourly
        ]
    else:
        payload["forecast_hourly"] = []
    payload["meta"] = _snapshot_meta(payload.get("meta") or {}, kind=BOOTSTRAP_KIND)
    return payload


def _merge_rows(target: dict[str, dict[str, Any]], rows: list[dict[str, Any]]) -> None:
    for row in rows:
        day = str(row.get("date") or "")
        if day:
            target[day] = row


def _history_bounds(series: dict[str, dict[str, dict[str, Any]]]) -> tuple[str | None, str | None]:
    days: set[str] = set()
    for rows in series.values():
        days.update(rows)
    if not days:
        return None, None
    ordered = sorted(days)
    return ordered[0], ordered[-1]


def _history_window_days(oldest: str | None, newest: str | None) -> int:
    if not oldest or not newest:
        return 0
    try:
        return max(1, (date.fromisoformat(newest) - date.fromisoformat(oldest)).days + 1)
    except ValueError:
        return 0


def _empty_history_series() -> dict[str, dict[str, dict[str, Any]]]:
    return {name: {} for name in _DAILY_SERIES}


def _series_from_existing_history(payload: dict[str, Any] | None) -> dict[str, dict[str, dict[str, Any]]]:
    series = _empty_history_series()
    if not payload:
        return series
    for name in _DAILY_SERIES:
        _merge_rows(series[name], list(payload.get(name) or []))
    return series


def _history_payload(
    *,
    series: dict[str, dict[str, dict[str, Any]]],
    bootstrap_meta: dict[str, Any],
) -> dict[str, Any]:
    oldest, newest = _history_bounds(series)
    global_oldest = bootstrap_meta.get("global_oldest_date") or oldest
    global_newest = bootstrap_meta.get("global_newest_date") or newest
    return {
        "pv_daily": [series["pv_daily"][day] for day in sorted(series["pv_daily"])],
        "forecast_hourly": [],
        "energy_daily": [series["energy_daily"][day] for day in sorted(series["energy_daily"])],
        "cost_daily": [series["cost_daily"][day] for day in sorted(series["cost_daily"])],
        "cost_monthly": [],
        "battery_daily": [series["battery_daily"][day] for day in sorted(series["battery_daily"])],
        "battery_flow_daily": [
            series["battery_flow_daily"][day] for day in sorted(series["battery_flow_daily"])
        ],
        "model_parameters": [],
        "latest_schedule": {},
        "dashboard_warnings": [],
        "pv_forecast_diagnostics": {},
        "daily_review": {},
        "daily_reviews": [],
        "meta": _snapshot_meta(
            {
                "window_days": _history_window_days(oldest, newest),
                "oldest_loaded_date": oldest,
                "newest_loaded_date": newest,
                "global_oldest_date": global_oldest,
                "global_newest_date": global_newest,
                "has_more_before": bool(global_oldest and oldest and oldest > str(global_oldest)),
            },
            kind=HISTORY_KIND,
        ),
    }


def artifact_from_payload(kind: str, payload: dict[str, Any]) -> SnapshotArtifact:
    if kind not in _SNAPSHOT_FILENAMES:
        raise ValueError(f"unsupported dashboard snapshot kind: {kind}")
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    gzip_bytes = gzip.compress(raw, compresslevel=9, mtime=0)
    digest = hashlib.sha256(raw).hexdigest()
    return SnapshotArtifact(
        kind=kind,
        raw=raw,
        gzip_bytes=gzip_bytes,
        etag=f'W/"{digest}"',
    )


def _artifact_from_gzip(kind: str, payload: bytes) -> SnapshotArtifact:
    raw = gzip.decompress(payload)
    digest = hashlib.sha256(raw).hexdigest()
    return SnapshotArtifact(
        kind=kind,
        raw=raw,
        gzip_bytes=payload,
        etag=f'W/"{digest}"',
    )


def _read_existing_history_payload(*, storage_client: Any | None = None) -> dict[str, Any] | None:
    artifact = load_precomputed_snapshot(HISTORY_KIND, storage_client=storage_client, use_cache=False)
    if artifact is None:
        return None
    parsed = json.loads(artifact.raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        return None
    meta = parsed.get("meta")
    if not isinstance(meta, dict):
        return None
    if meta.get("snapshot_kind") != HISTORY_KIND:
        return None
    if meta.get("snapshot_schema_version") != SNAPSHOT_SCHEMA_VERSION:
        return None
    return parsed


def build_dashboard_snapshot_payloads(
    db_path: Path,
    *,
    existing_history: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    bootstrap_slice = load_dashboard_slice(
        db_path,
        end_date=None,
        window_days=31,
        include_static=True,
    )
    bootstrap = build_bootstrap_payload(bootstrap_slice)
    bootstrap_meta = bootstrap.get("meta") or {}
    series = _series_from_existing_history(existing_history)
    for name in _DAILY_SERIES:
        _merge_rows(series[name], list(bootstrap.get(name) or []))

    global_oldest = str(bootstrap_meta.get("global_oldest_date") or "")
    existing_oldest, _ = _history_bounds(series)
    cursor_oldest = existing_oldest or str(bootstrap_meta.get("oldest_loaded_date") or "")
    has_more = bool(global_oldest and cursor_oldest and cursor_oldest > global_oldest)

    while has_more:
        try:
            previous_day = (date.fromisoformat(cursor_oldest) - timedelta(days=1)).isoformat()
        except ValueError:
            break
        chunk = load_dashboard_slice(
            db_path,
            end_date=previous_day,
            window_days=365,
            include_static=False,
        )
        chunk_payload = _payload_from_slice(chunk)
        previous_oldest = cursor_oldest
        for name in _DAILY_SERIES:
            _merge_rows(series[name], list(chunk_payload.get(name) or []))
        next_oldest, _ = _history_bounds(series)
        if not next_oldest or next_oldest >= previous_oldest:
            break
        cursor_oldest = next_oldest
        has_more = bool(global_oldest and cursor_oldest > global_oldest)

    history = _history_payload(series=series, bootstrap_meta=bootstrap_meta)
    return {
        BOOTSTRAP_KIND: bootstrap,
        HISTORY_KIND: history,
    }


def _upload_artifact(artifact: SnapshotArtifact, *, storage_client: Any) -> str:
    uri = snapshot_gcs_uri(artifact.kind)
    bucket_name, blob_name = _parse_gs_uri(uri)
    blob = storage_client.bucket(bucket_name).blob(blob_name)
    blob.cache_control = "private, max-age=0, must-revalidate"
    blob.metadata = {
        "dashboard_snapshot_schema": str(SNAPSHOT_SCHEMA_VERSION),
        "sha256": hashlib.sha256(artifact.raw).hexdigest(),
    }
    blob.upload_from_string(artifact.gzip_bytes, content_type="application/gzip")
    return uri


def write_dashboard_snapshots(
    db_path: Path,
    *,
    storage_client: Any | None = None,
) -> dict[str, dict[str, Any]]:
    prefix = dashboard_snapshot_prefix()
    if prefix and storage_client is None:
        from google.cloud.storage import Client

        storage_client = Client()

    full_history_rebuild = _full_history_rebuild_requested()
    existing_history = (
        None
        if full_history_rebuild
        else _read_existing_history_payload(storage_client=storage_client)
    )
    payloads = build_dashboard_snapshot_payloads(db_path, existing_history=existing_history)
    results: dict[str, dict[str, Any]] = {}

    for kind, payload in payloads.items():
        artifact = artifact_from_payload(kind, payload)
        if prefix:
            uri = _upload_artifact(artifact, storage_client=storage_client)
            location = uri
        else:
            directory = dashboard_snapshot_local_dir()
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / _SNAPSHOT_FILENAMES[kind]
            path.write_bytes(artifact.gzip_bytes)
            location = str(path)
        results[kind] = {
            "location": location,
            "raw_bytes": len(artifact.raw),
            "gzip_bytes": len(artifact.gzip_bytes),
            "etag": artifact.etag,
        }
        if kind == HISTORY_KIND:
            results[kind]["rebuild_mode"] = "full" if full_history_rebuild else "incremental"

    clear_snapshot_cache()
    return results


def _full_history_rebuild_requested() -> bool:
    raw = os.getenv("DASHBOARD_SNAPSHOT_FULL_HISTORY_REBUILD", "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _cache_ttl_seconds() -> float:
    raw = os.getenv("DASHBOARD_SNAPSHOT_CACHE_SECONDS", "30").strip() or "30"
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 30.0


def load_precomputed_snapshot(
    kind: str,
    *,
    storage_client: Any | None = None,
    use_cache: bool = True,
) -> SnapshotArtifact | None:
    if kind not in _SNAPSHOT_FILENAMES:
        raise ValueError(f"unsupported dashboard snapshot kind: {kind}")

    now = time.monotonic()
    cached = _SNAPSHOT_CACHE.get(kind)
    if use_cache and cached is not None and now - cached[0] < _cache_ttl_seconds():
        return cached[1]

    prefix = dashboard_snapshot_prefix()
    payload: bytes | None = None
    if prefix:
        if storage_client is None:
            from google.cloud.storage import Client

            storage_client = Client()
        bucket_name, blob_name = _parse_gs_uri(snapshot_gcs_uri(kind))
        blob = storage_client.bucket(bucket_name).blob(blob_name)
        try:
            payload = blob.download_as_bytes()
        except Exception as exc:
            if exc.__class__.__name__ == "NotFound":
                return None
            raise
    else:
        path = dashboard_snapshot_local_dir() / _SNAPSHOT_FILENAMES[kind]
        if not path.exists():
            return None
        payload = path.read_bytes()

    artifact = _artifact_from_gzip(kind, payload)
    if use_cache:
        _SNAPSHOT_CACHE[kind] = (now, artifact)
    return artifact


def clear_snapshot_cache() -> None:
    _SNAPSHOT_CACHE.clear()
