"""I/O-free time ownership for independent 23:00, 03:00, and 07:00 jobs."""

from __future__ import annotations

from datetime import datetime, time


# HISTORICAL_FAILURE_LOCK (2026-08-29 user-authorized time-ownership replacement):
# do not move these boundaries later or make 03 depend on a 23/07 Firestore state.
# 03 must stop forced monitoring at 06:45.  A KP profile operation is bounded
# to 240 seconds and release/logout reserves 60 seconds, so final standby must
# start by 06:50 and all 03 I/O ends at 06:55, leaving green at 07:00.
# Otherwise a slow 03 KP-NET write can race 07:00 and overwrite green after it
# has been read back. Guarded by test_night_soc_time_ownership.py.
FORCED_MONITOR_CUTOFF = time(6, 45)
FINAL_STANDBY_START_CUTOFF = time(6, 50)
CONTROL_HARD_CUTOFF = time(6, 55)
GREEN_START = time(7, 0)
MODE_OPERATION_MAX_SECONDS = 240
MODE_OPERATION_RELEASE_RESERVE_SECONDS = 60
MODE_OPERATION_START_BUDGET_SECONDS = MODE_OPERATION_MAX_SECONDS + MODE_OPERATION_RELEASE_RESERVE_SECONDS
SOC_OPERATION_MAX_SECONDS = 60


def may_start_03_io(now: datetime) -> bool:
    """Allow a new 03:00 external operation only before the hard control cutoff."""
    return now.timetz().replace(tzinfo=None) < CONTROL_HARD_CUTOFF


def must_stop_forced_monitoring(now: datetime) -> bool:
    """Return true at the dedicated forced-monitor cutoff."""
    return now.timetz().replace(tzinfo=None) >= FORCED_MONITOR_CUTOFF


def may_start_final_standby(now: datetime) -> bool:
    """Allow the last bounded standby operation only with its full 5-minute budget."""
    return now.timetz().replace(tzinfo=None) < FINAL_STANDBY_START_CUTOFF


def seconds_until_control_cutoff(now: datetime) -> int:
    """Bound a child operation so it cannot continue into 07:00 ownership."""
    cutoff = now.replace(hour=6, minute=55, second=0, microsecond=0)
    return max(0, int((cutoff - now).total_seconds()))


def seconds_until_forced_monitor_cutoff(now: datetime) -> int:
    """Bound realtime SOC work to the 06:45 forced-monitor owner window."""
    cutoff = now.replace(hour=6, minute=45, second=0, microsecond=0)
    return max(0, int((cutoff - now).total_seconds()))
