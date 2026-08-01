from __future__ import annotations

import app.forecasting.pv_physical as canonical
import app.pv_physical_forecast as legacy


def test_legacy_physical_pv_forecast_exports_canonical_objects() -> None:
    assert legacy.PhysicalPvCandidate is canonical.PhysicalPvCandidate
    assert legacy.build_physical_pv_candidate is canonical.build_physical_pv_candidate
