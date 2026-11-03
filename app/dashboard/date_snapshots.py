"""Explicit date-only refresh of existing materialized views.

Untouched dates are copied, not regenerated. Baseline source epochs and outside-
date revisions must remain proven; this path never falls back to a full rebuild.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

from google.cloud import storage

from app.dashboard.aggregation import _build_cost_monthly
from app.dashboard.data import clear_dashboard_cache, load_dashboard_slice
from app.dashboard.snapshots import (
    BOOTSTRAP_KIND, HISTORY_KIND, _DAILY_SERIES, _parse_gs_uri,
    _payload_from_slice, _read_dashboard_source_epoch, _upload_artifact,
    _validate_publication_payload, artifact_from_payload, snapshot_gcs_uri,
)
from app.separated_runtime.plan import PublishedPlan


def _outside_epoch(client: Any, target_date: str) -> dict[str, str]:
    epoch = _read_dashboard_source_epoch(client, history_scope=True, excluded_dates=frozenset({target_date}))
    return {key: value for key, value in epoch.items()
            if not key.startswith(("bounds_", "scope/", "history_", "retained/", "compare_retained/"))}


def _assert_current_plan(client: Any, identity: dict[str, str]) -> None:
    saved = client.collection("control_plan_current").document(identity["target_date"]).get(timeout=4, retry=None).to_dict() or {}
    plan = PublishedPlan.from_document(json.loads(saved["payload"]), target_date=identity["target_date"])
    if plan.version_id != identity["control_version_id"] or plan.sha256 != identity["raw_plan_sha256"]:
        raise ValueError("adopted plan changed; no publication from an obsolete import baseline")


def capture_date_refresh(client: Any, target_date: str) -> dict[str, Any]:
    remote = storage.Client()
    payloads = {}
    epochs = {}
    identity = {}
    for kind in (BOOTSTRAP_KIND, HISTORY_KIND):
        bucket, name = _parse_gs_uri(snapshot_gcs_uri(kind))
        blob = remote.bucket(bucket).blob(name)
        blob.reload(timeout=4, retry=None)
        raw = gzip.decompress(blob.download_as_bytes(raw_download=True, if_generation_match=int(blob.generation), timeout=4, retry=None))
        metadata = blob.metadata or {}
        if hashlib.sha256(raw).hexdigest() != metadata.get("sha256"):
            raise ValueError("date refresh baseline checksum mismatch")
        payloads[kind] = json.loads(raw)
        epochs[kind] = json.loads(gzip.decompress(base64.b64decode(metadata["source_epoch"])))
        if kind == BOOTSTRAP_KIND:
            identity = {key: metadata[key] for key in ("target_date", "generated_at", "control_version_id", "raw_plan_sha256")}
    _assert_current_plan(client, identity)
    baseline = epochs[BOOTSTRAP_KIND]
    observed = _read_dashboard_source_epoch(client, history_scope=bool(baseline.get("history_retained_before")),
                                            history_compare_before=baseline.get("history_compare_before"))
    if observed != baseline:
        raise ValueError("existing bootstrap is stale; reconcile dates explicitly before import")
    history = epochs[HISTORY_KIND]
    boundary = history.get("history_retained_before")
    if not boundary:
        raise ValueError("history provenance is missing; explicit history repair required")
    full = _read_dashboard_source_epoch(client, history_scope=True, history_compare_before=boundary)
    old = {key.removeprefix("retained/"): value for key, value in history.items() if key.startswith("retained/")}
    current = {key.removeprefix("compare_retained/"): value for key, value in full.items() if key.startswith("compare_retained/")}
    if old != current:
        raise ValueError("unreflected history change requires explicit dated repair")
    # Recent history may legitimately lag the bootstrap (the client keeps the
    # bootstrap copy). Retained older source revisions above must still match.
    # Never rebuild those unselected recent rows as a side effect of this import.
    return {"storage": remote, "payloads": payloads, "identity": identity,
            "outside_epoch": _outside_epoch(client, target_date)}


def merge_date_payload(previous: dict[str, Any], fresh: dict[str, Any], target_date: str) -> dict[str, Any]:
    result = deepcopy(previous)
    for field in (*_DAILY_SERIES, "forecast_hourly", "daily_reviews"):
        if field == "forecast_hourly" and previous.get("meta", {}).get("snapshot_kind") == HISTORY_KIND:
            # History intentionally does not carry hourly bulk data.
            continue
        old = [row for row in previous.get(field, []) if row.get("date") != target_date]
        updated = [row for row in fresh.get(field, []) if row.get("date") == target_date]
        if updated:
            originals = {(str(row.get("date")), row.get("hour")): row for row in previous.get(field, [])}
            updated = [dict(row) for row in updated]
            for row in updated:
                for key, value in originals.get((str(row.get("date")), row.get("hour")), {}).items():
                    if key.startswith(("forecast_", "retained_forecast_", "planned_")) and row.get(key) is None:
                        row[key] = value
            result[field] = sorted([*old, *updated], key=lambda row: (str(row.get("date")), int(row.get("hour") or 0)))
    meta = result.setdefault("meta", {})
    meta["snapshot_updated_dates"] = [target_date]
    for key in ("global_newest_date", "newest_loaded_date"):
        fresh_bound = fresh.get("meta", {}).get(key)
        if fresh_bound:
            meta[key] = max(str(meta.get(key) or ""), str(fresh_bound))
    reviews = result.get("daily_reviews", [])
    if reviews and target_date >= str(result.get("daily_review", {}).get("date") or ""):
        result["daily_review"] = max(reviews, key=lambda row: str(row.get("date") or ""))
    return result


def publish_date_refresh(client: Any, target_date: str, captured: dict[str, Any]) -> None:
    _assert_current_plan(client, captured["identity"])
    if _outside_epoch(client, target_date) != captured["outside_epoch"]:
        raise ValueError("another source date changed; no automatic multi-date regeneration")
    clear_dashboard_cache()
    fresh = _payload_from_slice(load_dashboard_slice(Path("artifacts/solar_monitor.db"), end_date=target_date,
                                                    window_days=1, include_static=True))
    payloads = {kind: merge_date_payload(previous, fresh, target_date)
                for kind, previous in captured["payloads"].items()}
    # Keep every unselected history row byte-for-byte equivalent to its prior value.
    history = payloads[HISTORY_KIND]
    payloads[BOOTSTRAP_KIND]["cost_monthly"] = _build_cost_monthly(history.get("cost_daily", []))
    identity = captured["identity"]
    saved = client.collection("control_plan_versions").document(identity["control_version_id"]).get(timeout=4, retry=None).to_dict() or {}
    plan = PublishedPlan.from_document(json.loads(saved["payload"]), target_date=identity["target_date"])
    if plan.sha256 != identity["raw_plan_sha256"]:
        raise ValueError("date refresh original mismatch")
    _validate_publication_payload(payloads[BOOTSTRAP_KIND], plan)
    epoch = _read_dashboard_source_epoch(client, history_scope=True)
    if _outside_epoch(client, target_date) != captured["outside_epoch"]:
        raise ValueError("another source date changed during date materialization")
    for kind, payload in payloads.items():
        _assert_current_plan(client, identity)
        _upload_artifact(artifact_from_payload(kind, payload), storage_client=captured["storage"],
                         publication_identity=identity, source_epoch=epoch, source_client=client, history_scope=True)
        _assert_current_plan(client, identity)
