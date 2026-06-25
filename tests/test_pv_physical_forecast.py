from __future__ import annotations

from datetime import datetime

import pytest

from app.pv_physical_forecast import build_physical_pv_candidate


def _forecast(shortwave: float | None) -> dict[str, object]:
    hourly = []
    for hour in range(7, 23):
        row: dict[str, object] = {"hour": hour}
        if shortwave is not None:
            row["shortwave_radiation_w_m2"] = shortwave
        hourly.append(row)
    return {"date": "2026-06-25", "hourly_weather": hourly}


def test_physical_pv_falls_back_without_shortwave(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHYSICAL_PV_FORECAST_ENABLED", "true")
    existing = {hour: 0.1 for hour in range(7, 23)}

    candidate = build_physical_pv_candidate(
        rows=[],
        forecast_history={},
        existing_hourly_pv=existing,
        forecast=_forecast(None),
        target_date="2026-06-25",
        lat=35.67452,
        lon=139.48216,
        timezone="Asia/Tokyo",
    )

    assert candidate.hourly_pv_kwh == existing
    assert candidate.diagnostics["selected_method"] == "existing"
    assert candidate.diagnostics["fallback_reason"] == "shortwave_missing"


def test_physical_pv_uses_global_scale_when_history_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHYSICAL_PV_FORECAST_ENABLED", "true")
    monkeypatch.setenv("PHYSICAL_PV_GLOBAL_MIN_DAYS", "2")
    monkeypatch.setenv("PHYSICAL_PV_DAYPART_MIN_SAMPLES", "999")
    monkeypatch.setenv("PHYSICAL_PV_BIN_MIN_SAMPLES", "999")
    monkeypatch.setenv("PHYSICAL_PV_RADIATION_SCALE", "1.0")
    rows = []
    history = {}
    for day in ["2026-06-21", "2026-06-22"]:
        history[day] = {hour: {"shortwave": 800.0, "pv": 0.5, "load": 0.2} for hour in range(7, 23)}
        for hour in range(7, 23):
            rows.append({"dt": datetime.fromisoformat(f"{day}T{hour:02d}:00:00"), "pv": 0.5})

    candidate = build_physical_pv_candidate(
        rows=rows,
        forecast_history=history,
        existing_hourly_pv={hour: 0.1 for hour in range(7, 23)},
        forecast=_forecast(800.0),
        target_date="2026-06-25",
        lat=35.67452,
        lon=139.48216,
        timezone="Asia/Tokyo",
    )

    assert candidate.diagnostics["enabled"] is True
    assert candidate.diagnostics["selected_method"] == "physical_global"
    assert "selected_physical_global" in candidate.diagnostics["decision_path"]
    assert sum(candidate.hourly_pv_kwh.values()) > 0
