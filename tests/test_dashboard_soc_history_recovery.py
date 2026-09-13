from __future__ import annotations

from app.dashboard.slice_assembler import merge_forecast_history_into_battery_daily


def _forecast_rows(day: str, *, target: float | None, night_kwh: float | None = None) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for hour in range(24):
        row: dict[str, object] = {
            "date": day,
            "hour": hour,
            "source": "forecast_hourly_snapshot",
            "forecast_run_id": f"run-{day}",
        }
        if target is not None:
            row["forecast_target_soc_percent"] = target
        if night_kwh is not None:
            row["forecast_night_charge_kwh"] = night_kwh
        rows.append(row)
    return rows


def test_recovers_configured_soc_for_every_historical_day() -> None:
    forecast = [
        *_forecast_rows("2026-09-06", target=65.0, night_kwh=2.1),
        *_forecast_rows("2026-09-07", target=72.0, night_kwh=3.2),
    ]

    rows = merge_forecast_history_into_battery_daily([], forecast)

    assert rows == [
        {
            "date": "2026-09-06",
            "setting_soc_target_percent": 65.0,
            "night_charge_kwh": 2.1,
            "plan_display_source": "forecast_plans",
        },
        {
            "date": "2026-09-07",
            "setting_soc_target_percent": 72.0,
            "night_charge_kwh": 3.2,
            "plan_display_source": "forecast_plans",
        },
    ]


def test_applied_control_evidence_has_priority_over_forecast_plan() -> None:
    rows = merge_forecast_history_into_battery_daily(
        [
            {
                "date": "2026-09-07",
                "setting_soc_target_percent": 80.0,
                "night_charge_kwh": 4.0,
                "settings_run_id": "control-run",
            }
        ],
        _forecast_rows("2026-09-07", target=72.0, night_kwh=3.2),
    )

    assert rows[0]["setting_soc_target_percent"] == 80.0
    assert rows[0]["night_charge_kwh"] == 4.0
    assert "plan_display_source" not in rows[0]


def test_inconsistent_forecast_plan_soc_fails_closed() -> None:
    rows = _forecast_rows("2026-09-07", target=72.0)
    rows[-1]["forecast_target_soc_percent"] = 73.0

    recovered = merge_forecast_history_into_battery_daily([], rows)

    assert recovered == []


def test_reconstructed_forecast_never_fabricates_configured_soc() -> None:
    rows = _forecast_rows("2026-08-30", target=77.0)
    for row in rows:
        row["source"] = "historical_reconstructed_estimate"
        row["is_reconstructed"] = True

    recovered = merge_forecast_history_into_battery_daily([], rows)

    assert recovered == []
