"""Stable result document assembly for forecast correction."""

from __future__ import annotations

from typing import Any


def assemble_forecast_correction_result(
    *,
    corrected_load: dict[int, float],
    corrected_pv: dict[int, float],
    raw_load: dict[int, float],
    raw_pv: dict[int, float],
    load_scenarios: list[dict[str, Any]],
    paired_scenarios: list[dict[str, Any]],
    peak_penalty: dict[str, object],
    rationale_details: dict[str, Any],
) -> dict[str, object]:
    """Build the persisted correction shape without changing its public keys."""
    rationale = {
        **rationale_details,
        "raw_hourly_load_forecast_kwh": {str(k): round(v, 4) for k, v in sorted(raw_load.items())},
        "raw_hourly_pv_forecast_kwh": {str(k): round(v, 4) for k, v in sorted(raw_pv.items())},
        "corrected_hourly_load_forecast_kwh": {str(k): round(v, 4) for k, v in sorted(corrected_load.items())},
        "corrected_hourly_pv_forecast_kwh": {str(k): round(v, 4) for k, v in sorted(corrected_pv.items())},
    }
    return {
        "enabled": True,
        "hourly_load_kwh": corrected_load,
        "hourly_pv_kwh": corrected_pv,
        "load_scenarios": load_scenarios,
        "paired_scenarios": paired_scenarios,
        "peak_penalty": peak_penalty,
        "rationale": rationale,
    }
