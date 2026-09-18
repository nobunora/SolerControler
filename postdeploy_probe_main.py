from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.kpnet.config import KpNetConfig
from app.kpnet.csv_direct import run_csv_workflow
from app.kpnet.settings_roundtrip import SettingsRoundtripError, run_settings_roundtrip


def _run_plan_generation() -> Path:
    cfg = KpNetConfig.from_env()
    env = os.environ.copy()
    env["FORECAST_DATE_OVERRIDE"] = datetime.now(ZoneInfo("Asia/Tokyo")).date().isoformat()
    completed = subprocess.run(
        [sys.executable, "energy_model_main.py"],
        env=env,
        check=False,
        timeout=300,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"post-deploy plan generation failed rc={completed.returncode}")
    plan_path = cfg.night_plan_path
    if not plan_path.exists() or plan_path.stat().st_size <= 0:
        raise RuntimeError(f"post-deploy plan was not created: {plan_path}")
    return plan_path


def main() -> int:
    """End-to-end post-deploy production probe.

    Order is intentional and fail-closed:
    1. prove the live KP-NET CSV path;
    2. prove local night-plan generation from those artifacts;
    3. only then perform the reversible one-minute live settings round-trip.

    A failure in steps 1 or 2 never mutates device settings.  The settings
    round-trip snapshots all controlled fields, proves a real 03 forced
    write/read-back, holds exactly 60 seconds, then proves a real 07 economy
    write/read-back before restoring the exact initial snapshot. Cloud Run
    retries remain disabled by deployment policy.
    """
    summary: dict[str, object] = {
        "message": "postdeploy-live-probe",
        "csv": "not_started",
        "plan": "not_started",
        "settings_roundtrip": "not_started",
        "status": "failed",
    }
    try:
        csv_rc = run_csv_workflow()
        if csv_rc != 0:
            raise RuntimeError(f"post-deploy CSV probe failed rc={csv_rc}")
        summary["csv"] = "passed"

        plan_path = _run_plan_generation()
        summary["plan"] = "passed"
        summary["plan_path"] = str(plan_path)

        target_soc = float(os.getenv("SETTINGS_ROUNDTRIP_TARGET_SOC", "50"))
        roundtrip = run_settings_roundtrip(target_soc_percent=target_soc)
        if roundtrip.get("forced_proof") != "passed":
            raise RuntimeError("post-deploy 03 forced proof was not verified")
        if roundtrip.get("economy_proof") != "passed":
            raise RuntimeError("post-deploy 07 economy proof was not verified")
        if roundtrip.get("restore_verified") is not True:
            raise RuntimeError("post-deploy settings restore was not verified")
        summary["settings_roundtrip"] = "passed"
        summary["roundtrip_forced_proof"] = roundtrip.get("forced_proof")
        summary["roundtrip_economy_proof"] = roundtrip.get("economy_proof")
        summary["roundtrip_restore_verified"] = roundtrip.get("restore_verified")
        summary["settings_roundtrip_evidence"] = roundtrip
        summary["status"] = "passed"
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
        return 0
    except SettingsRoundtripError as exc:
        summary["settings_roundtrip"] = "failed"
        summary["settings_roundtrip_summary"] = exc.summary
        summary["error_type"] = type(exc).__name__
    except Exception as exc:
        summary["error_type"] = type(exc).__name__
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
