"""Read-only acceptance probe using the production SOC acquisition path."""

from __future__ import annotations

import json
import time

from app.runtime.night_soc_time_contract import SOC_OPERATION_MAX_SECONDS
from app.runtime.soc_reading import latest_realtime_soc_reading, read_realtime_soc_with_retry


def run_probe() -> dict[str, object]:
    deadline = time.monotonic() + SOC_OPERATION_MAX_SECONDS
    reading = read_realtime_soc_with_retry(
        latest_realtime=lambda: latest_realtime_soc_reading(deadline_monotonic=deadline),
        env_int=lambda _name, default: default,
        env_float=lambda _name, default: default,
        deadline_monotonic=deadline,
    )
    if reading.value_percent is None or reading.observed_at is None or reading.retrieved_at is None:
        raise RuntimeError("SOC API probe returned no timestamped measurement")
    evidence: dict[str, object] = {
        "message": "soc-api-probe", "status": "passed", "source": "kpnet_mobile_api",
        "soc_percent": reading.value_percent, "device_measured_at": reading.observed_at.isoformat(),
        "retrieved_at": reading.retrieved_at.isoformat(),
    }
    print(json.dumps(evidence), flush=True)
    return evidence


if __name__ == "__main__":
    try:
        run_probe()
    except Exception as exc:
        print(json.dumps({"message": "soc-api-probe", "status": "failed", "error_type": type(exc).__name__}), flush=True)
        raise SystemExit(1) from None
