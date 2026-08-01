from __future__ import annotations

import app.energy_plan.soc_cost as canonical
import app.soc_cost_optimizer as legacy


def test_legacy_soc_cost_exports_canonical_objects() -> None:
    for name in legacy.__all__:
        assert getattr(legacy, name) is getattr(canonical, name)
