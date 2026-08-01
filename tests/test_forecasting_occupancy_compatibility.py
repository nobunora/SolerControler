from __future__ import annotations

import app.forecasting.occupancy as canonical
import app.occupancy_schedule as legacy


def test_legacy_occupancy_schedule_exports_canonical_objects() -> None:
    assert legacy.OccupancyScheduleEvent is canonical.OccupancyScheduleEvent
    assert legacy.apply_occupancy_schedule is canonical.apply_occupancy_schedule
    assert legacy.events_from_values is canonical.events_from_values
