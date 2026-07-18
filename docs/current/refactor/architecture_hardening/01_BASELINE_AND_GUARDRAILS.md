# Phase 01: Baseline and Guardrails

## Objective

Create a reliable comparison baseline before changing production structure.

This phase does not refactor production code. It identifies behavior contracts, records targeted test commands, and adds only the characterization tests required by later phases.

## Required reading

Read only:

1. `AGENTS.md`
2. `00_EXECUTION_PROTOCOL.md`
3. This file
4. `PROGRESS.md`
5. Existing tests named in the target sections below

Do not read all production modules. Locate symbols first.

## Scope

Primary targets:

- Operations backend parity
- Forced-charge decisions
- Dashboard backend parity
- Energy-model input and output contracts

Allowed production changes:

- None, except a minimal import seam required to test existing behavior
- Any such seam must preserve behavior and use a separate commit

Allowed test changes:

- Add missing characterization cases
- Add compact shared fixtures
- Add parity assertions
- Record deterministic command groups

## Step 01.1: Record repository baseline

Run:

    git status --short
    git rev-parse HEAD
    python --version

Record the commit and Python version in `PROGRESS.md`.

Do not regenerate repository-wide metrics already listed in `PROGRESS.md`.

## Step 01.2: Confirm targeted test groups

Run each group separately so Bridge timeout does not hide results.

Group A:

    python -m pytest -q tests/test_operations_domain.py tests/test_operations_db.py tests/test_dashboard_backend_parity.py

Group B:

    python -m pytest -q tests/test_forced_charge_state_machine.py tests/test_cloud_job_runner.py

Group C:

    python -m pytest -q tests/test_dashboard_data.py tests/test_firestore_dashboard_metrics.py

Group D:

    python -m pytest -q tests/test_energy_model.py tests/test_energy_model_runtime.py tests/test_soc_cost_optimizer.py

Record exact pass, fail, or timeout results. Do not rerun a timeout automatically.

If a test fails before any source change:

- Capture the failing test name and first relevant traceback.
- Mark Phase 01 blocked.
- Do not modify assertions to force green.

## Step 01.3: Inventory Operations behavior

Locate only these symbols and their direct tests:

    rg -n "recalc_cost_daily|upsert_battery_metrics|update_model_params|update_prediction_hit_rates" app tests

Create a compact behavior matrix in the Phase 01 handoff. Include:

- Inputs
- Returned values
- Persistent writes
- Missing-data behavior
- Zero-data behavior
- Timezone and accounting-day boundary
- Tariff tier boundary
- Rounding point
- Backend-specific behavior

Do not copy implementation bodies into documentation.

If SQLite, PostgreSQL, and Firestore already differ for the same fixture, stop and record a blocker. Phase 03 must not choose one implementation arbitrarily.

## Step 01.4: Add Operations characterization tests

Add tests only for behavior not already fixed by existing tests.

Required scenarios for daily cost calculation:

- Empty day
- Import only
- Export only
- Simultaneous nonzero source fields if currently accepted
- Exact tariff-tier boundary
- One value immediately below and above a tier boundary
- Negative or malformed values according to current behavior
- Missing interval
- Day boundary in the configured timezone
- Existing rounding behavior

Prefer one fixture table over many repetitive test functions.

The test must assert current behavior, even when a follow-up correction may later be desirable.

## Step 01.5: Inventory forced-charge safety behavior

Locate:

    rg -n "_monitor_partial_forced_and_stop|ForcedCharge|cutoff|timeout|retry|target_soc|stop" cloud_job_runner.py app/forced_charge tests/test_forced_charge_state_machine.py tests/test_cloud_job_runner.py

Record:

- States and transitions
- Inputs that trigger start, continue, stop, skip, or failure
- Cutoff behavior
- Timeout behavior
- Missing-SoC behavior
- Read failure behavior
- Command failure behavior
- Retry behavior
- Conditions that must fail safe

Do not call real KP-NET or cloud services.

Add characterization tests only for untested safety branches.

## Step 01.6: Inventory dashboard contracts

Locate:

    rg -n "_load_sqlite_slice|_load_postgres_slice|_load_firestore_slice|latest_schedule|Dashboard" app/dashboard_data.py app/dashboard tests/test_dashboard_data.py tests/test_dashboard_backend_parity.py

Record:

- Public output keys
- Value types
- Ordering guarantees used by tests
- Missing backend data behavior
- Cache or fallback behavior
- Backend-specific source differences

Add a parity fixture only when the same logical source data can be represented by all three backends.

Do not require byte-for-byte JSON ordering unless current callers depend on it.

## Step 01.7: Inventory energy-model contracts

Locate:

    rg -n "def main|AppConfig|_run_soc_optimization|evaluate_soc_candidate|optimize_soc_by_expected_cost|energy_plan" energy_model_main.py app/energy_plan tests/test_energy_model.py tests/test_energy_model_runtime.py tests/test_energy_plan_document.py

Record:

- CLI or callable entrypoints
- Required input files and environment keys
- Output file paths
- Energy-plan field names
- Time and timezone assumptions
- Forecast fallback order
- Optimization input and output shapes

Do not inspect all helper bodies. Phase 06 will inspect one symbol group at a time.

## Step 01.8: Establish quality commands

Record the exact commands that are available in the current environment.

Minimum:

    python -m compileall -q app energy_model_main.py cloud_job_runner.py
    git diff --check

Check whether mypy is installed using the existing command. Do not add a dependency in this phase.

Do not introduce a new formatter or linter unless it already exists in project configuration and can run without rewriting unrelated files.

## Completion gate

Phase 01 is complete only when:

- The baseline commit and Python version are recorded.
- All four targeted test groups have a recorded result.
- Current behavior matrices exist in the handoff for Operations, forced charging, dashboard, and energy model.
- Missing high-risk behavior is covered by characterization tests.
- No production behavior changed.
- `git diff --check` passes.
- Test-only changes are committed separately.
- `PROGRESS.md` is updated with Phase 01 status and the exact next symbols for Phase 02.

## Required handoff to Phase 02

Provide:

- Baseline commit
- Tests added
- Existing exact helper clones found
- Small typed boundaries already present
- Maximum six files or symbol ranges to inspect
- Explicit list of files that Phase 02 should not reread
