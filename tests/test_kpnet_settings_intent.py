from __future__ import annotations

import pytest

from app.kpnet import build_settings_intent


def test_settings_intent_is_pure_and_records_expected_changes() -> None:
    current = {"batteryOperatingMode": "1", "socChargeMode": "50"}
    desired = {"batteryOperatingMode": "2", "socChargeMode": "80", "_csrf": "secret"}

    intent = build_settings_intent(
        profile_name="forced",
        current_values=current,
        desired_values=desired,
        changed_fields=["batteryOperatingMode", "socChargeMode"],
        dry_run=True,
        reasons=("night_charge_required",),
    )

    assert intent.dry_run is True
    assert intent.reasons == ("night_charge_required",)
    assert [(c.field, c.current_value, c.desired_value) for c in intent.expected_changes] == [
        ("batteryOperatingMode", "1", "2"),
        ("socChargeMode", "50", "80"),
    ]
    assert current == {"batteryOperatingMode": "1", "socChargeMode": "50"}


def test_settings_intent_represents_no_change() -> None:
    intent = build_settings_intent(
        profile_name="green",
        current_values={},
        desired_values={},
        changed_fields=[],
        dry_run=False,
    )
    assert intent.has_changes is False


def test_settings_intent_rejects_changed_field_missing_from_payload() -> None:
    with pytest.raises(ValueError, match="missing from desired_values"):
        build_settings_intent(
            profile_name="forced",
            current_values={"socChargeMode": "50"},
            desired_values={},
            changed_fields=["socChargeMode"],
            dry_run=False,
        )


def test_settings_intent_deduplicates_changed_fields_without_reordering() -> None:
    intent = build_settings_intent(
        profile_name="forced",
        current_values={"socChargeMode": "50"},
        desired_values={"socChargeMode": "80"},
        changed_fields=["socChargeMode", "socChargeMode"],
        dry_run=False,
    )
    assert [change.field for change in intent.expected_changes] == ["socChargeMode"]
