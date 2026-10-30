"""Reviewed, single-day repair of derived night SOC from an unchanged adopted original."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.operations import forecast_persistence  # noqa: E402
from app.operations.firestore import open_firestore  # noqa: E402
from app.separated_runtime.plan import PublishedPlan  # noqa: E402


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return str(value.rfc3339()) if hasattr(value, "rfc3339") else value.isoformat()
    raise TypeError("unsupported repair evidence type")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=_json_default).encode()


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _code_identity() -> dict[str, str]:
    return {"commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "script": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "projection": hashlib.sha256(Path(forecast_persistence.__file__).read_bytes()).hexdigest()}


def _save(path: Path, value: dict[str, Any], *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        with path.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, default=_json_default)
    else:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(_canonical(value))
        temporary.replace(path)


def _read(client: Any, paths: list[str], *, transaction: Any = None) -> dict[str, dict[str, Any]]:
    snapshots = client.get_all([client.document(path) for path in paths], transaction=transaction, timeout=4, retry=None)
    result: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        path = snapshot.reference.path
        if path not in paths or path in result or not snapshot.exists or snapshot.update_time is None:
            raise ValueError("repair evidence identity missing")
        result[path] = {"data": snapshot.to_dict(), "revision": _json_default(snapshot.update_time)}
    if set(result) != set(paths):
        raise ValueError("repair evidence incomplete")
    return result


def prepare_repair(client: Any, state_path: Path, target: str, *, as_of: datetime) -> dict[str, Any]:
    day = date.fromisoformat(target)
    today = as_of.astimezone(ZoneInfo("Asia/Tokyo")).date()
    if not today-timedelta(days=30) <= day <= today:
        raise ValueError("repair must remain in the recent materialized window")
    metadata_path = f"forecast_plans/{target}"
    metadata = _read(client, [metadata_path])[metadata_path]["data"]
    version = str(metadata.get("control_version_id") or "")
    if len(version) != 64 or any(char not in "0123456789abcdef" for char in version):
        raise ValueError("repair requires an adopted immutable version")
    version_path, current_path = f"control_plan_versions/{version}", f"control_plan_current/{target}"
    paths = [metadata_path, version_path, current_path] + [f"forecast_hourly/{target}-{hour:02d}" for hour in range(24)]
    before = _read(client, paths)
    if before[metadata_path]["data"] != metadata:
        raise ValueError("repair metadata changed during preparation")
    payload = before[version_path]["data"].get("payload")
    if payload != before[current_path]["data"].get("payload") or not isinstance(payload, str):
        raise ValueError("repair original was superseded or unavailable")
    plan = PublishedPlan.from_document(json.loads(payload), target_date=target)
    if plan.version_id != version or plan.sha256 != metadata.get("raw_plan_sha256"):
        raise ValueError("repair original identity mismatch")
    writes = forecast_persistence._forecast_projection_values(json.loads(plan.raw_json), target_date=target,
        timezone_name="Asia/Tokyo", now=str(metadata["updated_at"]))
    expected = {f"{collection}/{key}": row for collection, key, row in writes}
    projection = expected[metadata_path]
    if metadata.get("forecast_run_id") != projection["forecast_run_id"]:
        raise ValueError("repair selected forecast run mismatch")
    soc = metadata.get("planned_hourly_soc_percent")
    if not isinstance(soc, dict) or any(soc.get(str(hour)) != projection["planned_hourly_soc_percent"].get(str(hour))
                                         for hour in range(7, 24)):
        raise ValueError("repair cannot change daytime SOC")
    patches: dict[str, dict[str, Any]] = {}
    fields: tuple[str, ...]
    for path in paths:
        if path == metadata_path:
            fields = ("planned_hourly_soc_percent", "night_soc_projection")
        elif path.startswith("forecast_hourly/"):
            actual, wanted = before[path]["data"], expected[path]
            if actual.get("forecast_run_id") != projection["forecast_run_id"]:
                raise ValueError("repair hourly vintage mismatch")
            for field in ("forecast_pv_kwh", "forecast_load_kwh", "forecast_charge_kwh"):
                if actual.get(field) != wanted[field]:
                    raise ValueError("repair cannot change energy values")
            fields = ("forecast_soc_percent", "forecast_grid_charge_kwh") if actual["hour"] < 7 else ()
        else:
            continue
        patch = {field: expected[path].get(field) for field in fields
                 if before[path]["data"].get(field) != expected[path].get(field)}
        if patch:
            patches[path] = patch
    if not patches or len(patches) > 8:
        raise ValueError("repair requires one to eight night projection documents")
    state = {"schema_version": 1, "target_date": target, "code": _code_identity(),
             "database_fingerprint": _sha(client._database_string), "before": before, "patches": patches,
             "raw_plan_sha256": plan.sha256, "control_version_id": version,
             "prepared_at": as_of.isoformat(), "status": "prepared"}
    _save(state_path, state, exclusive=True)
    return {"status": "PREPARED", "target_date": target, "patch_documents": len(patches),
            "state_sha256": hashlib.sha256(state_path.read_bytes()).hexdigest()}


def _load(client: Any, state_path: Path, reviewed_sha: str) -> dict[str, Any]:
    if hashlib.sha256(state_path.read_bytes()).hexdigest() != reviewed_sha:
        raise ValueError("reviewed repair SHA mismatch")
    state: dict[str, Any] = json.loads(state_path.read_bytes())
    if state["code"] != _code_identity() or state["database_fingerprint"] != _sha(client._database_string):
        raise ValueError("repair code or database identity changed")
    target = date.fromisoformat(state["target_date"]).isoformat()
    if not 1 <= len(state["patches"]) <= 8:
        raise ValueError("repair patch count outside scope")
    for path, patch in state["patches"].items():
        if path == f"forecast_plans/{target}":
            allowed = {"planned_hourly_soc_percent", "night_soc_projection"}
        elif path in {f"forecast_hourly/{target}-{hour:02d}" for hour in range(7)}:
            allowed = {"forecast_soc_percent", "forecast_grid_charge_kwh"}
        else:
            raise ValueError("repair patch document outside scope")
        if not patch or not set(patch) <= allowed:
            raise ValueError("repair patch field outside scope")
    return state


def verify_repair(client: Any, state_path: Path, reviewed_sha: str) -> dict[str, Any]:
    state = _load(client, state_path, reviewed_sha)
    current = _read(client, list(state["before"]))
    for path, snapshot in state["before"].items():
        expected = {**snapshot["data"], **state["patches"].get(path, {})}
        if _sha(current[path]["data"]) != _sha(expected):
            raise ValueError("repair native payload readback mismatch")
        if path not in state["patches"] and current[path]["revision"] != snapshot["revision"]:
            raise ValueError("repair immutable or non-target revision changed")
    result = {"status": "VERIFIED", "target_date": state["target_date"],
              "patch_documents": len(state["patches"]), "documents_verified": len(current),
              "raw_plan_sha256": state["raw_plan_sha256"], "original_and_non_target_documents_preserved": True,
              "verified_at": datetime.now(timezone.utc).isoformat()}
    _save(state_path.with_suffix(".verification.json"), result)
    return result


def apply_repair(client: Any, state_path: Path, reviewed_sha: str) -> dict[str, Any]:
    state = _load(client, state_path, reviewed_sha)
    receipt_path = state_path.with_suffix(".receipt.json")
    _save(receipt_path, {"status": "running", "reviewed_state_sha256": reviewed_sha}, exclusive=True)
    staged = False

    def write(transaction: Any) -> None:
        nonlocal staged
        current = _read(client, list(state["before"]), transaction=transaction)
        if _sha(current) != _sha(state["before"]):
            raise ValueError("repair source changed after review")
        for path, patch in state["patches"].items():
            transaction.set(client.document(path), patch, merge=list(patch))
        staged = True

    try:
        forecast_persistence._run_bounded_projection_transaction(client, write, deadline=time.monotonic()+60, max_attempts=1)
        result = verify_repair(client, state_path, reviewed_sha)
        _save(receipt_path, {**result, "status": "success", "reviewed_state_sha256": reviewed_sha})
        return result
    except Exception as error:
        _save(receipt_path, {"status": "unknown_commit" if staged else "rejected_before_write",
                            "exception_type": type(error).__name__, "reviewed_state_sha256": reviewed_sha})
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", required=True, choices=("prepare", "apply", "verify"))
    parser.add_argument("--state-path", required=True, type=Path)
    parser.add_argument("--target-date")
    parser.add_argument("--reviewed-state-sha256", default="")
    args = parser.parse_args()
    if not args.state_path.resolve().is_relative_to((Path.cwd()/"artifacts").resolve()):
        raise ValueError("repair state must remain under artifacts")
    client = open_firestore()
    if args.stage == "prepare":
        if not args.target_date:
            raise ValueError("prepare requires target date")
        result = prepare_repair(client, args.state_path, args.target_date, as_of=datetime.now(timezone.utc))
    elif args.stage == "apply":
        result = apply_repair(client, args.state_path, args.reviewed_state_sha256)
    else:
        result = verify_repair(client, args.state_path, args.reviewed_state_sha256)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print("Forecast night projection repair did not verify; inspect its private journal.")
        raise SystemExit(1) from None
