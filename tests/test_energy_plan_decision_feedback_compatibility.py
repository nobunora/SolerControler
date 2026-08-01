from __future__ import annotations

import app.energy_plan.decision_feedback as canonical
import app.soc_decision_feedback as legacy


def test_legacy_decision_feedback_exports_canonical_objects() -> None:
    for name in legacy.__all__:
        assert getattr(legacy, name) is getattr(canonical, name)
