from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.runtime.night_soc_controller import (
    CONTROLLED_SETTING_FIELDS,
    build_device_soc_guard,
    compare_setting_readback,
    effective_target_soc,
    make_plan_snapshot,
    validate_soc_observation,
)


def test_plan_snapshot_is_stable_and_carries_revision() -> None:
    plan = {
        "forecast": {"date": "2026-08-20"},
        "result": {"target_soc_7_percent": 71, "required_night_charge_kwh": 6.9},
        "plan_revision": "3",
    }

    snapshot = make_plan_snapshot(plan)

    assert snapshot.plan_id.startswith("2026-08-20-3-")
    assert len(snapshot.content_hash) == 64
    assert snapshot.raw_target_soc_percent == pytest.approx(71)


def test_device_candidate_is_guard_but_stop_threshold_is_before_it() -> None:
    guard = build_device_soc_guard(
        {"50": "50%", "80": "80%", "100": "100%"},
        raw_target_soc_percent=71,
        stop_margin_percent=1,
    )

    assert guard.device_soc_code == "80"
    assert guard.device_soc_ceiling_percent == pytest.approx(80)
    assert guard.stop_threshold_percent == pytest.approx(70)


def test_device_candidate_without_safe_upper_value_is_rejected() -> None:
    with pytest.raises(ValueError, match="no candidate"):
        build_device_soc_guard(
            {"50": "50%"}, raw_target_soc_percent=71, stop_margin_percent=1
        )


def test_readback_comparison_reports_only_controlled_mismatches() -> None:
    requested = {field: "1" for field in CONTROLLED_SETTING_FIELDS}
    observed = dict(requested)
    observed["socChargeMode"] = "0"

    matched, mismatches = compare_setting_readback(requested, observed)

    assert matched is False
    assert mismatches == ("socChargeMode",)


def test_soc_observation_gate_rejects_stale_values() -> None:
    now = datetime.now(timezone.utc)
    valid, reason = validate_soc_observation(
        71,
        now - timedelta(seconds=361),
        now=now,
        max_age_seconds=360,
    )

    assert valid is False
    assert reason == "soc_stale"


def test_effective_target_respects_minimum_and_upper_bound() -> None:
    assert effective_target_soc(20, 30) == pytest.approx(30)
    assert effective_target_soc(110, 30) == pytest.approx(100)
