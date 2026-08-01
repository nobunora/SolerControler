"""Compatibility exports for occupancy schedule forecasting.

New code should import from :mod:`app.forecasting.occupancy`.
"""

from app.forecasting.occupancy import (
    OCCUPANCY_SCHEDULE_HEADERS,
    OCCUPANCY_SCHEDULE_TAB,
    OccupancyAdjustment,
    OccupancyScheduleEvent,
    apply_occupancy_event,
    apply_occupancy_schedule,
    events_from_values,
    filter_training_load_rows,
    find_event_for_date,
    load_occupancy_events_from_env,
    load_occupancy_events_from_path,
    load_occupancy_events_from_sheet,
    should_include_training_date,
)

__all__ = [
    "OCCUPANCY_SCHEDULE_HEADERS", "OCCUPANCY_SCHEDULE_TAB", "OccupancyAdjustment",
    "OccupancyScheduleEvent", "apply_occupancy_event", "apply_occupancy_schedule",
    "events_from_values", "filter_training_load_rows", "find_event_for_date",
    "load_occupancy_events_from_env", "load_occupancy_events_from_path",
    "load_occupancy_events_from_sheet", "should_include_training_date",
]
