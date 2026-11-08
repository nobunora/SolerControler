"""Bounded read-only PV history; never rewrites a saved forecast or display."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import sqlite3
import time
from threading import Lock
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from app.energy_plan.hourly_pv_bias import LOOKBACK_DAYS, _stamp, collect_pv_bias_days
from app.energy_plan.monitoring_history import _monitoring_row, merge_monitoring_rows
from app.energy_plan.pv_bias_training import PvTrainingDay, collect_training_days
from app.forecasting.pv_ideal_max import IdealMaxGeometry
from app.operations.forecast_snapshot import build_forecast_snapshot_rows

MAX_ORIGINS = LOOKBACK_DAYS * 8
MAX_ACTUALS = LOOKBACK_DAYS * 48
MAX_ORIGINAL_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024


class _OriginalReadBudget:
    """Reserve before I/O so parallel reads cannot exceed the total budget."""
    def __init__(self) -> None:
        self.remaining = MAX_TOTAL_BYTES
        self.lock = Lock()

    def read(self, doc: dict[str, Any], storage: Any, deadline: float) -> bytes:
        with self.lock:
            if self.remaining < MAX_ORIGINAL_BYTES:
                raise ValueError("total original read budget exhausted")
            self.remaining -= MAX_ORIGINAL_BYTES
        # A failed download keeps its reservation: its transferred size is unknown.
        raw = _read_original(doc, storage, deadline)
        with self.lock:
            self.remaining += MAX_ORIGINAL_BYTES - len(raw)
        return raw


def _earlier_actuals(target: str, existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    start = (date.fromisoformat(target) - timedelta(days=LOOKBACK_DAYS)).isoformat() + "T00:00:00"
    stamps = [r["dt"].isoformat() for r in existing if isinstance(r.get("dt"), datetime)]
    end = min(stamps) if stamps else target + "T00:00:00"
    if end <= start:
        return existing
    if os.getenv("DATA_BACKEND", "").lower() == "firestore":
        from app.operations.firestore import open_firestore
        from google.cloud.firestore_v1.base_query import FieldFilter
        query = open_firestore().collection("monitoring_samples").where(filter=FieldFilter("ts", ">=", start)).where(filter=FieldFilter("ts", "<", end))
        docs = list(query.limit(MAX_ACTUALS + 1).stream(timeout=20, retry=None))
        if len(docs) > MAX_ACTUALS:
            raise ValueError("PV actual history read budget exceeded")
        older = [_monitoring_row(d.to_dict() or {}) for d in docs]
    else:
        path = Path(os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db"))
        if not path.is_file():
            return existing
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            older = [_monitoring_row(dict(r)) for r in db.execute(
                "SELECT * FROM monitoring_samples WHERE ts>=? AND ts<? LIMIT ?", (start, end, MAX_ACTUALS + 1))]
        if len(older) > MAX_ACTUALS:
            raise ValueError("PV actual history read budget exceeded")
    return merge_monitoring_rows(older, existing)


def _read_original(doc: dict[str, Any], storage: Any, deadline: float) -> bytes:
    expected = str(doc.get("detail_sha256") or doc.get("raw_plan_sha256") or "")
    if len(expected) != 64:
        raise ValueError("missing original checksum")
    inline = doc.get("plan_json")
    if isinstance(inline, str) and inline.strip():
        raw = inline.encode()
    else:
        uri = urlparse(str(doc.get("detail_gcs_uri") or doc.get("detail_uri") or ""))
        if uri.scheme != "gs" or not uri.netloc:
            raise ValueError("missing original reference")
        generation = doc.get("detail_generation", doc.get("generation"))
        blob = storage.bucket(uri.netloc).blob(uri.path.lstrip("/"), generation=int(generation) if generation else None)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("PV original read deadline")
        blob.reload(timeout=min(6., remaining), retry=None)
        if int(blob.size or 0) > MAX_ORIGINAL_BYTES:
            raise ValueError("original size budget exceeded")
        packed = blob.download_as_bytes(if_generation_match=int(blob.generation), timeout=min(6., max(.1, deadline - time.monotonic())), retry=None)
        if packed[:2] == b"\x1f\x8b":
            with gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream:
                raw = stream.read(MAX_ORIGINAL_BYTES + 1)
        else:
            raw = packed
    if len(raw) > MAX_ORIGINAL_BYTES or hashlib.sha256(raw).hexdigest() != expected:
        raise ValueError("original size or checksum mismatch")
    return raw


def load_training_origins(*, target_date: str, as_of: datetime) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if os.getenv("DATA_BACKEND", "").lower() != "firestore":
        return [], {"reason": "original_store_unavailable_use_local_manifest"}
    from app.operations.firestore import open_firestore
    from google.cloud import storage
    from google.cloud.firestore_v1.base_query import FieldFilter
    start = (date.fromisoformat(target_date) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    docs = list(open_firestore().collection("night_plan_decisions").where(filter=FieldFilter("date", ">=", start))
                .where(filter=FieldFilter("date", "<", target_date)).limit(MAX_ORIGINS + 1).stream(timeout=20, retry=None))
    if len(docs) > MAX_ORIGINS:
        return [], {"reason": "original_metadata_read_budget_exceeded"}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    zone = ZoneInfo("Asia/Tokyo")
    for snapshot in docs:
        data = snapshot.to_dict() or {}
        issue = _stamp(data.get("generated_at"))
        acquired = snapshot.create_time
        ds = str(data.get("date") or "")
        if issue is None or acquired is None or acquired > as_of or not start <= ds < target_date:
            continue
        if issue > datetime.combine(date.fromisoformat(ds), datetime.min.time(), zone) + timedelta(hours=7):
            continue
        data["_available_at"] = acquired
        grouped[ds].append(data)
    over_budget = [day for day, items in grouped.items() if len(items) > 8]
    for day in over_budget:
        del grouped[day]
    store, deadline = storage.Client(), time.monotonic() + 90
    budget = _OriginalReadBudget()
    def read_day(item: tuple[str, list[dict[str, Any]]]) -> tuple[dict[str, Any] | None, int]:
        ds, candidates = item
        failures = 0
        for doc in sorted(candidates, key=lambda x: _stamp(x.get("generated_at")) or as_of):
            if time.monotonic() >= deadline:
                break
            try:
                raw = budget.read(doc, store, deadline)
                plan = json.loads(raw)
                if plan.get("forecast", {}).get("date") != ds:
                    raise ValueError("original date mismatch")
                forecast = plan.get("forecast", {})
                if not forecast.get("pv_radiation_rows") and not any(
                    isinstance(r, dict) and r.get("shortwave_radiation_w_m2") is not None
                    for r in forecast.get("hourly_weather", [])):
                    raise ValueError("saved weather unavailable")
                frozen = build_forecast_snapshot_rows(plan, ingested_at=doc["_available_at"].isoformat(), timezone="Asia/Tokyo")
                issue = _stamp(frozen[0].get("issued_at")) if len(frozen) == 24 else None
                if (issue is None or any(r.get("issued_at_source") != "plan" for r in frozen)
                        or issue > datetime.combine(date.fromisoformat(ds), datetime.min.time(), zone) + timedelta(hours=7)
                        or issue > doc["_available_at"]):
                    raise ValueError("original issuance mismatch")
                return {"date": ds, "issued_at": issue.isoformat(), "available_at": doc["_available_at"],
                        "sha256": hashlib.sha256(raw).hexdigest(), "plan": plan, "input_bytes": len(raw)}, failures
            except Exception:
                failures += 1
        return None, failures
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(read_day, sorted(grouped.items())))
    origins = [origin for origin, _ in results if origin is not None]
    size = sum(o["input_bytes"] for o in origins)
    if size > MAX_TOTAL_BYTES:
        return [], {"reason": "total_original_bytes_exceeded"}
    return origins, {"source": "immutable_night_plan_decisions", "metadata_rows": len(docs),
                     "original_days": len(origins), "rejected_originals": sum(n for _, n in results),
                     "unread_or_unusable_days": len(grouped) - len(origins), "daily_budget_rejected_days": len(over_budget),
                     "input_bytes": size, "read_budget_charged_bytes": MAX_TOTAL_BYTES - budget.remaining,
                     "lookback_days": LOOKBACK_DAYS}


def load_pv_training_history(*, target_date: str, as_of: datetime, geometry: IdealMaxGeometry,
                             existing_actuals: list[dict[str, Any]]) -> tuple[tuple[PvTrainingDay, ...], tuple[Any, ...], dict[str, Any]]:
    if not existing_actuals:
        return (), (), {"reason": "no_actuals", "lookback_days": LOOKBACK_DAYS}
    try:
        actuals = _earlier_actuals(target_date, existing_actuals)
        if not actuals:
            return (), (), {"reason": "no_actuals", "lookback_days": LOOKBACK_DAYS}
        origins, coverage = load_training_origins(target_date=target_date, as_of=as_of)
        training, counts = collect_training_days(origins, actuals, geometry=geometry, target_date=target_date, as_of=as_of)
        snapshots = [row for origin in origins for row in build_forecast_snapshot_rows(origin["plan"],
                     ingested_at=origin["available_at"].isoformat(), timezone=geometry.timezone_name)]
        economic, replay_coverage = collect_pv_bias_days(target_date=target_date, as_of=as_of,
            timezone_name=geometry.timezone_name, actual_rows=actuals, snapshot_rows=snapshots)
        return training, economic, {**coverage, **counts, "economic_replay_days": len(economic), "economic_coverage": replay_coverage}
    except Exception as exc:
        print(json.dumps({"event": "pv_training_history", "severity": "ERROR", "error_type": type(exc).__name__}), flush=True)
        return (), (), {"reason": "history_unavailable", "error_type": type(exc).__name__, "lookback_days": LOOKBACK_DAYS}
