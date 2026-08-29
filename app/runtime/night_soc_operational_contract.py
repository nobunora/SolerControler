"""Immutable operational contracts for the scheduled 23:00 -> 03:00 -> 07:00 path.

This module intentionally has no I/O and no Cloud/KP-NET dependency.  It is the
single, testable definition of values which must remain stable across workflow,
controller, persistence, and deployment code.
"""

from typing import Final, Mapping


# HISTORICAL_FAILURE_LOCK (d1d7792, 1dd21ae, 3cdd48c, 2026-08-28/29 runtime
# evidence): NEVER add ``batteryOperatingMode`` to this tuple.  At 23:00 the
# controller must preserve the 12 SOC/window fields owned by the 03:00 planner,
# but it must replace the operating mode with the KP-NET standby candidate.  If
# this list includes batteryOperatingMode, a green value (1) is copied back over
# the standby candidate (5), producing a successful-looking 23:00 job that
# leaves the battery in green mode.  If any of the remaining fields is removed,
# the 23:00 guard can overwrite charge thresholds or 23:00--07:00 / 07:00--23:00
# windows before 03:00 has taken ownership.  Guarded by
# test_night_soc_protected_contract.py and test_kpnet_workflow.py.
SLOT23_PRESERVED_FIELDS: Final[tuple[str, ...]] = (
    "socSafetyMode",
    "socEconomyMode",
    "socContactInput",
    "socChargeMode",
    "chargeStartTimeH",
    "chargeStartTimeM",
    "chargeEndTimeH",
    "chargeEndTimeM",
    "dischargeStartTimeH",
    "dischargeStartTimeM",
    "dischargeEndTimeH",
    "dischargeEndTimeM",
)

# HISTORICAL_FAILURE_LOCK (EVIDENCE_20260829_DAY_GATE, 2026-08-29 03:00 forced-reapply failure): do not add a state here
# merely because a standby command was attempted. The allowed sequence is a
# durable read-back-confirmed standby/no-charge/final-verification then 07:00;
# adding STANDBY_UNCONFIRMED or generic failures turns green while KP-NET mode is
# unknown, physically risking the battery remaining forced/economy. Guarded by
# test_night_soc_protected_contract.py::test_protected_contract_has_documented_locks_at_each_operational_boundary
# and tests/test_cloud_job_runner.py 07-gate replay tests.
DAY_TRANSITION_ALLOWED_STATES: Final[frozenset[str]] = frozenset(
    {"STANDBY_ACKED", "COMPLETED_NO_CHARGE", "VERIFIED"}
)

FAIL_SAFE_STANDBY_ACKED_STATE: Final[str] = "STANDBY_ACKED"
FAIL_SAFE_STANDBY_UNCONFIRMED_STATE: Final[str] = "STANDBY_UNCONFIRMED"

# HISTORICAL_FAILURE_LOCK (2026-08-29 03:00 production retry evidence): this
# is the deployment contract for the Cloud Run 03 job, not the internal KP-NET
# retry budget.  Do not change it to 1.  A platform retry regenerates plan_id;
# the second attempt then fails its deliberately strict lease check and obscures
# the original device read-back mismatch.  The physical effect is that no
# verified terminal record remains for 07:00, so green transition correctly
# blocks even when SOC happens to be 100%.  scripts/deploy_gcp_jobs.ps1 must
# remain equal to this value.  Guarded by test_night_soc_protected_contract.py
# and test_production_deploy_scripts.py.
SLOT03_CLOUD_RUN_MAX_RETRIES: Final[int] = 0


def is_day_transition_allowed_state(state: object) -> bool:
    """Return true only for a durable terminal state permitted to reach 07:00."""
    return str(state or "") in DAY_TRANSITION_ALLOWED_STATES


def failure_terminal_values(*, stop_reason: str, standby_confirmed: bool) -> Mapping[str, str]:
    """Classify a failed 03:00 command without falsely claiming standby read-back."""
    return {
        "state": FAIL_SAFE_STANDBY_ACKED_STATE
        if standby_confirmed
        else FAIL_SAFE_STANDBY_UNCONFIRMED_STATE,
        "terminal_state": "failed_command" if standby_confirmed else "standby_unconfirmed",
        "stop_reason": stop_reason,
    }
