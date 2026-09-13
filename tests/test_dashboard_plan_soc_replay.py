from __future__ import annotations

from app.operations.dashboard_plan_soc_replay import (
    build_missing_plan_soc_patch,
    candidate_from_forecast_plan,
)


def test_forecast_plan_before_07_is_valid_replay_evidence() -> None:
    candidate = candidate_from_forecast_plan(
        {
            "date": "2026-09-07",
            "forecast_run_id": "run-a",
            "forecast_issued_at": "2026-09-06T17:30:00Z",
            "planned_target_soc_percent": 72.0,
            "planned_night_charge_kwh": 3.2,
        }
    )

    assert candidate is not None
    assert candidate.date == "2026-09-07"
    assert candidate.target_soc_percent == 72.0
    assert candidate.night_charge_kwh == 3.2


def test_late_forecast_plan_is_not_accepted_as_historical_setting() -> None:
    candidate = candidate_from_forecast_plan(
        {
            "date": "2026-09-07",
            "updated_at": "2026-09-07T08:00:00+09:00",
            "planned_target_soc_percent": 72.0,
        }
    )

    assert candidate is None


def test_replay_patch_never_overwrites_existing_control_values() -> None:
    candidate = candidate_from_forecast_plan(
        {
            "date": "2026-09-07",
            "forecast_issued_at": "2026-09-06T17:30:00Z",
            "planned_target_soc_percent": 72.0,
            "planned_night_charge_kwh": 3.2,
        }
    )
    assert candidate is not None

    patch = build_missing_plan_soc_patch(
        {
            "setting_soc_target_percent": 80.0,
            "night_charge_kwh": 4.0,
            "settings_run_id": "strong-control-evidence",
        },
        candidate,
        replayed_at="2026-09-13T23:00:00+09:00",
    )

    assert patch == {}


def test_replay_patch_fills_only_missing_fields_with_provenance() -> None:
    candidate = candidate_from_forecast_plan(
        {
            "date": "2026-09-07",
            "forecast_run_id": "run-a",
            "forecast_issued_at": "2026-09-06T17:30:00Z",
            "planned_target_soc_percent": 72.0,
            "planned_night_charge_kwh": 3.2,
        }
    )
    assert candidate is not None

    patch = build_missing_plan_soc_patch(
        {"night_charge_kwh": 4.0},
        candidate,
        replayed_at="2026-09-13T23:00:00+09:00",
    )

    assert patch["setting_soc_target_percent"] == 72.0
    assert "night_charge_kwh" not in patch
    assert patch["plan_display_source"] == "forecast_plans_replay"
    assert patch["plan_replay_forecast_run_id"] == "run-a"
