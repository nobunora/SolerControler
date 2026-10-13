"""One-time, reviewed monitoring repair. No normal ingestion or control entry."""
from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from google.cloud.firestore_v1 import _helpers
from google.cloud.firestore_v1.types import Document

from app.operations.monitoring_sync import MONITORING_FIELDS, prepare_monitoring_csvs
from app.operations.sqlite import PipelineConfig


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, value: Any, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".repair-", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(_canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        if exclusive:
            # Same-directory hard link exposes the fsynced file atomically and
            # cannot replace another prepared manifest. No runtime lease.
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _directory(state_path: Path) -> Path:
    return Path(f"{state_path}.d")


def _rpc(deadline: float) -> dict[str, Any]:
    if not math.isfinite(deadline):
        raise ValueError("monitoring repair requires a finite deadline")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("monitoring repair deadline exceeded")
    return {"timeout": min(4.0, remaining), "retry": None}


def _fingerprint(client: Any) -> str:
    # Identifiers stay inside the hash and private native preimages.
    return _sha({"project": client.project, "database": client._database_string})


def _native(snapshot: Any) -> dict[str, Any]:
    if not snapshot.exists:
        return {"exists": False}
    if not isinstance(snapshot.update_time, datetime) or snapshot.update_time.utcoffset() is None:
        raise ValueError("native source revision unavailable")
    document = Document(name=snapshot.reference._document_path,
                        fields=_helpers.encode_dict(snapshot.to_dict() or {}),
                        create_time=snapshot.create_time, update_time=snapshot.update_time)
    raw = Document.pb(document).SerializeToString(deterministic=True)
    return {"exists": True, "document_proto": base64.b64encode(raw).decode("ascii"),
            "sha256": hashlib.sha256(raw).hexdigest()}


def _read_refs(client: Any, refs: list[Any], deadline: float) -> dict[str, Any]:
    snapshots = list(client.get_all(refs, **_rpc(deadline)))
    _rpc(deadline)
    result = {snapshot.reference.path: snapshot for snapshot in snapshots}
    if len(result) != len(refs) or set(result) != {ref.path for ref in refs}:
        raise ValueError("incomplete native document readback")
    return result


def _load(state_path: Path, client: Any | None = None) -> dict[str, Any]:
    state: dict[str, Any] = json.loads(state_path.read_bytes())
    immutable = {key: value for key, value in state.items() if key != "reviewed_state_sha256"}
    if state.get("schema_version") != 1 or _sha(immutable) != state.get("reviewed_state_sha256"):
        raise ValueError("immutable repair manifest changed")
    if client is not None and state["database_fingerprint"] != _fingerprint(client):
        raise ValueError("repair database fingerprint mismatch")
    for entry in state["csv_files"]:
        if hashlib.sha256(Path(entry["path"]).read_bytes()).hexdigest() != entry["sha256"]:
            raise ValueError("prepared CSV evidence changed")
    preimage = _directory(state_path) / "preimage.json"
    if hashlib.sha256(preimage.read_bytes()).hexdigest() != state["preimage_sha256"]:
        raise ValueError("native backup preimage changed")
    calibration_preimage = _directory(state_path) / "calibration-preimage.json"
    if hashlib.sha256(calibration_preimage.read_bytes()).hexdigest() != state["calibration_preimage_sha256"]:
        raise ValueError("calibration current backup preimage changed")
    return state


def _journal(state_path: Path) -> dict[str, Any]:
    path = _directory(state_path) / "journal.json"
    result: dict[str, Any] = json.loads(path.read_bytes()) if path.exists() else {"schema_version": 1, "stages": {}}
    return result


def _save_stage(state_path: Path, stage: str, receipt: dict[str, Any]) -> dict[str, Any]:
    journal = _journal(state_path)
    stored = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    if stored.get("status") == "failed":
        exception_type, _, exception_traceback = sys.exc_info()
        stored["failure_frames"] = [
            {"type": exception_type.__name__, "file": Path(frame.filename.replace("\\", "/")).name,
             "function": frame.name, "line": frame.lineno}
            for frame in traceback.extract_tb(exception_traceback, limit=-8)
        ] if exception_type is not None and exception_traceback is not None else []
    if stored.get("status") not in {"running", "pending"}:
        stored.setdefault("finished_at", _now())
    stored["receipt_sha256"] = _sha(stored)
    journal["stages"][stage] = stored
    _atomic_json(_directory(state_path) / "journal.json", journal)
    return stored


def _policy(cfg: PipelineConfig) -> dict[str, Any]:
    return {"day_rate_yen_per_kwh": cfg.day_rate_yen_per_kwh, "tariff_mode": cfg.cost_tariff_mode,
            **{name: getattr(cfg, name) for name in (
                "night8_day_start_hhmm", "night8_day_end_hhmm", "night8_day_tier1_upper_kwh", "night8_day_tier2_upper_kwh",
                "night8_day_rate_tier1_yen", "night8_day_rate_tier2_yen", "night8_day_rate_tier3_yen", "night8_night_rate_yen")}}


def _calibration_current_preimage(cfg: PipelineConfig, deadline: float) -> tuple[dict[str, Any], dict[str, Any]]:
    from app.backup.night_plan_archive import night_plan_archive_prefix
    from app.energy_plan.settings import ForecastSettings
    from app.forecasting.clear_ghi_observations import canonical_bytes, site_fingerprint
    from app.forecasting.clear_ghi_store import ClearGhiStore
    from app.forecasting.pv_array import load_pv_array_configs
    settings = ForecastSettings.from_env()
    arrays = load_pv_array_configs()
    if settings.timezone != cfg.timezone:
        raise ValueError("pipeline and normal planner timezone disagree")
    fingerprint = site_fingerprint(latitude=settings.latitude, longitude=settings.longitude,
        timezone_name=settings.timezone, arrays=[asdict(array) for array in arrays])
    prefix = night_plan_archive_prefix()
    storage: Any = None
    if prefix:
        from google.cloud.storage import Client
        storage = Client()
    root = {"site_fingerprint": fingerprint, "cloud_prefix": prefix or None,
            "local_directory": str(cfg.artifacts_dir), "backend": "gcs" if prefix else "local"}
    store = ClearGhiStore(cfg.artifacts_dir, fingerprint=fingerprint, storage=storage, cloud_prefix=prefix or None, deadline=deadline)
    current, generation = store.read_current()
    if current is None:
        return root, {"status": "verified_absent", "generation": str(generation), "root": root}
    raw_current = canonical_bytes(current)
    names = [f"states/{hashlib.sha256(raw_current).hexdigest()}.json", f"calibrations/{current['calibration_sha256']}.json"]
    for record in current["clear_days"]:
        names.extend(f"originals/{source}/{record['date']}/{record[f'{source}_sha256']}.json" for source in ("actual", "certification"))
    originals: dict[str, str] = {}
    for name in names:
        raw, _ = store._read_original(name)
        if hashlib.sha256(raw).hexdigest() != name.rsplit("/", 1)[1].removesuffix(".json"):
            raise ValueError("calibration current recovery original checksum mismatch")
        originals[name] = base64.b64encode(raw).decode("ascii")
    return root, {"status": "verified", "generation": str(generation), "root": root, "current": current,
                  "state_sha256": hashlib.sha256(raw_current).hexdigest(), "current_bytes": base64.b64encode(raw_current).decode("ascii"),
                  "originals": originals}


def prepare_source_repair(
    *, client: Any, cfg: PipelineConfig, cost_end_ts: str, csv_run_dirs: tuple[Path, ...],
    target_dates: tuple[date, ...], state_path: Path,
) -> dict[str, Any]:
    if state_path.exists():
        raise ValueError("prepare cannot replace an existing repair manifest")
    if target_dates != (date(2026, 9, 20),) or cfg.data_backend != "firestore":
        raise ValueError("repair scope is the approved September 20 Firestore repair")
    deadline = time.monotonic() + 60.0
    files: list[Path] = []
    evidence: list[dict[str, str]] = []
    for run_dir in csv_run_dirs:
        summary_path = run_dir / "kpnet_summary.json"
        summary = json.loads(summary_path.read_bytes())
        evidence.append({"path": str(summary_path.resolve()), "sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest()})
        entries = summary.get("csv_downloads")
        if not isinstance(entries, list) or not entries:
            raise ValueError("CSV download manifest is missing")
        for entry in entries:
            path = Path(entry["path"])
            if not path.is_file():
                raise ValueError("CSV manifest file is unavailable")
            files.append(path)
    files = sorted(set(files))
    evidence.extend({"path": str(path.resolve()), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in files)
    prepared = prepare_monitoring_csvs(files, window=None)
    target = target_dates[0].isoformat()
    rows = [row for row in prepared.rows if str(row["ts"])[:10] == target]
    expected = [(datetime.combine(target_dates[0], datetime.min.time()) + timedelta(minutes=30 * index)).isoformat() for index in range(48)]
    if sorted(str(row["ts"]) for row in rows) != expected or any(ts[:10] == target for ts in prepared.conflict_timestamps):
        raise ValueError("target CSV must supply 48 unique half-hour slots")
    for row in rows:
        for field in MONITORING_FIELDS[1:]:
            value = row.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("CSV contains missing or invalid source energy/SOC")
            if field == "soc_percent" and value > 100:
                raise ValueError("CSV source SOC is outside its valid range")
    refs = [client.collection("monitoring_samples").document(ts) for ts in expected]
    snapshots = _read_refs(client, refs, deadline)
    existing: dict[str, Any] = {}
    candidates: list[dict[str, Any]] = []
    prepared_at = _now()
    for row in sorted(rows, key=lambda value: value["ts"]):
        ts = str(row["ts"])
        snapshot = snapshots[f"monitoring_samples/{ts}"]
        if snapshot.exists:
            saved = snapshot.to_dict() or {}
            if any(saved.get(field) != row.get(field) for field in MONITORING_FIELDS):
                raise ValueError("existing source differs from approved CSV")
            existing[ts] = _native(snapshot)
        else:
            payload = {field: row[field] for field in MONITORING_FIELDS}
            payload.update(source_csv=row["_source_csv"], ingested_at=prepared_at)
            candidates.append({"document_id": ts, "payload": payload})
    if (len(existing), len(candidates)) not in {(35, 13), (48, 0)}:
        raise ValueError("repair requires 35 existing/13 absent or 48 verified existing source documents")
    if not candidates and any(isinstance((snapshots[f"monitoring_samples/{ts}"].to_dict() or {}).get(field), bool)
                              for ts in expected for field in MONITORING_FIELDS[1:]):
        raise ValueError("verified existing source contains invalid boolean energy/SOC")
    cost_start = target_dates[0].replace(day=1).isoformat()
    if datetime.fromisoformat(cost_end_ts) <= datetime.fromisoformat(f"{target}T00:00:00"):
        raise ValueError("cost end must include the repaired day")
    affected = [target, (target_dates[0] + timedelta(days=1)).isoformat()]
    derived_refs = [client.collection(name).document(day) for name in ("dashboard_daily_metrics", "battery_daily_metrics") for day in affected]
    derived = _read_refs(client, derived_refs, deadline)
    cost_query = client.collection("cost_daily").where("date", ">=", cost_start).where("date", "<", cost_end_ts[:10]).limit(3661)
    cost_docs = list(cost_query.stream(**_rpc(deadline)))
    if len(cost_docs) > 3660:
        raise ValueError("cost preimage exceeds budget")
    from google.cloud import firestore
    prior = list(client.collection("cost_daily").where("date", "<", cost_start).order_by("date", direction=firestore.Query.DESCENDING).limit(1).stream(**_rpc(deadline)))
    daily_start = (target_dates[0] - timedelta(days=1)).isoformat() + "T23:00:00"
    daily_end = (target_dates[0] + timedelta(days=2)).isoformat() + "T00:00:00"
    sources: dict[str, Any] = {}
    for stage, source_start, source_end in (("daily", daily_start, daily_end), ("cost", cost_start + "T00:00:00", cost_end_ts)):
        native_rows = _source_range(client, source_start, source_end, deadline)
        sources[stage] = {"start": source_start, "end": source_end, "expected_count": len(native_rows) + len(candidates),
                          "existing": {snapshot.reference.path: _native(snapshot)["sha256"] for snapshot in native_rows}}
    preimage = {**{path: _native(snapshot) for path, snapshot in snapshots.items()},
                **{path: _native(snapshot) for path, snapshot in derived.items()},
                **{snapshot.reference.path: _native(snapshot) for snapshot in cost_docs + prior}}
    calibration_root, calibration_preimage = _calibration_current_preimage(cfg, deadline)
    calibration_path = _directory(state_path) / "calibration-preimage.json"
    if calibration_path.exists():
        if calibration_path.read_bytes() != _canonical(calibration_preimage):
            raise ValueError("interrupted prepare calibration current backup differs")
    else:
        _atomic_json(calibration_path, calibration_preimage, exclusive=True)
    preimage_path = _directory(state_path) / "preimage.json"
    if preimage_path.exists():
        if preimage_path.read_bytes() != _canonical(preimage):
            raise ValueError("existing interrupted prepare backup differs")
    else:
        _atomic_json(preimage_path, preimage, exclusive=True)
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    state: dict[str, Any] = {
        "schema_version": 1, "repair_id": _sha({"candidate": candidates, "prepared_at": prepared_at})[:24],
        "checkpoint_commit": commit, "prepared_at": prepared_at, "target_dates": [target], "affected_dates": affected,
        "candidate_sha256": _sha(candidates), "candidates": candidates, "existing": existing,
        "csv_files": evidence, "csv_manifest_sha256": _sha(evidence), "database_fingerprint": _fingerprint(client),
        "cost_start_date": cost_start, "cost_end_ts": cost_end_ts, "policy": _policy(cfg), "policy_sha256": _sha(_policy(cfg)),
        "artifacts_dir": str(cfg.artifacts_dir),
        "preimage_sha256": hashlib.sha256((_directory(state_path) / "preimage.json").read_bytes()).hexdigest(),
        "cost_baseline": {snapshot.reference.path: _native(snapshot) for snapshot in prior},
        "sources": sources,
        "calibration_root": calibration_root,
        "calibration_preimage_sha256": hashlib.sha256(calibration_path.read_bytes()).hexdigest(),
    }
    state["reviewed_state_sha256"] = _sha(state)
    _atomic_json(state_path, state, exclusive=True)
    _save_stage(state_path, "prepare", {"status": "success", "prepared_at": prepared_at,
                                        "input_sha256": state["reviewed_state_sha256"], "raw_writes": 0})
    return {"status": "prepared", "target_dates": [target], "candidate_count": len(candidates),
            "reviewed_state_sha256": state["reviewed_state_sha256"]}


def _pin_calibration_preimage(client: Any, state_path: Path, state: dict[str, Any]) -> None:
    preimage = json.loads((_directory(state_path) / "calibration-preimage.json").read_bytes())
    if preimage.get("status") not in {"verified", "verified_absent"}:
        raise ValueError("calibration current recovery backup is unproven")
    intent_path = _directory(state_path) / "forecast-intent.json"
    if not intent_path.exists():
        raise ValueError("forecast target intent must precede source mutation")
    intent = json.loads(intent_path.read_bytes())
    if intent.get("prepared_sha256") != state["reviewed_state_sha256"]:
        raise ValueError("calibration recovery intent binding mismatch")
    from app.energy_plan.monthly_projection import previous_billing_period_for_target
    target_date = date.fromisoformat(intent["target_date"])
    normal_start, _, _ = previous_billing_period_for_target(target_date)
    if any(not normal_start <= date.fromisoformat(day) < target_date for day in state["target_dates"]):
        raise ValueError("repair date outside normal calibration input window")
    previous_version = intent.get("previous_control_version_id")
    path = _directory(state_path) / "previous-calibration-plan.json"
    if path.exists():
        return
    if not previous_version:
        _atomic_json(path, {"status": "unavailable", "reason_code": "previous_adopted_plan_missing"})
        return
    snapshot = client.collection("control_plan_versions").document(previous_version).get(**_rpc(time.monotonic() + 20))
    payload = (snapshot.to_dict() or {}).get("payload")
    if not isinstance(payload, str):
        raise ValueError("previous adopted original is unavailable")
    published_document = json.loads(payload)
    from app.separated_runtime.plan import PublishedPlan
    previous = PublishedPlan.from_document(published_document, target_date=published_document["target_date"])
    original = json.loads(previous.raw_json)
    if previous.sha256 != intent["previous_raw_plan_sha256"] or previous.version_id != previous_version:
        raise ValueError("previous adopted original recovery binding mismatch")
    trace = original.get("decision_rationale", {}).get("pv_clear_ghi", {})
    pack = trace.get("calibration_pack")
    if not isinstance(pack, dict):
        _atomic_json(path, {"status": "unavailable", "reason_code": "legacy_plan_has_no_calibration_binding",
                            "previous_raw_plan_sha256": previous.sha256})
        return
    from app.forecasting.clear_ghi_observations import canonical_bytes
    from app.forecasting.clear_ghi_store import ClearGhiStore
    root = state["calibration_root"]
    prefix = root["cloud_prefix"]
    storage: Any = None
    if prefix:
        from google.cloud.storage import Client
        storage = Client()
    store = ClearGhiStore(Path(root["local_directory"]), fingerprint=pack["site_fingerprint"], storage=storage, cloud_prefix=prefix)
    pack_sha = hashlib.sha256(canonical_bytes(pack)).hexdigest()
    name = f"calibrations/{pack_sha}.json"
    if pack["site_fingerprint"] == root["site_fingerprint"] and name in preimage.get("originals", {}):
        raw = base64.b64decode(preimage["originals"][name])
    else:
        raw, _ = store._read_original(name)
    if raw != canonical_bytes(pack) or pack_sha != trace["calibration_sha256"]:
        raise ValueError("previous adopted calibration original checksum mismatch")
    _atomic_json(path, {"status": "verified", "previous_raw_plan_sha256": previous.sha256,
                        "original": original, "calibration_sha256": pack_sha,
                        "calibration_original": base64.b64encode(raw).decode("ascii")})


def verify_source_repair(*, client: Any, state_path: Path) -> dict[str, Any]:
    state = _load(state_path, client)
    deadline = time.monotonic() + 20.0
    ids = sorted([*state["existing"], *(entry["document_id"] for entry in state["candidates"])])
    refs = [client.collection("monitoring_samples").document(ts) for ts in ids]
    snapshots = _read_refs(client, refs, deadline)
    drift = any(_native(snapshots[f"monitoring_samples/{ts}"]) != previous for ts, previous in state["existing"].items())
    verified = absent = conflicting = 0
    for candidate in state["candidates"]:
        snapshot = snapshots[f"monitoring_samples/{candidate['document_id']}"]
        if not snapshot.exists:
            absent += 1
        elif _canonical(snapshot.to_dict()) == _canonical(candidate["payload"]):
            _native(snapshot)
            verified += 1
        else:
            conflicting += 1
    candidate_count = len(state["candidates"])
    status = "committed_drift" if drift else "conflict" if conflicting else "verified" if verified == candidate_count else "not_committed" if absent == candidate_count else "partial"
    return {"schema_version": 1, "repair_id": state["repair_id"], "stage": "source", "target_dates": state["target_dates"],
            "candidate_sha256": state["candidate_sha256"], "source_before_sha256": _sha(state["existing"]),
            "source_after_sha256": _sha({ts: _native(snapshots[f"monitoring_samples/{ts}"]) for ts in ids}),
            "status": status, "attempted_count": 0, "verified_count": verified, "raw_writes": 0,
            "reason_code": status if status != "verified" else None, "readback_at": _now()}


def apply_source_repair(*, client: Any, state_path: Path, reviewed_state_sha256: str) -> dict[str, Any]:
    started = time.monotonic()
    state = _load(state_path, client)
    if reviewed_state_sha256 != state["reviewed_state_sha256"]:
        raise ValueError("reviewed immutable repair SHA mismatch")
    before = verify_source_repair(client=client, state_path=state_path)
    if before["status"] == "verified":
        previous = _journal(state_path)["stages"].get("source", {})
        if not state["candidates"] and not previous.get("calibration_preimage_sha256"):
            _pin_calibration_preimage(client, state_path, state)
        if previous.get("calibration_preimage_sha256") or not state["candidates"]:
            current_sha = hashlib.sha256((_directory(state_path) / "calibration-preimage.json").read_bytes()).hexdigest()
            if current_sha != previous.get("calibration_preimage_sha256", state["calibration_preimage_sha256"]):
                raise ValueError("old calibration recovery preimage changed")
            before["calibration_preimage_sha256"] = current_sha
        return _save_stage(state_path, "source", before)
    if before["status"] != "not_committed":
        return _save_stage(state_path, "source", before)
    _pin_calibration_preimage(client, state_path, state)
    _save_stage(state_path, "source", {**before, "status": "running", "started_at": _now()})
    batch = client.batch()
    for candidate in state["candidates"]:
        batch.create(client.collection("monitoring_samples").document(candidate["document_id"]), candidate["payload"])
    commit_error = None
    try:
        batch.commit(**_rpc(started + 60.0))
    except Exception as error:
        commit_error = type(error).__name__
    try:
        result = verify_source_repair(client=client, state_path=state_path)
    except Exception as error:
        result = {**before, "status": "unknown_commit", "reason_code": type(error).__name__}
    result.update(attempted_count=len(state["candidates"]), raw_writes=None if commit_error else len(state["candidates"]),
                  elapsed_ms=int((time.monotonic() - started) * 1000), commit_response_reason=commit_error,
                  calibration_preimage_sha256=hashlib.sha256((_directory(state_path) / "calibration-preimage.json").read_bytes()).hexdigest())
    return _save_stage(state_path, "source", result)


def _source_range(client: Any, start: str, end: str, deadline: float) -> list[Any]:
    limit = int((datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() // 1800)
    rows = list(client.collection("monitoring_samples").where("ts", ">=", start).where("ts", "<", end)
                .order_by("ts").limit(limit + 1).stream(**_rpc(deadline)))
    _rpc(deadline)
    if len(rows) > limit or len({row.id for row in rows}) != len(rows):
        raise ValueError("derived source range exceeds half-hour budget")
    return rows


def _source_digest(rows: list[Any]) -> str:
    return _sha({snapshot.reference.path: _native(snapshot) for snapshot in rows})


def _cost_expected(source: list[Any], state: dict[str, Any], client: Any, deadline: float) -> dict[str, dict[str, Any]]:
    from app.operations.cost_daily import DailyCostPolicy, EnergyInterval, apply_cumulative_baseline, calculate_daily_costs
    policy = state["policy"]
    kwargs = {key.removeprefix("night8_"): value for key, value in policy.items() if key.startswith("night8_")}
    kwargs.update(tariff_mode=policy["tariff_mode"], day_rate_yen_per_kwh=policy["day_rate_yen_per_kwh"])
    intervals = [EnergyInterval(timestamp=str((row.to_dict() or {}).get("ts", row.id)),
                               load_kwh=(row.to_dict() or {}).get("load_kwh"), buy_kwh=(row.to_dict() or {}).get("buy_kwh")) for row in source]
    results = calculate_daily_costs(intervals, DailyCostPolicy(**kwargs))
    baseline = _read_refs(client, [client.document(path) for path in state["cost_baseline"]], deadline) if state["cost_baseline"] else {}
    prior = next(iter(baseline.values())).to_dict() if baseline else {}
    results = apply_cumulative_baseline(results, base_kwh=float(prior.get("cumulative_kwh") or 0), base_yen=float(prior.get("cumulative_yen") or 0))
    return {"cost_daily/" + result.date: {"date": result.date, "self_consumption_kwh": result.self_consumption_kwh,
            "savings_yen": result.savings_yen, "cumulative_kwh": result.cumulative_kwh, "cumulative_yen": result.cumulative_yen} for result in results}


def _matches(saved: Any, expected: Any) -> bool:
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(saved, (int, float)) and not isinstance(saved, bool) and math.isfinite(saved) and math.isclose(saved, expected, rel_tol=1e-10, abs_tol=1e-8)
    return _canonical(saved) == _canonical(expected)


def _daily_expected(source: list[Any], state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = [snapshot.to_dict() or {} for snapshot in source]
    result: dict[str, dict[str, Any]] = {}

    def energy(values: list[Any]) -> float | None:
        if not values or any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in values):
            return None
        return math.fsum(values)

    for day in state["affected_dates"]:
        daily = [row for row in rows if str(row["ts"])[:10] == day]
        if not daily:
            continue
        fields = {"actual_pv_kwh": "pv_kwh", "actual_load_kwh": "load_kwh", "buy_kwh": "buy_kwh",
                  "sell_kwh": "sell_kwh", "charge_kwh": "charge_kwh", "discharge_kwh": "discharge_kwh"}
        payload: dict[str, Any] = {target: energy([row.get(field) for row in daily]) for target, field in fields.items()}
        payload.update(date=day, sample_count=len(daily), first_sample_at=min(str(row["ts"]) for row in daily),
                       latest_sample_at=max(str(row["ts"]) for row in daily))
        daytime = [row for row in daily if "07:00" <= str(row["ts"])[11:16] < "23:00"]
        nighttime = [row for row in daily if row not in daytime]
        payload.update(day_buy_kwh=energy([row.get("buy_kwh") for row in daytime]),
                       night_buy_kwh=energy([row.get("buy_kwh") for row in nighttime]))
        night_start = (date.fromisoformat(day) - timedelta(days=1)).isoformat() + "T23:00:00"
        night_end = day + "T07:00:00"
        payload["review_night_charge_kwh"] = energy([row.get("charge_kwh") for row in rows if night_start <= str(row["ts"]) < night_end])
        result["dashboard_daily_metrics/" + day] = payload
    for day in state["target_dates"]:
        valid = [row for row in rows if str(row["ts"])[:10] == day and energy([row.get("pv_kwh")]) is not None
                 and float(row["pv_kwh"]) > 0 and energy([row.get("charge_kwh")]) is not None and float(row["charge_kwh"]) > 0
                 and not isinstance(row.get("soc_percent"), bool) and isinstance(row.get("soc_percent"), (int, float)) and math.isfinite(row["soc_percent"])]
        latest = max(valid, key=lambda row: str(row["ts"])) if valid else None
        result["battery_daily_metrics/" + day] = {"pv_charge_end_at": latest["ts"] if latest else None,
                                                 "pv_charge_end_soc_percent": latest["soc_percent"] if latest else None}
    return result


def _read_adopted_plan(client: Any, state_path: Path, state: dict[str, Any]) -> tuple[Any, str]:
    from app.separated_runtime.plan import PublishedPlan
    path = _directory(state_path) / "adopted-forecast.json"
    raw = path.read_bytes()
    receipt = json.loads(raw)
    if receipt.get("prepared_sha256") != state["reviewed_state_sha256"] or receipt.get("status") != "verified":
        raise ValueError("adopted forecast receipt is not verified for this repair")
    snapshot = client.collection("control_plan_versions").document(receipt["control_version_id"]).get(**_rpc(time.monotonic() + 20))
    plan = PublishedPlan.from_document(json.loads(snapshot.to_dict()["payload"]), target_date=receipt["target_date"])
    if plan.sha256 != receipt["raw_plan_sha256"] or plan.version_id != receipt["control_version_id"] or json.loads(plan.raw_json)["generated_at"] != receipt["generated_at"]:
        raise ValueError("adopted immutable forecast identity mismatch")
    return plan, hashlib.sha256(raw).hexdigest()


def regenerate_derived(
    *, client: Any, cfg: PipelineConfig, state_path: Path, stage: Literal["daily", "cost", "history"],
) -> dict[str, Any]:
    from app.operations.firestore import recalc_cost_daily_from, recalc_monitoring_daily_metrics
    state = _load(state_path, client)
    if _policy(cfg) != state["policy"] or cfg.data_backend != "firestore":
        raise ValueError("prepared calculation policy changed")
    if verify_source_repair(client=client, state_path=state_path)["status"] != "verified":
        raise ValueError("source repair is not verified")
    if stage == "history":
        from app.dashboard.snapshots import _parse_gs_uri, _read_dashboard_source_epoch, write_dashboard_history_snapshot
        plan, receipt_sha = _read_adopted_plan(client, state_path, state)
        previous = _journal(state_path)["stages"].get(stage, {})
        if previous.get("status") == "verified" and previous.get("adopted_receipt_sha256") == receipt_sha:
            from google.cloud.storage import Client
            import gzip
            storage = Client()
            deadline = time.monotonic() + 60
            epoch = _read_dashboard_source_epoch(client, history_scope=True, deadline=deadline)
            bucket, name = _parse_gs_uri(previous["location"])
            blob = storage.bucket(bucket).blob(name)
            blob.reload(**_rpc(deadline))
            generation = int(blob.generation)
            metadata = blob.metadata or {}
            encoded = base64.b64encode(gzip.compress(_canonical(epoch), mtime=0)).decode("ascii")
            body = blob.download_as_bytes(if_generation_match=generation, **_rpc(deadline))
            if (str(generation) == previous["generation"] and metadata.get("sha256") == previous["sha256"]
                and hashlib.sha256(gzip.decompress(body)).hexdigest() == previous["sha256"]
                and metadata.get("source_epoch") == encoded and epoch == previous["source_epoch"]
                and all(metadata.get(key) == value for key, value in previous["publication_identity"].items())
                and _read_dashboard_source_epoch(client, history_scope=True, deadline=deadline) == epoch):
                return {**previous, "reason_code": "verified_existing_output"}
        result = write_dashboard_history_snapshot(cfg.db_path, published_plan=plan, source_client=client)
        result.update(adopted_receipt_sha256=receipt_sha, repair_manifest_sha256=state["reviewed_state_sha256"])
        return _save_stage(state_path, stage, result)
    if stage not in {"daily", "cost"}:
        raise ValueError("unknown derived repair stage")
    intent_path = _directory(state_path) / "forecast-intent.json"
    if not intent_path.exists():
        raise ValueError("normal planner target intent is required before derived repair")
    intent = json.loads(intent_path.read_bytes())
    if intent.get("prepared_sha256") != state["reviewed_state_sha256"]:
        raise ValueError("forecast intent is not bound to prepared repair")
    from app.energy_plan.monthly_projection import previous_billing_period_for_target
    planned_target = date.fromisoformat(intent["target_date"])
    normal_start, _, _ = previous_billing_period_for_target(planned_target)
    if any(not normal_start <= date.fromisoformat(day) < planned_target for day in state["target_dates"]):
        raise ValueError("repair date outside normal calibration input window")
    start = state["cost_start_date"] + "T00:00:00" if stage == "cost" else (date.fromisoformat(min(state["affected_dates"])) - timedelta(days=1)).isoformat() + "T23:00:00"
    end = state["cost_end_ts"] if stage == "cost" else (date.fromisoformat(max(state["affected_dates"])) + timedelta(days=1)).isoformat() + "T00:00:00"
    total_started = time.monotonic()
    for attempt in range(2):
        deadline = time.monotonic() + (60 if stage == "cost" else 30)
        source = _source_range(client, start, end, deadline)
        pinned = state["sources"][stage]
        candidate_paths = {"monitoring_samples/" + candidate["document_id"] for candidate in state["candidates"]}
        if (len(source) != pinned["expected_count"] or
            {snapshot.reference.path for snapshot in source} != set(pinned["existing"]) | candidate_paths or
            any(_native(snapshot)["sha256"] != pinned["existing"][snapshot.reference.path]
                for snapshot in source if snapshot.reference.path not in candidate_paths)):
            raise ValueError("prepared derived source scope/count/revisions changed")
        before_sha = _source_digest(source)
        expected_output = _cost_expected(source, state, client, deadline) if stage == "cost" else _daily_expected(source, state)
        previous = _journal(state_path)["stages"].get(stage, {})
        if not attempt and previous.get("status") == "verified" and previous.get("source_after_sha256") == before_sha:
            saved = _read_refs(client, [client.document(path) for path in expected_output], deadline) if expected_output else {}
            if (all(snapshot.exists and all(_matches((snapshot.to_dict() or {}).get(key), value) for key, value in expected_output[path].items())
                    for path, snapshot in saved.items()) and _source_digest(_source_range(client, start, end, deadline)) == before_sha):
                return {**previous, "reason_code": "verified_existing_output"}
        baseline_refs = [client.document(path) for path in state["cost_baseline"]]
        if stage == "cost" and baseline_refs:
            baseline = _read_refs(client, baseline_refs, deadline)
            if {path: _native(snap) for path, snap in baseline.items()} != state["cost_baseline"]:
                raise ValueError("prepared cumulative cost baseline changed")
        progress: list[dict[str, Any]] = []
        receipt: dict[str, Any] = {"status": "running", "stage": stage, "started_at": _now(),
                                  "source_before_sha256": before_sha, "input_sha256": state["reviewed_state_sha256"],
                                  "attempt": attempt, "units": progress}
        _save_stage(state_path, stage, receipt)

        def on_progress(event: dict[str, Any]) -> None:
            if event["collection_counts"]["monitoring_samples_read"] != len(source):
                raise ValueError("source document count changed during recalculation")
            progress.append(event)
            _save_stage(state_path, stage, receipt)

        error: Exception | None = None
        try:
            if stage == "daily":
                counts = recalc_monitoring_daily_metrics(client, calendar_dates=set(state["target_dates"]),
                    dashboard_affected_dates=set(state["affected_dates"]), updated_at=state["prepared_at"], deadline=deadline, on_progress=on_progress)
            else:
                counts = {"cost": recalc_cost_daily_from(client, start_date=state["cost_start_date"], end_ts=state["cost_end_ts"],
                    updated_at=state["prepared_at"], deadline=deadline, on_progress=on_progress, **state["policy"])}
        except Exception as exc:
            error = exc
            counts = {}
        # Unknown ACK or callback failure never means rollback. Recover with
        # bounded, write-free readback of prepared units.
        read_deadline = time.monotonic() + 20 if error is not None else deadline
        prepared_units = [unit for unit in progress if unit["event"] == "unit_prepared"]
        verified = attempted = 0
        output: dict[str, Any] = {}
        try:
            for unit in prepared_units:
                refs = [client.collection(document["collection"]).document(document["id"]) for document in unit["documents"]]
                snapshots = _read_refs(client, refs, read_deadline)
                for document in unit["documents"]:
                    path = document["collection"] + "/" + document["id"]
                    snapshot = snapshots[path]
                    saved = snapshot.to_dict() or {}
                    attempted += 1
                    if snapshot.exists and all(_canonical(saved.get(key)) == _canonical(value) for key, value in document["payload"].items()):
                        verified += 1
                    output[path] = _native(snapshot)
            after_sha = _source_digest(_source_range(client, start, end, read_deadline))
        except Exception as exc:
            receipt.update(status="unknown_commit" if prepared_units else "failed", reason_code=type(exc).__name__)
            return _save_stage(state_path, stage, receipt)
        expected_refs = [client.document(path) for path in expected_output]
        expected_saved = _read_refs(client, expected_refs, read_deadline) if expected_refs else {}
        complete_scope = all(snapshot.exists and all(_matches((snapshot.to_dict() or {}).get(key), value)
                             for key, value in expected_output[path].items()) for path, snapshot in expected_saved.items())
        status = "verified" if verified == attempted and complete_scope and (prepared_units or not source) else "partial"
        if before_sha != after_sha:
            status = "failed"
        if error is not None and not prepared_units:
            status = "failed"
        receipt.update(status=status, source_after_sha256=after_sha, output_sha256=_sha(output),
                       verified_count=verified, attempted_count=attempted, counts=counts,
                       elapsed_ms=int((time.monotonic() - total_started) * 1000),
                       reason_code="source_drift" if before_sha != after_sha else type(error).__name__ if error else None)
        _save_stage(state_path, stage, receipt)
        if before_sha != after_sha and not attempt:
            continue
        return receipt
    raise RuntimeError("derived source did not converge")


def _calibration_readback(client: Any, cfg: PipelineConfig, state_path: Path, state: dict[str, Any], plan: Any) -> dict[str, Any]:
    from app.energy_plan.monthly_projection import previous_billing_period_for_target
    from app.forecasting.clear_ghi_observations import canonical_bytes
    from app.forecasting.clear_ghi_store import ClearGhiStore
    adopted = json.loads((_directory(state_path) / "adopted-forecast.json").read_bytes())
    execution_path = Path(adopted["execution_receipt_path"])
    execution_raw = execution_path.read_bytes()
    if hashlib.sha256(execution_raw).hexdigest() != adopted["execution_receipt_sha256"]:
        raise ValueError("adopted execution evidence changed")
    execution = json.loads(execution_raw)
    events = [entry.get("jsonPayload", {}) for entry in execution["logs"]]
    if any(event.get("event") in {"pv_calibration_retained", "pv_calibration_failed"} for event in events):
        raise ValueError("calibration retained or failed after source repair")
    document = json.loads(plan.raw_json)
    trace = document["decision_rationale"]["pv_clear_ghi"]
    pack = trace["calibration_pack"]
    pack_sha = hashlib.sha256(canonical_bytes(pack)).hexdigest()
    if pack_sha != trace["calibration_sha256"] or pack_sha != adopted["calibration_sha256"]:
        raise ValueError("adopted calibration pack hash mismatch")
    target_date = date.fromisoformat(plan.target_date)
    start, _, _ = previous_billing_period_for_target(target_date)
    root = state["calibration_root"]
    if root["site_fingerprint"] != pack["site_fingerprint"]:
        raise ValueError("adopted calibration site differs from prepared normal planner inputs")
    prefix = root["cloud_prefix"]
    storage_client: Any = None
    if prefix:
        from google.cloud.storage import Client
        storage_client = Client()
    store = ClearGhiStore(Path(root["local_directory"]), fingerprint=pack["site_fingerprint"], storage=storage_client, cloud_prefix=prefix)
    updates = [event for event in events if event.get("event") == "pv_calibration_updated" and event.get("calibration_sha256") == pack_sha]
    if updates:
        state_sha = updates[-1]["state_sha256"]
        state_raw, generation = store._read_original(f"states/{state_sha}.json")
        if hashlib.sha256(state_raw).hexdigest() != state_sha:
            raise ValueError("selected immutable calibration state checksum mismatch")
        current = store._validate_state(json.loads(state_raw))
    else:
        preimage_raw = (_directory(state_path) / "calibration-preimage.json").read_bytes()
        source_receipt = _journal(state_path)["stages"].get("source", {})
        if hashlib.sha256(preimage_raw).hexdigest() != source_receipt.get("calibration_preimage_sha256"):
            raise ValueError("old calibration recovery preimage checksum mismatch")
        preimage = json.loads(preimage_raw)
        current = preimage.get("current")
        generation = preimage.get("generation")
        if current is not None and hashlib.sha256(canonical_bytes(current)).hexdigest() != preimage["state_sha256"]:
            raise ValueError("old calibration recovery state checksum mismatch")
    if current is None or current["calibration_sha256"] != pack_sha:
        raise ValueError("selected immutable calibration state is unavailable")
    records = {record["date"]: record for record in current["clear_days"]}
    observations = [event for event in events if event.get("event") == "pv_calibration_observations"]
    if any(day in event.get("retained_observation_gaps", [])
           for day in state["target_dates"] for event in observations) or any(
           day in event.get("retained_observation_gaps", []) or
           day in event.get("excluded_dates", {}).get("satellite_hour_missing", [])
           for day in pack["training_dates"] for event in observations):
        raise ValueError("calibration provider gap after source repair")
    verified_dates: dict[str, str] = {}
    for day_string in state["target_dates"]:
        day = date.fromisoformat(day_string)
        if not start <= day < target_date:
            raise ValueError("repair date outside normal calibration input window")
        timestamps = [f"{day_string}T{hour:02d}:{minute:02d}:00" for hour in range(5, 18) for minute in (0, 30)]
        samples = _read_refs(client, [client.collection("monitoring_samples").document(ts) for ts in timestamps], time.monotonic() + 20)
        values: list[Any] = [(samples[f"monitoring_samples/{ts}"].to_dict() or {}).get("pv_kwh") for ts in timestamps]
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("calibration actual input is incomplete")
        actual_source = {"date": day_string, "samples": [{"hour": hour, "actual_halfhour_kwh": [float(value) for value in values[(hour - 5) * 2:(hour - 4) * 2]]} for hour in range(5, 18)]}
        actual_sha = hashlib.sha256(canonical_bytes(actual_source)).hexdigest()
        if not any(event.get("checked_actual_sha256_by_date", {}).get(day_string) == actual_sha for event in observations):
            raise ValueError("normal planner calibration input hash does not match repaired source")
        if any(day_string in event.get("excluded_dates", {}).get("satellite_hour_missing", []) for event in observations):
            if day_string in pack["training_dates"] or not any(
                    day_string in event.get("excluded_dates", {}).get("satellite_hour_missing", []) and
                    event.get("checked_actual_sha256_by_date", {}).get(day_string) == actual_sha
                    for event in observations):
                raise ValueError("unselected calibration gap lacks matching repaired actual evidence")
            # Approved release acceptance only: this day remains uncertified and
            # is not part of the active pack. Keep its unresolved state visible.
            verified_dates[day_string] = "pending_satellite_hour_missing"
            continue
        record = records.get(day_string)
        if record is None:
            # An absent training day alone is not evidence of non-clear sky.
            proven = any(day_string in event.get("excluded_dates", {}).get("not_certified_clear", []) for event in observations)
            if not proven or day_string in pack["training_dates"]:
                raise ValueError("repaired calibration day has no certified exclusion evidence")
            verified_dates[day_string] = "not_certified_clear"
            continue
        if record["actual_sha256"] != actual_sha or record["actual_source"] != actual_source:
            raise ValueError("adopted calibration actual hash does not match repaired source")
        if hashlib.sha256(canonical_bytes(record["certification_source"])).hexdigest() != record["certification_sha256"]:
            raise ValueError("calibration certification original hash mismatch")
        for source_name in ("actual", "certification"):
            raw_original, _ = store._read_original(f"originals/{source_name}/{day_string}/{record[f'{source_name}_sha256']}.json")
            if raw_original != canonical_bytes(record[f"{source_name}_source"]):
                raise ValueError("selected calibration observation original mismatch")
        as_of = datetime.fromisoformat(pack["training_as_of"])
        if any(datetime.fromisoformat(record[key]) > as_of for key in ("actual_available_at", "certification_available_at")):
            raise ValueError("calibration observation unavailable at training as_of")
        verified_dates[day_string] = actual_sha
    return {"status": "verified", "calibration_sha256": pack_sha, "state_sha256": hashlib.sha256(canonical_bytes(current)).hexdigest(),
            "generation": str(generation), "dates": verified_dates, "input_start": start.isoformat(), "input_end": plan.target_date}


def verify_repair(*, client: Any, cfg: PipelineConfig, state_path: Path) -> dict[str, Any]:
    """Read all fixed receipts and final calibration/asset bindings; write no cloud data."""
    state = _load(state_path, client)
    source = verify_source_repair(client=client, state_path=state_path)
    journal = _journal(state_path)
    if source["status"] != "verified" or any(journal["stages"].get(stage, {}).get("status") != "verified" for stage in ("daily", "cost", "history")):
        return {"status": "partial", "reason_code": "unfinished_repair_stages"}
    plan, adopted_sha = _read_adopted_plan(client, state_path, state)
    if journal["stages"]["history"].get("adopted_receipt_sha256") != adopted_sha:
        raise ValueError("history was built from a different adopted receipt")
    calibration = _calibration_readback(client, cfg, state_path, state, plan)
    deadline = time.monotonic() + 60
    cost_source = _source_range(client, state["cost_start_date"] + "T00:00:00", state["cost_end_ts"], deadline)
    daily_start = (date.fromisoformat(min(state["affected_dates"])) - timedelta(days=1)).isoformat() + "T23:00:00"
    daily_end = (date.fromisoformat(max(state["affected_dates"])) + timedelta(days=1)).isoformat() + "T00:00:00"
    daily_source = _source_range(client, daily_start, daily_end, deadline)
    expected = {**_cost_expected(cost_source, state, client, deadline), **_daily_expected(daily_source, state)}
    snapshots = _read_refs(client, [client.document(path) for path in expected], deadline)
    if any(not snapshot.exists or any(not _matches((snapshot.to_dict() or {}).get(key), value)
            for key, value in expected[path].items()) for path, snapshot in snapshots.items()):
        raise ValueError("final derived metrics do not match repaired source")
    if (_source_digest(cost_source) != _source_digest(_source_range(client, state["cost_start_date"] + "T00:00:00", state["cost_end_ts"], deadline)) or
        _source_digest(daily_source) != _source_digest(_source_range(client, daily_start, daily_end, deadline))):
        raise ValueError("source changed during final derived verification")
    from app.dashboard.snapshots import BOOTSTRAP_KIND, HISTORY_KIND
    identity = {"target_date": plan.target_date, "generated_at": json.loads(plan.raw_json)["generated_at"],
                "control_version_id": plan.version_id, "raw_plan_sha256": plan.sha256}
    from app.dashboard.snapshots import _artifact_from_gzip, _parse_gs_uri, _read_dashboard_source_epoch, _validate_publication_payload, snapshot_gcs_uri
    from google.cloud.storage import Client
    storage = Client()
    for kind in (BOOTSTRAP_KIND, HISTORY_KIND):
        bucket, name = _parse_gs_uri(snapshot_gcs_uri(kind))
        blob = storage.bucket(bucket).blob(name)
        blob.reload(timeout=4, retry=None)
        generation = int(blob.generation)
        metadata = blob.metadata or {}
        artifact = _artifact_from_gzip(kind, blob.download_as_bytes(if_generation_match=generation, timeout=4, retry=None))
        if any(metadata.get(key) != value for key, value in identity.items()) or metadata.get("sha256") != hashlib.sha256(artifact.raw).hexdigest():
            raise ValueError("dashboard snapshot identity does not match adopted forecast")
        import gzip
        epoch = json.loads(gzip.decompress(base64.b64decode(metadata["source_epoch"])))
        actual_epoch = _read_dashboard_source_epoch(client, history_scope=kind == HISTORY_KIND, deadline=deadline,
                                                     history_compare_before=epoch.get("history_compare_before"))
        if epoch != actual_epoch:
            raise ValueError("dashboard snapshot source revisions do not match repaired source")
        if kind == BOOTSTRAP_KIND:
            _validate_publication_payload(json.loads(artifact.raw), plan)
    return _save_stage(state_path, "verify", {"status": "verified", "readback_at": _now(), "source": source,
                                             "adopted_receipt_sha256": adopted_sha, "calibration": calibration})
