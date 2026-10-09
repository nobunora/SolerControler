"""Read-only acceptance probe using the production SOC acquisition path."""

from __future__ import annotations

import json

from app.runtime.soc_reading import latest_realtime_soc_reading


def run_probe() -> dict[str, object]:
    reading = latest_realtime_soc_reading()
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
