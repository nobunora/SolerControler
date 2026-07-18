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
| 02 Shared boundaries | completed | c203f83 | Existing shared Operations primitives verified across all adapters; no safe new clone extraction required. |
| 03 Operations deduplication | completed | 226dc6a | Daily-cost policy has one pure owner; three adapters retain mapping and persistence only. |
| 04 Forced-charge orchestration | completed | b20048f | Monitor policy is state-owned; clock/device/status effects are injected ports. |
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

### 2026-07-19 Phase 02 handoff

- Date: 2026-07-19
- Phase and step: Phase 02, Steps 02.1-02.8
- Status: completed
- Commit: `c203f83` (test-only ownership verification)
- Intent: Confirm that foundational Operations primitives already have one cohesive owner and avoid creating unnecessary abstractions before Phase 03.
- Changed files: `tests/test_operations_domain.py`; this progress record.
- Preserved contracts: All adapter public functions, environment behavior, time-window inclusivity, tariff precision, persistence formats, and production control flow.
- Tests run: `python -m pytest -q tests/test_operations_domain.py tests/test_operations_db.py` -> 20 passed.
- Static checks: `python -m compileall -q app/operations tests/test_operations_domain.py` -> passed; `git diff --check` -> passed with only the configured LF-to-CRLF warning.
- Behavior differences: None; production code was unchanged.
- Shared helpers added: None. `extract_hourly_forecast_from_plan`, `is_within_window`, `parse_hhmm_to_minute`, and `tiered_increment_cost` already reside in `app.operations.domain`.
- Old copies removed: None; no permitted exact helper clone remained outside later-phase business logic.
- Compatibility wrappers retained: Adapter-private aliases such as `_tiered_day_increment_cost` remain so current module call sites/import behavior do not change.
- Typed models added: None. Existing small models were sufficient; adding another wrapper would not replace a repeated raw boundary in this phase.
- Environment reads moved: None; no touched pure helper reads environment values directly.
- Corrupted message check: `app.main._copy_local_csv` already raises `FileNotFoundError` with the valid text `ローカルCSVが見つかりません`; no repair was necessary and no test parses the message.
- Removed duplication: No new removal. The ownership test now proves all three database adapters reference the same four shared Operations primitives.
- Remaining duplication: `recalc_cost_daily` calculation orchestration remains in `app.operations_db`, `app.postgres_ops`, and `app.firestore_ops`; battery daily metrics, model-parameter mapping, and model hit-rate calculations also remain adapter-owned and require Phase 03 parity analysis.
- Remaining risks: Shared `app.operations.domain` still contains some environment/I/O helpers; moving those was not necessary for the verified primitives and would exceed Phase 02 scope.
- Next agent must read: `03_OPERATIONS_DOMAIN_DEDUPLICATION.md`; this Phase 02 handoff; `tests/test_operations_db.py` cost characterization table; `tests/test_operations_domain.py` ownership test; `recalc_cost_daily` ranges at `app/operations_db.py:546`, `app/postgres_ops.py:445`, and `app/firestore_ops.py:306` as one comparison set.
- Next target symbols: `recalc_cost_daily`; `upsert_battery_daily_metrics`; `upsert_model_parameters_from_plan`; `recalc_model_hit_rates`; shared tariff/window primitives in `app.operations.domain`.
- Existing parity fixtures Phase 03 can reuse: `test_recalc_cost_daily_night8_tiered`, `test_tiered_increment_cost_preserves_boundaries_and_float_precision`, `test_recalc_cost_daily_characterizes_missing_negative_and_day_boundaries`, `test_recalc_cost_daily_empty_input_writes_no_rows`, and `test_all_database_adapters_use_the_shared_plan_domain`.
- Do not reread: Full adapter files, full `energy_model_main.py`, full `cloud_job_runner.py`, or full `app/dashboard_data.py`; locate only the listed Operations symbols.
- Blockers: None.
- System-level reason: Later business-rule consolidation must build on primitives whose semantics and owner are already stable across every adapter.
- Contribution to final target: The shared time/tariff/forecast primitives are now executable ownership contracts, preventing adapters from silently reintroducing copies.
- Business meaning with clearer ownership: Stateless Operations time-window parsing, tier calculation, and plan forecast mapping belong to `app.operations.domain`; persistence remains explicitly adapter-owned.
- Local-optimization risks considered: A new generic helper module, redundant typed wrapper, broad environment migration, and premature `recalc_cost_daily` extraction were rejected.
- Behavior evidence: 20 nearest tests, compilation, and diff validation passed without production changes.
- Ownership evidence: Identity assertions cover SQLite, PostgreSQL, and Firestore for four shared primitives, not only two adapters or output similarity.
- Context reduction achieved: Phase 03 can inspect four named symbol groups and reuse five named fixtures without repeating a clone scan.
- Intentionally deferred work: Operations business calculations and backend mapping belong to Phase 03; forced charge, dashboard, and energy-model boundaries remain in their assigned later phases.
- What the next phase must not undo: Do not copy shared primitives back into adapters or move backend persistence decisions into `app.operations.domain`.

### 2026-07-19 Phase 03 handoff

- Date: 2026-07-19
- Phase and step: Phase 03, Steps 03.1-03.8
- Status: completed
- Commits: `a4f8fc4` pure domain/tests; `213880c` SQLite; `f0635c8` PostgreSQL; `79d876d` Firestore; `226dc6a` duplicate removal.
- Intent: Give daily-cost business meaning one storage-independent owner while preserving each backend's persistence mechanics.
- Changed files: `app/operations/cost_daily.py`, the three Operations adapters, `tests/test_operations_cost_daily.py`, `tests/test_postgres_operations.py`, `tests/test_firestore_operations.py`, and this progress record.
- Preserved contracts: `recalc_cost_daily` signatures/return (`None`), table/collection and six field names, merge/upsert behavior, updated timestamp, transaction/commit boundaries, 450-document Firestore batching, tariff values, day window, monthly tier reset, missing/negative semantics, precision, and unsupported-mode `ValueError`.
- Tests run: final Phase 03 command covering pure domain, all adapters, dashboard parity, Firestore metrics, and pipeline dispatch -> 34 passed. Earlier adapter gates: SQLite 24 passed, PostgreSQL 27 passed, Firestore 29 passed.
- Static checks: `python -m compileall -q app` passed; `python -m mypy app/operations/cost_daily.py` passed with no issues; `git diff --check` passed with only configured LF-to-CRLF warnings.
- Behavior differences: None observed across characterization and planned-persistence fixtures.
- New typed boundaries: immutable `EnergyInterval`, `DailyCostPolicy`, and `DailyCostResult`; pure `calculate_daily_costs`.
- Backend mappers added: Each adapter maps `ts`, `load_kwh`, and `buy_kwh` into `EnergyInterval`. SQLite/PostgreSQL retain SQL cursor/upsert/commit; Firestore retains document-ID fallback, merge writes, and batch commits.
- Compatibility wrappers retained: Public `recalc_cost_daily` functions remain in all three adapter modules and delegate to the pure owner.
- Removed duplication: 476 lines of unreachable/duplicated flat and night8 calculation were removed from the adapters. A tariff change now requires editing one domain module and its tests.
- Backend-specific normalization: Firestore continues to fall back from missing `ts` field to document ID. SQL adapters use stored `ts`. These are mapper concerns and do not branch domain policy.
- Operations environment reads moved: None; existing composition passes tariff parameters into wrappers and the pure model has no environment access.
- Remaining Operations queue: `upsert_battery_daily_metrics`, `upsert_model_parameters_from_plan`, and `recalc_model_hit_rates` remain adapter-owned. They are explicitly deferred as separate rule families because Phase 03 prohibits starting another family before completing and committing daily cost; they require their own cross-backend persistence fixtures before future consolidation.
- Remaining risks: PostgreSQL and Firestore verification uses deterministic fake adapters rather than live services; transaction service behavior was preserved structurally but no production writes were performed.
- Next agent must read: `04_FORCED_CHARGE_ORCHESTRATION.md`; this Phase 03 handoff; `app/forced_charge/state_machine.py`; `cloud_job_runner.py::_monitor_partial_forced_and_stop`; `tests/test_forced_charge_state_machine.py`; relevant monitor/fail-safe range in `tests/test_cloud_job_runner.py`.
- Next target symbols: `ChargePolicy`, `ChargeMonitorProgress`, `decide_transition`, `_monitor_partial_forced_and_stop`, `_attempt_03_fail_safe_standby`, and forced/standby effect execution.
- Do not reread: Operations adapter function bodies, full `cloud_job_runner.py`, full dashboard/energy modules, or completed cost tests unless a regression directly points there.
- Blockers: None.
- System-level reason: Storage selection must not select a different interpretation of tariff, missing data, or accounting day.
- Contribution to final target: Daily-cost policy is pure, typed, deterministic, and independent of SQLite/PostgreSQL/Firestore; adapters now map and persist.
- Business meaning with clearer ownership: `app.operations.cost_daily.calculate_daily_costs` exclusively owns daily self-consumption, flat/tiered savings, and cumulative totals.
- Local-optimization risks considered: No generic repository, schema rewrite, rounding cleanup, backend branch in domain logic, or migration of unrelated Operations families was introduced.
- Behavior evidence: Characterization tables plus SQLite rows, PostgreSQL planned tuples, and Firestore planned documents prove values, fields, precision, and commit behavior.
- Ownership evidence: Three adapter calculation bodies were deleted; identity/shared primitive tests remain; pure model contains no I/O or env access.
- Context reduction achieved: A maintainer can change daily-cost policy by reading one 170-line focused module and its direct test instead of three large adapters.
- Intentionally deferred work: The three named Operations rule families require separate characterization/migration programs; forced-charge work begins next without modifying Operations.
- What the next phase must not undo: Do not bypass `calculate_daily_costs`, add backend tariff branches, or move persistence clients into the domain model.

### 2026-07-19 Phase 04 handoff

- Date: 2026-07-19
- Phase and step: Phase 04, Steps 04.1-04.8
- Status: completed
- Commits: `f94326a` settings boundary; `265fa95` runner settings use; `7576b4e` terminal routing; `ed9803e` injected ports; `d490100` demand decision; `b20048f` runner demand delegation.
- Intent: Make forced-charge monitoring decisions deterministic and reusable while retaining device, clock, sleep, logging, and persistence in an imperative shell.
- Changed files: `app/forced_charge/state_machine.py`, `app/forced_charge/ports.py`, `app/forced_charge/__init__.py`, `app/settings/forced_charge.py`, `cloud_job_runner.py`, focused forced-charge tests, and this progress record.
- Preserved contracts: Start/skip thresholds, target hysteresis, inclusive cutoff/timeout comparisons, retry settings, initial missing-SoC standby, consecutive sensor failure limit, read/start/reapply failure standby attempts, persisted reason strings, profile labels/order, environment keys/defaults/blank behavior, and public one-argument monitor entrypoint.
- State/observation/decision/command types: `ChargeState`, `ChargeObservation`, `ChargePolicy`, `ChargeMonitorProgress`, `ChargeDemand`, `ChargeTransition`, and `ChargeEffect`.
- External ports introduced: `MonitorClock`, `MonitorDevicePort`, and `MonitorStatusPort`. Default runner adapters preserve existing global function behavior; tests/simulations may inject alternatives.
- Compatibility wrappers retained: `_monitor_partial_forced_and_stop(plan_path)` still works without ports; `_should_keep_standby_without_charge` delegates to typed settings and `requires_forced_charge`; existing runner helper names remain.
- Settings moved: cutoff, poll seconds, retry attempts/delay, stop margin, sensor failure limit, reapply policy, completion estimate lead time, and no-charge percent/kWh epsilons now parse in `ForcedChargeSettings` with existing defaults and bounds.
- Exact fail-safe tests: initial missing SoC, consecutive sensor failures, monitor read exception, forced-start failure, reapply failure, standby failure persistence, cutoff/runtime compatibility reason, state stop table, and standby-confirm failure.
- Tests run: final required groups: state/settings 23 passed, cloud runner 42 passed, KP-NET settings intent/workflow 41 passed (106 total). After demand ownership addition, state/settings/runner 69 passed.
- Static checks: `python -m mypy app/forced_charge app/settings/forced_charge.py` -> no issues in 4 files; `python -m compileall -q app cloud_job_runner.py` passed; `git diff --check` passed.
- Behavior differences: None observed.
- Monitor policy ownership: target, cutoff, sensor limit, runtime, continue effects, reapply progress, and no-charge epsilon demand are pure state-boundary decisions. The runner consumes typed transitions and does not directly call clock/SoC/profile/status globals in the monitor range.
- Remaining broad catches and justification: Read/start/reapply boundaries retain broad `Exception` catches because current safety behavior requires a standby attempt for unexpected device/transport failures before re-raising. Narrowing them without concrete transport exception contracts would weaken fail-safe coverage.
- Explicit compatibility distinction: `decide_transition(INITIALIZING)` describes a generic state machine that requests standby when target is already reached, while the production no-charge path records a completed event without issuing a redundant device command. This pre-existing external-action distinction remains explicit in the shell; the shared `ChargeDemand` owns whether charging is needed.
- Remaining risks: Stop-command confirmation is represented in the generic state machine but the existing profile wrapper raises on failed confirmation rather than returning a typed confirmation observation; exception tests prove the preserved fail-safe outcome.
- Next agent must read: `05_DASHBOARD_REPOSITORY_BOUNDARY.md`; this Phase 04 handoff; `app/dashboard/models.py`; `app/dashboard/service.py`; only `_load_sqlite_slice`, `_load_postgres_slice`, `_load_firestore_slice` ranges in `app/dashboard_data.py`; direct dashboard tests.
- Next target symbols: three `_load_*_slice` functions, `DashboardRawData`, `assemble_dashboard_slice`, backend row/document mappers, cache/fallback boundary.
- Do not reread: full `cloud_job_runner.py`, Operations adapters, energy model, or completed forced-charge tests unless a regression points there.
- Blockers: None.
- System-level reason: Safety decisions must not drift between an imperative loop and reusable state logic.
- Contribution to final target: Monitor policy is pure and deterministic; external effects are explicit injectable boundaries; runner coordinates observations, transitions, effects, and sleep.
- Business meaning with clearer ownership: `app.forced_charge.state_machine` owns continue/stop/fail/demand decisions; `cloud_job_runner` owns KP-NET/clock/persistence execution.
- Local-optimization risks considered: No shortened retry order, removed broad fail-safe, combined reason code, generic KP-NET client, or real device validation was introduced.
- Behavior evidence: 106 required-group tests plus exact failure injection and boundary tables; operator-visible persistence reasons and action order remain asserted.
- Ownership evidence: monitor stop branches and demand epsilon left the runner; direct monitor I/O globals were replaced by three narrow ports.
- Context reduction achieved: Forced-charge policy can be understood and tested in the focused state/settings modules without reading the full runner.
- Intentionally deferred work: The explicit generic-initialization versus production no-command compatibility distinction needs a product-approved action contract before unification; dashboard work begins next.
- What the next phase must not undo: Do not add safety decisions to runner branches, bypass injected ports, or read forced-charge env values inside state logic.
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
