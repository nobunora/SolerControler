# Architecture Hardening Progress

This file is the single source of truth for phase status, preserved contracts, blockers, and handoffs.

## Phase status

| Phase | Status | Commit | Notes |
|---|---|---|---|
| 01 Baseline and guardrails | not started | - | |
| 02 Shared boundaries | not started | - | |
| 03 Operations deduplication | not started | - | |
| 04 Forced-charge orchestration | not started | - | |
| 05 Dashboard repository boundary | not started | - | |
| 06 Energy-model decomposition | not started | - | |
| 07 Integration and closeout | not started | - | |

Allowed status values:

- `not started`
- `in progress`
- `blocked`
- `completed`

## Baseline evidence

Review date: 2026-07-18.

### Repository

- Worktree was clean before documentation changes.
- No source code was modified during the architecture review.
- No obvious application-level import cycle was observed.

### Confirmed tests

Energy model and SoC optimizer:

    tests/test_energy_model.py
    tests/test_soc_cost_optimizer.py
    50 passed

Cloud job, forced-charge state machine, and KP-NET:

    tests/test_cloud_job_runner.py
    tests/test_forced_charge_state_machine.py
    tests/test_kpnet_workflow.py
    97 passed

Dashboard and operations:

    tests/test_dashboard_data.py
    tests/test_dashboard_backend_parity.py
    tests/test_operations_db.py
    tests/test_firestore_dashboard_metrics.py
    44 passed

Confirmed total: 191 passed.

The complete non-external pytest command exceeded the AI Pwsh Bridge wait window. Its final result is unknown and must not be reported as passed or failed.

### Type-check baseline

Command:

    python -m mypy app energy_model_main.py cloud_job_runner.py db_pipeline_main.py dashboard_server.py

Result:

    Found 92 errors in 10 files
    Checked 51 source files

Do not increase errors in the assigned scope.

## Structural risk map

Do not rescan the whole repository merely to reproduce these numbers.

- `energy_model_main.py`: about 2,559 lines
- `app/dashboard_data.py`: about 1,718 lines
- `app/kpnet_workflow.py`: about 1,628 lines
- `cloud_job_runner.py`: about 1,228 lines
- `_monitor_partial_forced_and_stop`: about 263 lines
- `_load_sqlite_slice`: about 240 lines
- `_load_postgres_slice`: about 240 lines
- `_load_firestore_slice`: about 194 lines
- `_run_soc_optimization`: about 216 lines
- `evaluate_soc_candidate`: 20 parameters
- `optimize_soc_by_expected_cost`: 22 parameters
- `AppConfig`: 49 fields
- Direct environment accesses: about 187
- Broad exception catches: about 66
- Lines containing `Any`: about 391
- Lines containing raw dictionary types: about 395

These values are prioritization evidence, not permanent quality gates.

## Contracts that remain stable

Unless a later approved handoff explicitly changes one, preserve:

- Environment key names and defaults
- Database table and column names
- Firestore collection and field names
- Plan and dashboard JSON field names
- Timezone and accounting-day boundaries
- Tariff tiers and rounding behavior
- Missing, empty, zero, and `None` semantics
- Forced-charge cutoff, timeout, retry, and fail-safe behavior
- Public CLI behavior
- Dashboard output shape
- Energy-plan output shape

## Accepted architecture decisions

1. No big-bang rewrite.
2. Shared business calculations move to pure functions.
3. Adapters remain responsible for loading, mapping, and saving.
4. Backends migrate one at a time.
5. Old implementations remain until parity is demonstrated.
6. Environment parsing moves only within the active phase scope.
7. Raw dictionaries remain only at I/O and compatibility boundaries.
8. Feature changes and structural changes use separate commits.
9. Large modules are read by symbol range, not as full files.
10. Each execution request should remain small enough to finish in about one minute.

## Current blockers

None.

## Follow-up queue

- Repair the corrupted Japanese `FileNotFoundError` message in `app/main.py` during Phase 02 if it remains behavior-neutral.
- Reduce existing mypy errors by module, not through a repository-wide cleanup.
- Confirm the full non-external test suite during Phase 07 using smaller command groups.

## Handoff records

Append new records below. Do not delete or rewrite older handoffs.

### Handoff template

- Date:
- Phase and step:
- Status:
- Commit:
- Intent:
- Changed files:
- Preserved contracts:
- Tests run:
- Static checks:
- Behavior differences:
- New typed boundaries:
- Removed duplication:
- Remaining risks:
- Next agent must read:
- Next target symbols:
- Do not reread:
- Blockers:
