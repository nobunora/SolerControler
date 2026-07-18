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
| 01 Baseline and guardrails | completed | 29d039f | Baseline and high-risk Operations cost contracts recorded. |
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

### 2026-07-19 Phase 01 handoff

- Date: 2026-07-19
- Phase and step: Phase 01, Steps 01.1-01.8
- Status: completed
- Commit: `29d039f` (test-only characterization coverage)
- Intent: Freeze comparison evidence before production ownership moves between adapters, orchestrators, and typed boundaries.
- Changed files: `tests/test_operations_db.py`; this progress record.
- Preserved contracts: Public APIs, CLI behavior, environment keys/defaults, persistence fields, timezone/date interpretation, tariff tiers, unrounded float results, missing/zero semantics, forced-charge fail-safe behavior, dashboard shape/cache behavior, and energy-plan V1 fields.
- Tests run:
  - Group A: `python -m pytest -q tests/test_operations_domain.py tests/test_operations_db.py tests/test_dashboard_backend_parity.py` -> 22 passed after the added tests (15 passed before the change).
  - Group B: `python -m pytest -q tests/test_forced_charge_state_machine.py tests/test_cloud_job_runner.py` -> 60 passed.
  - Group C: `python -m pytest -q tests/test_dashboard_data.py tests/test_firestore_dashboard_metrics.py` -> 32 passed.
  - Group D: `python -m pytest -q tests/test_energy_model.py tests/test_energy_model_runtime.py tests/test_soc_cost_optimizer.py` -> 54 passed.
  - Focused changed-file check: `python -m pytest -q tests/test_operations_db.py` -> 17 passed.
- Static checks:
  - `python -m compileall -q app energy_model_main.py cloud_job_runner.py` -> passed.
  - `git diff --check` -> passed (Git emitted only the configured LF-to-CRLF conversion warning).
  - `python -m mypy app energy_model_main.py cloud_job_runner.py db_pipeline_main.py dashboard_server.py` -> installed and unchanged baseline: 92 errors in 10 files, 51 files checked.
- Behavior differences: None; only tests and this handoff changed.
- New typed boundaries: None added. Existing boundaries confirmed: `DashboardRawData`/`DashboardSlice`, forced-charge policy/observation/transition models, `EnergyModelConfig`/`EnergyModelContext`, and `PlanDocumentV1`.
- Removed duplication: None in this baseline phase.
- Operations behavior matrix:
  - Inputs: monitoring timestamp, load/buy kWh, tariff mode/rates/windows, and update timestamp; all three adapters expose matching calculation parameters.
  - Return/write: returns `None`; upserts `date`, `self_consumption_kwh`, `savings_yen`, cumulative kWh/yen, and `updated_at` into `cost_daily` (Firestore uses merge writes).
  - Missing/zero: empty input writes nothing; null load/buy become zero; malformed/blank timestamps are skipped; absent intervals are not interpolated; a valid all-null sample produces a zero-valued day.
  - Negative/simultaneous: night8 clamps load and buy independently to zero before `max(load-buy, 0)`; simultaneous positive load/buy uses their nonnegative difference.
  - Time/tariff/rounding: ISO timestamp's represented date and local clock determine accounting day/window; day window is start-inclusive/end-exclusive; monthly tier accumulators reset by `YYYY-MM`; exact and adjacent tier boundaries retain float precision with no final rounding.
  - Backend differences: loading and persistence syntax/batching differ; inspected calculation order and shared `app.operations.domain.tiered_increment_cost` use agree. No conflicting fixture result was found.
  - Requested legacy names `upsert_battery_metrics`, `update_model_params`, and `update_prediction_hit_rates` are absent; current names are `upsert_battery_daily_metrics`, `upsert_model_parameters_from_plan`, and `recalc_model_hit_rates`.
- Forced-charge behavior matrix:
  - States: initializing, holding standby, starting forced, monitoring, stopping, six terminal success/failure states.
  - Start/skip: missing plan fails without a device command; missing initial SoC defaults to standby; target already reached/no required charge skips forced mode; otherwise forced mode starts.
  - Stop/failure priority: target/hysteresis, cutoff, sensor failure limit, then runtime limit; every monitoring terminal requests standby first. Failed standby confirmation becomes command failure.
  - External failures/retry: initial/reapply command failure and sensor exceptions attempt fail-safe standby and preserve failure; consecutive missing SoC stops safely; stagnant SoC can reapply forced mode under the configured policy.
  - Safety contracts: timezone-aware cutoff, bounded finite SoC, positive runtime/failure limits, configured cutoff/poll/retry semantics, and no real KP-NET/cloud calls in baseline tests.
- Dashboard behavior matrix:
  - Public shape: `DashboardData` contains pv/cost/monthly/battery/model/flow/energy/forecast rows, latest schedule, warnings, diagnostics, and daily reviews; `DashboardSlice` adds meta.
  - Ordering/selection: forecast rows sort by date/hour; latest schedule selection is input-order independent and does not mix plan dates or run IDs; monitor then no-charge sources have explicit priority.
  - Missing/fallback/cache: missing backend data returns typed empty collections/default schedule; legacy full-load API delegates to a 365-day slice; Firestore cache keys include project, database, end date, days, and static flag and has explicit clearing.
  - Backend differences: loaders map SQLite, PostgreSQL, and Firestore storage independently into `DashboardRawData`; shared assembly preserves normalized values. Parity comparison tolerates float storage noise and ignored metadata while reporting coverage/field differences.
- Energy-model behavior matrix:
  - Entrypoint/input: `main() -> int` builds `EnergyModelConfig.from_env`, loads CSV history, coefficients, forecast, current SoC and occupancy context, then runs forecast/constraint/optimization stages.
  - Files/env: CSV paths come from explicit env or latest artifacts; output is `<ARTIFACTS_DIR>/night_charge_plan.json`; invalid numeric config values continue to raise `ValueError`; timezone default remains `Asia/Tokyo`.
  - Fallback/optimization: runtime tests preserve loaded values and forecast diagnostics/fallback reason; cost optimization may return the legacy peak-SoC objective when no cost result is available.
  - Output: `PlanDocumentV1` fixes 14 top-level fields and preserves consumer result keys including `target_soc_7_percent` and `required_night_charge_kwh`; JSON is UTF-8, non-ASCII preserving, indented output.
- Existing exact helper clones found: `recalc_cost_daily` retains matching calculation bodies in `app/operations_db.py`, `app/postgres_ops.py`, and `app/firestore_ops.py`; all already delegate tier cost/window parsing to `app.operations.domain`.
- Small typed boundaries already present: `app/dashboard/models.py`, `app/forced_charge/state_machine.py`, `EnergyModelConfig`/`EnergyModelContext`, and `app/energy_plan/models.py::PlanDocumentV1`.
- Remaining risks: Adapter-level parity for identical live-shaped fixtures is not exhaustive; production I/O was intentionally not exercised; mypy baseline remains 92 errors.
- Next agent must read: `02_SHARED_BOUNDARIES.md`; `PROGRESS.md` Phase 01 handoff; `app/main.py` around the corrupted `FileNotFoundError`; `app/operations/domain.py` shared helper exports; adapter import blocks in `app/operations_db.py`, `app/postgres_ops.py`, and `app/firestore_ops.py` count as one symbol range.
- Next target symbols: Phase 02 exact-clone inventory; `app.main._copy_local_csv`/nearby `FileNotFoundError`; shared operations helper aliases and existing typed models.
- Do not reread: Full `energy_model_main.py`, full `cloud_job_runner.py`, full `app/dashboard_data.py`, or the three full Operations adapters; use the symbol ranges recorded above.
- Blockers: None.
- System-level reason: Later ownership moves need a stable way to distinguish regressions, existing defects, backend differences, and operational fallbacks.
- Contribution to final target: Executable contract evidence now protects behavior while duplicated policy and mixed responsibilities are moved to clearer owners.
- Business meaning with clearer ownership: Tariff-tier primitives, forced-charge transitions, dashboard assembly, and energy-plan serialization each have an identified current owner and comparison surface.
- Local-optimization risks considered: No production cleanup, idealized behavior, weakened assertion, repository-wide test expansion, or backend-specific abstraction was introduced merely to improve local metrics.
- Behavior evidence: 168 targeted passing tests after the change (22 + 60 + 32 + 54), focused 17-test confirmation, compile success, and explicit matrices above.
- Ownership evidence: Existing shared pure/typed boundaries and the remaining three-way `recalc_cost_daily` ownership duplication are named precisely for later parity/extraction work.
- Context reduction achieved: Later phases can begin from six files/symbol ranges and need not reread the four oversized modules.
- Intentionally deferred work: Production deduplication, runner thinning, dashboard repository extraction, energy-model decomposition, live service validation, and unrelated mypy cleanup belong to later phases.
- What the next phase must not undo: Characterization expectations, fail-safe/fallback order, persistence/public field names, unrounded tariff results, or the existing typed boundaries.
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
