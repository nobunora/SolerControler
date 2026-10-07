"""Offline production-plan replay against a fixed, trusted Git baseline."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timedelta
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.runtime import cloud_job
from app.runtime.soc_reading import SocReading
from app.separated_runtime import control
from app.separated_runtime.plan import PublishedPlan


class ReplayClock:
    def __init__(self, day: str) -> None:
        self.at = datetime.fromisoformat(day + "T03:00:00+09:00")

    def now(self, zone: ZoneInfo) -> datetime:
        return self.at.astimezone(zone)

    def sleep(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)

    def monotonic_seconds(self) -> float:
        return 0.0


class ReplayDevice:
    def __init__(self, values: list[float | None]) -> None:
        self.values = iter(values)
        self.events: list[Any] = []

    def read_soc(self, paths: Any) -> SocReading:
        value = next(self.values)
        self.events.append(("soc", value))
        return SocReading(value, "offline-replay", None, None)

    def apply_profile(self, *, profile: str, dynamic_forced_profile: bool, label: str) -> None:
        self.events.append(("mode", profile, dynamic_forced_profile))


def verify_calculation_source(baseline: str, approved_patch_sha256: str = "") -> dict[str, Any]:
    if approved_patch_sha256 and not re.fullmatch(r"[0-9a-f]{64}", approved_patch_sha256):
        raise ValueError("approved calculation patch must be a SHA-256")
    patch = subprocess.check_output(
        ["git", "diff", "--no-ext-diff", "--no-color", "--binary", baseline, "--",
         "app/energy_model", "app/forecasting", "app/energy_plan/workflow.py",
         "app/operations/forecast_persistence.py", "app/operations/forecast_recovery.py"],
        text=True, encoding="utf-8",
    )
    digest = hashlib.sha256(patch.encode("utf-8")).hexdigest() if patch else ""
    if digest != approved_patch_sha256:
        raise AssertionError("canonical calculation/publication source differs from the explicitly approved patch")
    return {"canonical_calculation_source_unchanged": not bool(patch),
            "approved_calculation_patch_sha256": digest}


def verify(backup: Path, baseline: str, *, approved_calculation_patch_sha256: str = "") -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{40}", baseline):
        raise ValueError("baseline must be a full Git commit SHA")
    calculation_evidence = verify_calculation_source(baseline, approved_calculation_patch_sha256)
    source = subprocess.check_output(["git", "show", baseline + ":app/runtime/cloud_job.py"], text=True, encoding="utf-8")
    old: Any = ModuleType("_frozen_cloud_job")
    sys.modules[old.__name__] = old
    exec(compile(source, "<trusted-git-baseline>", "exec"), old.__dict__)
    rate = {"percent_per_hour": 20.0, "source": "same-frozen-input-in-both-runtimes"}
    old.estimate_forced_charge_rate_percent_per_hour = lambda paths: rate
    saved_path = cloud_job._night_plan_path
    original_env = os.environ.get("KP_NET_MODE_ONLY_23_03_07")
    os.environ["KP_NET_MODE_ONLY_23_03_07"] = "true"
    rows: list[dict[str, Any]] = []
    try:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            for inventory in sorted((backup / "storage").glob("bucket-*-objects.json")):
                for item in json.loads(inventory.read_text(encoding="utf-8")):
                    name = item["name"]
                    if not re.fullmatch(r"night_charge_plans/decisions/2026-10-0[4-7]/[0-9a-f]+\.json\.gz", name):
                        continue
                    compressed = (backup / item["local_file"]).read_bytes()
                    if hashlib.sha256(compressed).hexdigest() != item["local_sha256"]:
                        raise ValueError("backup object checksum mismatch")
                    raw = gzip.decompress(compressed) if compressed.startswith(b"\x1f\x8b") else compressed
                    body = json.loads(raw)
                    day = body["forecast"]["date"]
                    target = float(body["result"]["target_soc_7_percent"])
                    plan = PublishedPlan.from_bytes(raw, target_date=day, charge_rate_info=rate)
                    path = root / "old.json"
                    path.write_bytes(raw)
                    traces = 0
                    scenarios: list[list[float | None]] = [[max(0, target - 20), target], [max(0, target - 20), None, None, None]]
                    for values in scenarios:
                        old_clock, new_clock = ReplayClock(day), ReplayClock(day)
                        issued = datetime.fromisoformat(body["generated_at"].replace("Z", "+00:00"))
                        start = max(old_clock.at, issued + timedelta(seconds=1))
                        old_clock.at = new_clock.at = start
                        old_device, new_device = ReplayDevice(values), ReplayDevice(values)
                        cloud_job._night_plan_path = lambda: root / "new.json"
                        with redirect_stdout(io.StringIO()):
                            old._monitor_partial_forced_and_stop(path, clock=old_clock, device_port=old_device)
                            control.run_slot("03", store=SimpleNamespace(fetch=lambda *a, **kw: plan),
                                             clock=new_clock, device_port=new_device)
                        if old_device.events != new_device.events or old_clock.at != new_clock.at:
                            raise AssertionError("baseline/new physical operation or timing trace differs")
                        if (root / "new.json").read_bytes() != raw:
                            raise AssertionError("published original bytes differ")
                        traces += 1
                    rows.append({"day": day, "plan_sha256": plan.sha256, "raw_bytes": len(raw),
                                 "target_soc_percent": target, "required_charge_kwh": body["result"]["required_night_charge_kwh"],
                                 "matching_operation_traces": traces})
    finally:
        cloud_job._night_plan_path = saved_path
        if original_env is None:
            os.environ.pop("KP_NET_MODE_ONLY_23_03_07", None)
        else:
            os.environ["KP_NET_MODE_ONLY_23_03_07"] = original_env
    if len(rows) < 4:
        raise ValueError("four production decision archives are required")
    return {"status": "passed", "baseline": baseline, "plans": rows,
            **calculation_evidence,
            "scope": "saved production decisions, byte/value preservation and simulated device traces; not physical-device proof"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--approved-calculation-patch-sha256", default="")
    args = parser.parse_args()
    result = verify(args.backup, args.baseline, approved_calculation_patch_sha256=args.approved_calculation_patch_sha256)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Parity passed: {len(result['plans'])} production plans; original bytes, values, control traces and timing match")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
