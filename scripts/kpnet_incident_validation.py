"""Static validation for the independent scheduled KP-NET ownership contract."""
from __future__ import annotations

from pathlib import Path


def run_independent_slot_validation() -> dict[str, bool]:
    source = Path("app/runtime/slot_orchestration.py").read_text(encoding="utf-8")
    return {
        "slot23_standby": 'profile="standby"' in source,
        "slot07_green": 'profile="green"' in source,
        "no_day_gate": "_assert_day_transition_allowed" not in source,
    }
