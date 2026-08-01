from __future__ import annotations

import app.local_control.models as canonical
import app.models as legacy


def test_legacy_models_export_canonical_objects() -> None:
    for name in legacy.__all__:
        assert getattr(legacy, name) is getattr(canonical, name)
