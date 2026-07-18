# Architecture Hardening Progress

This file is the single source of truth for phase status, preserved contracts, blockers, and handoffs.

## Program-level alignment record

Every phase and step must be evaluated against `VISION_AND_DECISION_PRINCIPLES.md`.

A status of `completed` requires evidence for both:

- Preserved behavior and contracts
- Improved ownership, boundaries, or required context

Passing tests alone is not sufficient.

For every phase, record:

- Why the phase is necessary at the system level
- How it contributes to the final target
- Which business meaning gains clearer ownership
- Which local-optimization risks were considered
- Which behavior evidence supports the change
- Which ownership evidence supports the change
- Which work was intentionally deferred
- What the next phase must not undo
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
## Why this progress file is necessary

The implementation will be distributed across phases, commits, and possibly multiple agents.

Without a shared progress record, later agents may see only the current code and make decisions that:

- Repeat completed analysis
- Reintroduce removed duplication
- Break compatibility wrappers that still have a purpose
- Optimize a local file without understanding the system-level objective
- Treat a temporary state as the intended final architecture
- Lose the reason a decision was deferred
- Mark work complete based only on passing tests

`PROGRESS.md` therefore preserves architectural intent across time and agent boundaries.

Its purpose is not only to list completed tasks. It must explain how each completed task moves the system toward controlled ownership of behavior.

## Progress-file-specific final target

When maintained correctly, this file allows a future agent to determine:

- What the final architecture is intended to achieve
- Why the current phase exists
- Which behavior and compatibility contracts are protected
- Which business meanings already have a clear owner
- Which temporary compatibility structures remain
- Which risks were considered
- Which work was deliberately deferred
- What must not be reversed in the next phase

The next agent should be able to continue from the handoff without reconstructing the entire repository history.

## Progress-file-specific local-optimization risks

This file must prevent:

- Reporting only changed files and test counts
- Marking a phase complete without ownership evidence
- Treating fewer lines or fewer functions as the primary outcome
- Omitting deferred work because it is outside the current task
- Recording a backend-specific success as system-wide success
- Losing the connection between a local change and the final target
- Allowing the next phase to undo a boundary created by the previous phase

## Expanded handoff template

In addition to the existing handoff fields, every new handoff must include:

- System-level reason:
- Contribution to final target:
- Business meaning with clearer ownership:
- Local-optimization risks considered:
- Behavior evidence:
- Ownership evidence:
- Context reduction achieved:
- Intentionally deferred work:
- What the next phase must not undo:

A phase may be marked `completed` only when these fields contain specific evidence rather than general claims.
