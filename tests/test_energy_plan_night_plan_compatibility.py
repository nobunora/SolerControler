from __future__ import annotations

import app.energy_plan.night_plan as canonical
import app.night_plan as legacy


def test_legacy_night_plan_exports_canonical_objects() -> None:
    for name in legacy.__all__:
        assert getattr(legacy, name) is getattr(canonical, name)
