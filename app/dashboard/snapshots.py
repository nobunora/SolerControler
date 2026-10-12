from __future__ import annotations

import gzip
import hashlib
import math
import json
import os
import time
import tempfile
import base64
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from collections import defaultdict
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from google.api_core.exceptions import NotFound, PreconditionFailed

from app.dashboard.data import clear_dashboard_cache, load_dashboard_slice
from app.dashboard.models import DashboardSlice
from app.dashboard.slice_assembler import extract_pv_forecast_diagnostics
from app.separated_runtime.plan import PublishedPlan


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


def _read_history_publication(*, storage_client: Any) -> tuple[dict[str, Any] | None, dict[str, str]]:
    """Read producer history bytes and revision metadata from one generation."""
    bucket, name = _parse_gs_uri(snapshot_gcs_uri(HISTORY_KIND))
    blob = storage_client.bucket(bucket).blob(name)
    try:
        blob.reload(timeout=4, retry=None)
        raw = blob.download_as_bytes(if_generation_match=int(blob.generation), timeout=4, retry=None)
    except NotFound:
        return None, {}
    parsed = json.loads(gzip.decompress(raw))
    if (not isinstance(parsed, dict) or not isinstance(parsed.get("meta"), dict)
            or parsed["meta"].get("snapshot_kind") != HISTORY_KIND
            or parsed["meta"].get("snapshot_schema_version") != SNAPSHOT_SCHEMA_VERSION):
        raise ValueError("dashboard history publication schema mismatch")
    encoded = (blob.metadata or {}).get("source_epoch")
    epoch = json.loads(gzip.decompress(base64.b64decode(encoded))) if encoded else {}
    if not isinstance(epoch, dict):
        raise ValueError("dashboard history publication revisions are invalid")
    return parsed, epoch


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


def _upload_artifact(
    artifact: SnapshotArtifact, *, storage_client: Any,
    publication_identity: dict[str, str] | None = None,
    source_epoch: dict[str, str] | None = None,
    source_client: Any | None = None,
    history_scope: bool = False,
) -> str:
    uri = snapshot_gcs_uri(artifact.kind)
    bucket_name, blob_name = _parse_gs_uri(uri)
    blob = storage_client.bucket(bucket_name).blob(blob_name)
    metadata = {
        "dashboard_snapshot_schema": str(SNAPSHOT_SCHEMA_VERSION),
        "sha256": hashlib.sha256(artifact.raw).hexdigest(),
    }
    if publication_identity is not None:
        if not source_epoch:
            raise ValueError("dashboard source epoch is unproven")
        metadata.update(publication_identity)
        epoch_raw = json.dumps(source_epoch, sort_keys=True, separators=(",", ":")).encode()
        metadata["source_epoch"] = base64.b64encode(gzip.compress(epoch_raw, mtime=0)).decode("ascii")
    if sum(len(key.encode()) + len(value.encode()) for key, value in metadata.items()) > 8192:
        raise ValueError("dashboard publication metadata exceeds 8 KiB")
    deadline = time.monotonic() + 24.0
    for _ in range(3):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("dashboard publication deadline exceeded")
        compare_before = (source_epoch or {}).get("history_compare_before")
        if source_client is not None and _read_dashboard_source_epoch(source_client, history_scope=history_scope, deadline=deadline,
                                                                      history_compare_before=compare_before) != source_epoch:
            raise ValueError("dashboard publication source revisions changed")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("dashboard publication deadline exceeded")
        try:
            blob.reload(timeout=min(4.0, remaining), retry=None)
            expected_generation = int(blob.generation)
            previous = blob.metadata or {}
        except NotFound:
            expected_generation, previous = 0, {}
        if previous.get("control_version_id"):
            if publication_identity is None:
                raise ValueError("dashboard publication lacks adopted generation evidence")
            old_order = (previous["target_date"], datetime.fromisoformat(previous["generated_at"].replace("Z", "+00:00")))
            new_order = (publication_identity["target_date"], datetime.fromisoformat(publication_identity["generated_at"].replace("Z", "+00:00")))
            if old_order > new_order or (old_order == new_order and previous["control_version_id"] != publication_identity["control_version_id"]):
                raise ValueError("dashboard publication generation cannot regress")
            old_epoch = json.loads(gzip.decompress(base64.b64decode(previous["source_epoch"])))
            _check_source_epoch_order(old_epoch, source_epoch or {})
        blob.cache_control = "private, max-age=0, must-revalidate"
        blob.metadata = metadata
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("dashboard publication deadline exceeded")
        try:
            blob.upload_from_string(artifact.gzip_bytes, content_type="application/gzip",
                                    if_generation_match=expected_generation, timeout=min(4.0, remaining), retry=None)
            if source_client is not None and _read_dashboard_source_epoch(
                source_client, history_scope=history_scope, deadline=deadline,
                history_compare_before=compare_before,
            ) != source_epoch:
                # The PUT cannot be undone safely. Never report this generation
                # as verified; the caller must rematerialize from its fixed plan.
                raise ValueError("dashboard source revisions changed after upload")
            if time.monotonic() >= deadline:
                raise RuntimeError("dashboard publication deadline exceeded after upload")
            return uri
        except PreconditionFailed:
            continue
    raise RuntimeError("concurrent dashboard publication did not converge")


def _write_dashboard_snapshots_once(
    db_path: Path,
    *,
    storage_client: Any | None = None,
    update_history: bool = False,
    full_history_rebuild: bool = False,
    published_plan: PublishedPlan | None = None,
    source_epoch: dict[str, str] | None = None,
    source_client: Any | None = None,
) -> dict[str, dict[str, Any]]:
    prefix = dashboard_snapshot_prefix()
    # This is the producer cache, including the repository's own cache. The
    # reader artifact cache in another process retains its existing 30s TTL.
    clear_dashboard_cache()
    if published_plan is None and os.getenv("DATA_BACKEND", "sqlite").lower() == "firestore":
        if source_client is None:
            from app.dashboard.firestore_repository import _open_dashboard_firestore_client
            source_client = _open_dashboard_firestore_client()
        latest = source_client.collection("night_charge_plans").document("latest").get(timeout=4, retry=None).to_dict() or {}
        if latest.get("date"):
            projection = source_client.collection("forecast_plans").document(latest["date"]).get(timeout=4, retry=None).to_dict() or {}
            version = projection.get("control_version_id")
            if version:
                document = source_client.collection("control_plan_versions").document(version).get(timeout=4, retry=None).to_dict() or {}
                published_plan = PublishedPlan.from_document(json.loads(document["payload"]), target_date=latest["date"])
                if published_plan.sha256 != projection.get("raw_plan_sha256") or published_plan.version_id != version:
                    raise ValueError("dashboard adopted projection identity mismatch")
    publication_identity: dict[str, str] | None = None
    if published_plan is not None:
        original = json.loads(published_plan.raw_json)
        publication_identity = {"target_date": published_plan.target_date, "generated_at": original["generated_at"],
                                "control_version_id": published_plan.version_id, "raw_plan_sha256": published_plan.sha256}
    if prefix and storage_client is None:
        from google.cloud.storage import Client

        storage_client = Client()

    full_history_rebuild = bool(
        full_history_rebuild
        or _full_history_rebuild_requested()
    )
    history_requested = bool(update_history or full_history_rebuild)
    previous_history_epoch: dict[str, str] = {}
    if history_requested and published_plan is not None and prefix:
        existing_history_candidate, previous_history_epoch = _read_history_publication(storage_client=storage_client)
    else:
        existing_history_candidate = _read_existing_history_payload(storage_client=storage_client) if history_requested else None
    history_scope = bool(history_requested and (existing_history_candidate is not None or full_history_rebuild))
    history_compare_before = previous_history_epoch.get("history_retained_before")
    if published_plan is not None:
        if os.getenv("DATA_BACKEND", "sqlite").lower() != "firestore":
            raise ValueError("dashboard publication source revisions require Firestore")
        if source_client is None:
            from app.dashboard.firestore_repository import _open_dashboard_firestore_client
            source_client = _open_dashboard_firestore_client()
        observed = _read_dashboard_source_epoch(source_client, history_scope=history_scope,
                                                history_compare_before=history_compare_before)
        if source_epoch is not None and source_epoch != observed:
            raise ValueError("dashboard supplied source revisions do not match storage")
        source_epoch = observed
        if history_scope and existing_history_candidate is not None and not full_history_rebuild:
            previous_retained = {key.removeprefix("retained/"): value for key, value in previous_history_epoch.items() if key.startswith("retained/")}
            observed_retained = {key.removeprefix("compare_retained/"): value for key, value in observed.items() if key.startswith("compare_retained/")}
            # Legacy history has no retained-source evidence. Migrate once
            # through the existing bounded rebuild instead of stamping it.
            full_history_rebuild = not history_compare_before or previous_retained != observed_retained
            if full_history_rebuild:
                print(json.dumps({"event": "dashboard_history_rebuild_required", "stage": "snapshot_producer",
                                  "reason_code": "retained_source_changed" if history_compare_before else "legacy_history_revision_missing"}), flush=True)
            else:
                refresh_end = date.fromisoformat(observed["history_retained_before"]) - timedelta(days=1)
                refresh_start = date.fromisoformat(str(history_compare_before))
                if refresh_end >= refresh_start:
                    gap = load_dashboard_slice(db_path, end_date=refresh_end.isoformat(),
                                               window_days=(refresh_end - refresh_start).days + 1, include_static=False)
                    gap_payload = _payload_from_slice(gap)
                    existing_history_candidate = dict(existing_history_candidate)
                    for name in _DAILY_SERIES:
                        rows = {str(row["date"]): row for row in existing_history_candidate.get(name, [])}
                        _merge_rows(rows, list(gap_payload.get(name) or []))
                        existing_history_candidate[name] = list(rows.values())
    if not history_requested or (
        existing_history_candidate is None and not full_history_rebuild
    ):
        bootstrap_slice = load_dashboard_slice(
            db_path,
            end_date=None,
            window_days=31,
            include_static=True,
        )
        payloads = {
            BOOTSTRAP_KIND: build_bootstrap_payload(bootstrap_slice),
        }
    else:
        existing_history = None if full_history_rebuild else existing_history_candidate
        payloads = build_dashboard_snapshot_payloads(db_path, existing_history=existing_history)
        history_meta = payloads[HISTORY_KIND].get("meta")
        if isinstance(history_meta, dict):
            if full_history_rebuild:
                history_meta["snapshot_full_history_rebuild_date"] = _today_jst_iso()
            elif isinstance(existing_history_candidate, dict):
                previous_meta = existing_history_candidate.get("meta")
                if isinstance(previous_meta, dict):
                    history_meta["snapshot_full_history_rebuild_date"] = previous_meta.get(
                        "snapshot_full_history_rebuild_date"
                    )
    results: dict[str, dict[str, Any]] = {}
    if published_plan is not None and _read_dashboard_source_epoch(source_client, history_scope=history_scope,
                                                                  history_compare_before=history_compare_before) != source_epoch:
        raise ValueError("dashboard source revisions changed during materialization")

    for kind, payload in payloads.items():
        if published_plan is not None and kind == BOOTSTRAP_KIND:
            _validate_publication_payload(payload, published_plan)
            forecast_run_id = next(row["forecast_run_id"] for row in payload["forecast_hourly"] if row["date"] == published_plan.target_date)
            diagnostics = extract_pv_forecast_diagnostics(original, producer_identity=publication_identity, forecast_run_id=forecast_run_id)
            payload["pv_forecast_diagnostics"] = diagnostics
            summary = diagnostics["pv_model"]
            if summary["status"] in {"invalid", "unsupported"}:
                print(json.dumps({"event": "dashboard_pv_model_summary_unavailable", "stage": "bootstrap_summary",
                                  "target_date": summary["plan_date"], "model_id": summary["model_id"], "reason_code": summary["reason_code"]}), flush=True)
        artifact = artifact_from_payload(kind, payload)
        if prefix:
            uri = _upload_artifact(artifact, storage_client=storage_client,
                                   publication_identity=publication_identity, source_epoch=source_epoch,
                                   source_client=source_client, history_scope=history_scope)
            location = uri
        else:
            directory = dashboard_snapshot_local_dir()
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / _SNAPSHOT_FILENAMES[kind]
            with tempfile.NamedTemporaryFile(dir=directory, prefix=f"{kind}-", suffix=".part", delete=False) as temporary:
                temporary.write(artifact.gzip_bytes)
                temporary_path = Path(temporary.name)
            temporary_path.replace(path)
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


def write_dashboard_snapshots(
    db_path: Path, *, storage_client: Any | None = None,
    update_history: bool = False, full_history_rebuild: bool = False,
    published_plan: PublishedPlan | None = None, source_epoch: dict[str, str] | None = None,
    source_client: Any | None = None,
) -> dict[str, dict[str, Any]]:
    """Publish, with one rematerialization on detected post-upload source drift."""
    for attempt in range(2):
        try:
            return _write_dashboard_snapshots_once(
                db_path, storage_client=storage_client, update_history=update_history,
                full_history_rebuild=full_history_rebuild, published_plan=published_plan,
                source_epoch=source_epoch, source_client=source_client,
            )
        except ValueError as exc:
            if str(exc) != "dashboard source revisions changed after upload" or attempt:
                raise
            # A supplied epoch is a binding, not permission to replace it.
            if source_epoch is not None:
                raise
            print(json.dumps({"event": "dashboard_publication_rematerializing", "reason_code": "post_upload_source_drift"}), flush=True)
    raise RuntimeError("dashboard publication did not converge")


def write_dashboard_history_snapshot(
    db_path: Path, *, published_plan: PublishedPlan, storage_client: Any | None = None,
    source_client: Any | None = None, deadline: float | None = None,
) -> dict[str, Any]:
    """Rebuild only history, validating the adopted bootstrap in memory."""
    deadline = deadline if deadline is not None else time.monotonic() + 60.0
    if not math.isfinite(deadline):
        raise ValueError("dashboard history requires a finite deadline")
    if os.getenv("DATA_BACKEND", "sqlite").lower() != "firestore":
        raise ValueError("dashboard publication source revisions require Firestore")
    if source_client is None:
        from app.dashboard.firestore_repository import _open_dashboard_firestore_client
        source_client = _open_dashboard_firestore_client()
    if not dashboard_snapshot_prefix():
        raise ValueError("dashboard history publication requires configured GCS storage")
    if storage_client is None:
        from google.cloud.storage import Client
        storage_client = Client()
    original = json.loads(published_plan.raw_json)
    identity = {"target_date": published_plan.target_date, "generated_at": original["generated_at"],
                "control_version_id": published_plan.version_id, "raw_plan_sha256": published_plan.sha256}
    for attempt in range(2):
        clear_dashboard_cache()
        epoch = _read_dashboard_source_epoch(source_client, history_scope=True, deadline=deadline)
        payloads = build_dashboard_snapshot_payloads(db_path, existing_history=None)
        _validate_publication_payload(payloads[BOOTSTRAP_KIND], published_plan)
        if _read_dashboard_source_epoch(source_client, history_scope=True, deadline=deadline) != epoch:
            raise ValueError("dashboard source revisions changed during materialization")
        payloads[HISTORY_KIND]["meta"]["snapshot_full_history_rebuild_date"] = _today_jst_iso()
        artifact = artifact_from_payload(HISTORY_KIND, payloads[HISTORY_KIND])
        try:
            location = _upload_artifact(artifact, storage_client=storage_client,
                                        publication_identity=identity, source_epoch=epoch,
                                        source_client=source_client, history_scope=True)
        except ValueError as exc:
            if str(exc) == "dashboard source revisions changed after upload" and not attempt:
                continue
            raise
        bucket, name = _parse_gs_uri(location)
        blob = storage_client.bucket(bucket).blob(name)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("dashboard history readback deadline exceeded")
        blob.reload(timeout=min(4.0, remaining), retry=None)
        generation = int(blob.generation)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("dashboard history readback deadline exceeded")
        payload = blob.download_as_bytes(if_generation_match=generation,
                                         timeout=min(4.0, remaining), retry=None)
        if time.monotonic() >= deadline:
            raise RuntimeError("dashboard history readback deadline exceeded")
        metadata = blob.metadata or {}
        if payload != artifact.gzip_bytes or any(metadata.get(key) != value for key, value in identity.items()):
            raise ValueError("dashboard history publication readback mismatch")
        encoded = base64.b64encode(gzip.compress(json.dumps(epoch, sort_keys=True, separators=(",", ":")).encode(), mtime=0)).decode("ascii")
        if metadata.get("source_epoch") != encoded or metadata.get("sha256") != hashlib.sha256(artifact.raw).hexdigest():
            raise ValueError("dashboard history publication epoch readback mismatch")
        clear_snapshot_cache()
        return {"status": "verified", "location": location, "generation": str(generation),
                "etag": artifact.etag, "sha256": hashlib.sha256(artifact.raw).hexdigest(),
                "source_epoch": epoch, "publication_identity": identity, "rebuild_mode": "full",
                "raw_bytes": len(artifact.raw), "gzip_bytes": artifact.content_length}
    raise RuntimeError("dashboard history publication did not converge")


def _check_source_epoch_order(previous: dict[str, str], incoming: dict[str, str]) -> None:
    for key, old in previous.items():
        if key.startswith(("scope/", "retained/", "compare_retained/", "history_")):
            continue
        if key not in incoming:
            raise ValueError("dashboard source revision component missing")
        new = incoming[key]
        old_stamp, old_count, _ = old.split("|", 2)
        new_stamp, new_count, _ = new.split("|", 2)
        scope_advanced = incoming.get(f"scope/{key}", "") > previous.get(f"scope/{key}", "")
        if (new_stamp < old_stamp or (int(new_count) < int(old_count) and not scope_advanced)
                or (new_stamp == old_stamp and new != old and not scope_advanced)):
            raise ValueError("dashboard actuals source epoch cannot regress")


def _read_dashboard_source_epoch(client: Any, *, history_scope: bool, deadline: float | None = None,
                                history_compare_before: str | None = None) -> dict[str, str]:
    """Producer-only bounded revision map of every Firestore payload source.

    Native update_time owns ordering; stored ingestion timestamps are not
    promoted to a new source epoch when an older producer finishes late.
    """
    groups: dict[str, list[tuple[str, str]]] = defaultdict(list)
    retained_before: str | None = None
    retained_sources = {"sunshine_daily", "battery_daily_metrics", "forecast_hourly", "forecast_hourly_snapshots",
                        "forecast_hourly_reconstructed", "forecast_plans", "dashboard_daily_metrics", "night_charge_plans",
                        "monitoring_samples", "cost_daily", "analog_plans"}
    read_count = 0
    deadline = deadline if deadline is not None else time.monotonic() + 60.0
    if not math.isfinite(deadline):
        raise ValueError("dashboard source revision requires a finite deadline")

    def timeout() -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("dashboard source revision deadline exceeded")
        return min(4.0, remaining)

    def record(collection: str, snapshots: list[Any], *, stamp_field: str = "date") -> list[Any]:
        nonlocal read_count
        read_count += len(snapshots)
        if len(snapshots) > 3660:
            raise ValueError("dashboard source revision query exceeds 3660 documents")
        for snapshot in snapshots:
            if not snapshot.exists:
                continue
            updated = snapshot.update_time
            if not isinstance(updated, datetime) or updated.utcoffset() is None:
                raise ValueError("dashboard source native revision is unavailable")
            row = snapshot.to_dict() or {}
            day = str(row.get(stamp_field) or snapshot.id)[:10]
            nanos = getattr(updated, "nanosecond", 0) or updated.microsecond * 1000
            revision = updated.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + f".{nanos:09d}+00:00"
            # Every document participates in the digest, including corrected
            # old dates. Grouping by source scope keeps full SHA256 evidence
            # within GCS metadata; no row or digest is silently truncated.
            groups[collection].append((f"{day}/{snapshot.id}", revision))
            if retained_before and collection in retained_sources:
                if day < retained_before:
                    groups[f"retained/{collection}"].append((f"{day}/{snapshot.id}", revision))
                if history_compare_before and day < history_compare_before:
                    groups[f"compare_retained/{collection}"].append((f"{day}/{snapshot.id}", revision))
        return snapshots

    bounds: list[str] = []
    bound_sources = (("sunshine_daily", "date"), ("cost_daily", "date"), ("battery_daily_metrics", "date"),
                     ("forecast_hourly", "date"), ("monitoring_samples", "ts"), ("forecast_hourly_reconstructed", "date"))
    for collection, field in bound_sources:
        query = client.collection(collection).select([field]).order_by(field)
        samples = list(query.limit(1).stream(timeout=timeout(), retry=None)) + list(query.limit_to_last(1).get(timeout=timeout(), retry=None))
        for snapshot in record(f"bounds_{collection}", samples, stamp_field=field):
            bounds.append(str((snapshot.to_dict() or {})[field])[:10])
    if not bounds:
        raise ValueError("dashboard source revision bounds are unavailable")
    end = date.fromisoformat(max(bounds))
    retained_before = (end - timedelta(days=30)).isoformat() if history_scope else None
    start = date.fromisoformat(min(bounds)) if history_scope else end - timedelta(days=30)
    monitoring_start = start - timedelta(days=14)
    epoch: dict[str, str] = {}
    if retained_before:
        epoch["history_retained_before"] = retained_before
        if history_compare_before:
            date.fromisoformat(history_compare_before)
            epoch["history_compare_before"] = history_compare_before
    source_names = ("sunshine_daily", "battery_daily_metrics", "forecast_hourly", "forecast_hourly_snapshots",
                    "forecast_hourly_reconstructed", "forecast_plans", "dashboard_daily_metrics", "night_charge_plans")
    for name in source_names:
        source_start = monitoring_start if name == "dashboard_daily_metrics" else start
        fields = ["date"]
        if name == "night_charge_plans":
            fields.extend(["decision_rationale.forecast_correction.recent_and_analog_hourly_floor.analog_day",
                           "daytime_soc_optimization.forecast_correction.recent_and_analog_hourly_floor.analog_day"])
        query = client.collection(name).where("date", ">=", source_start.isoformat()).where("date", "<=", end.isoformat())
        samples = record(name, list(query.select(fields).limit(3661).stream(timeout=timeout(), retry=None)))
        epoch[f"scope/{name}"] = source_start.isoformat()
        if name == "night_charge_plans":
            analog_dates = set()
            for snapshot in samples:
                row = snapshot.to_dict() or {}
                for section in ("decision_rationale", "daytime_soc_optimization"):
                    correction = (row.get(section) or {}).get("forecast_correction") or {}
                    analog_day = (correction.get("recent_and_analog_hourly_floor") or {}).get("analog_day")
                    if analog_day:
                        analog_dates.add(date.fromisoformat(str(analog_day)).isoformat())
            if analog_dates:
                refs = [client.collection(name).document(day) for day in sorted(analog_dates)]
                record("analog_plans", list(client.get_all(refs, field_paths=["date"], timeout=timeout(), retry=None)))
    monitoring_max_documents = 48 * 1096
    monitoring_count = 0
    monitoring_query = (client.collection("monitoring_samples").where("ts", ">=", monitoring_start.isoformat())
                        .where("ts", "<", (end + timedelta(days=1)).isoformat())
                        .order_by("ts").order_by("__name__").select(["ts"]))
    monitoring_cursor: tuple[str, str] | None = None
    monitoring_ids: set[str] = set()
    while True:
        page = list(monitoring_query.limit(3661).stream(timeout=timeout(), retry=None))
        timeout()
        # Sentinels are read again by the following query and counted as reads.
        read_count += max(0, len(page) - 3660)
        accepted = page[:3660]
        monitoring_count += len(accepted)
        if monitoring_count > monitoring_max_documents:
            raise ValueError("dashboard monitoring source exceeds 52608 documents")
        for snapshot in accepted:
            cursor = (str((snapshot.to_dict() or {}).get("ts", "")), str(snapshot.id))
            if not cursor[0] or (monitoring_cursor is not None and cursor <= monitoring_cursor) or snapshot.id in monitoring_ids:
                raise ValueError("dashboard monitoring source cursor did not advance")
            monitoring_cursor = cursor
            monitoring_ids.add(snapshot.id)
        record("monitoring_samples", accepted, stamp_field="ts")
        if len(page) <= 3660:
            break
        monitoring_query = monitoring_query.start_after(accepted[-1])
    epoch["scope/monitoring_samples"] = monitoring_start.isoformat()
    # Monthly summaries use all existing cost_daily history. This revalidation
    # is explicitly bounded and runs only in the producer, never in a GET.
    record("cost_daily", list(client.collection("cost_daily").select(["date"]).limit(3661).stream(timeout=timeout(), retry=None)))
    record("model_parameters", list(client.collection("model_parameters").select(["name"]).limit(3661).stream(timeout=timeout(), retry=None)), stamp_field="name")
    record("settings_events", list(client.collection("settings_events").select(["recorded_at"]).order_by("recorded_at")
                                  .limit_to_last(40).get(timeout=timeout(), retry=None)), stamp_field="recorded_at")
    record("latest_plan", list(client.get_all([client.collection("night_charge_plans").document("latest")], field_paths=["date"], timeout=timeout(), retry=None)))
    record("latest_battery", list(client.collection("battery_daily_metrics").select(["date"]).order_by("date")
                                 .limit_to_last(1).get(timeout=timeout(), retry=None)))
    for key, rows in groups.items():
        unique = sorted(set(rows))
        digest = hashlib.sha256(json.dumps(unique, separators=(",", ":")).encode()).hexdigest()
        epoch[key] = f"{max(stamp for _, stamp in unique)}|{len(unique)}|{digest}"
    timeout()
    print(json.dumps({"event": "dashboard_source_revisions_read", "stage": "snapshot_producer",
                      "document_reads": read_count, "components": len(epoch), "history_scope": history_scope}), flush=True)
    return epoch


def _validate_publication_payload(payload: dict[str, Any], plan: PublishedPlan) -> None:
    """Validate values against the adopted original, never predict in a GET."""
    from app.operations.domain import extract_hourly_forecast_from_plan
    from app.operations.forecast_snapshot import build_forecast_snapshot_rows

    original = json.loads(plan.raw_json)
    rows = [row for row in payload.get("forecast_hourly", []) if row.get("date") == plan.target_date]
    expected = extract_hourly_forecast_from_plan(original)
    if len(rows) != 24 or {row.get("hour") for row in rows} != set(range(24)):
        raise ValueError("dashboard forecast generation is incomplete")
    values = {row["hour"]: row for row in rows}
    snapshots = build_forecast_snapshot_rows(original, ingested_at=original["generated_at"], timezone="Asia/Tokyo")
    run_id = snapshots[0]["forecast_run_id"]
    for row in expected:
        actual = values[row["hour"]]
        if actual.get("forecast_run_id") != run_id or any(actual.get(key) != row.get(key) for key in ("forecast_pv_kwh", "forecast_load_kwh")):
            raise ValueError("dashboard forecast generation mismatch")
    schedule = payload.get("latest_schedule") or {}
    if schedule.get("plan_date") != plan.target_date or schedule.get("planned_target_soc_percent") != original["result"]["target_soc_7_percent"]:
        raise ValueError("dashboard schedule generation mismatch")
    target = original["result"]["target_soc_7_percent"]
    capacity = original["result"]["effective_capacity_kwh"]
    for row in rows:
        if row.get("forecast_target_soc_percent") != target or row.get("forecast_capacity_kwh") != capacity:
            raise ValueError("dashboard SOC/capacity generation mismatch")
    from app.energy_plan.soc_cost import simulate_daytime_soc_percent
    expected_soc = simulate_daytime_soc_percent(
        target_soc_percent=target, capacity_kwh=capacity,
        hourly_load_kwh={int(row["hour"]): row["forecast_load_kwh"] for row in expected},
        hourly_pv_kwh={int(row["hour"]): row["forecast_pv_kwh"] for row in expected},
    )
    for hour, soc in expected_soc.items():
        if values.get(hour, {}).get("forecast_soc_percent") != soc:
            raise ValueError("dashboard SOC series generation mismatch")


def _full_history_rebuild_requested() -> bool:
    raw = os.getenv("DASHBOARD_SNAPSHOT_FULL_HISTORY_REBUILD", "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _today_jst_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()


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
            payload = blob.download_as_bytes(timeout=4, retry=None)
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
