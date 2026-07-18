# Phase 07: Final Integration and Closeout

## Objective

Verify that all completed refactors preserve behavior, reduce duplicated ownership, and leave the repository in a maintainable state.

This phase integrates and validates existing work. It must not become another broad refactor phase.

## Prerequisites

Phase 06 must be completed.

Read only:

1. `AGENTS.md`
2. `00_EXECUTION_PROTOCOL.md`
3. This file
4. All handoff records in `PROGRESS.md`
5. Files and test groups explicitly named by those handoffs

Do not reread every implementation file.

## Scope

Allowed work:

- Fix integration defects introduced by Phases 01 through 06
- Remove temporary comparison code
- Remove proven dead compatibility code
- Consolidate test commands
- Update architecture documentation
- Record remaining debt
- Perform final static and regression checks

Not allowed:

- New features
- Algorithm tuning
- Schema changes
- New architecture initiatives
- Repository-wide style cleanup
- Unrelated mypy cleanup
- Large renaming
- Dependency upgrades

## Step 07.1: Verify phase completion records

For each phase, confirm that `PROGRESS.md` records:

- Status
- Commits
- Changed files
- Preserved contracts
- Tests run
- Static checks
- Behavior differences
- Remaining risks
- Blockers

A phase marked completed without evidence must be changed back to `in progress` or `blocked`.

Do not infer success from missing notes.

## Step 07.2: Review ownership boundaries

Confirm single ownership for:

- Operations business calculations
- Forced-charge transition policy
- Dashboard shared assembly
- Energy-model forecast calculations
- Optimization orchestration
- Energy-plan serialization
- Settings parsing moved during each phase

Use targeted searches for known old symbols and duplicate bodies.

Do not rerun a broad clone scan unless handoffs identify unresolved duplication.

## Step 07.3: Remove temporary migration code

Search for temporary artifacts named in handoffs, including:

- Old-versus-new comparison branches
- Debug output
- Temporary feature flags
- Transitional duplicate calculations
- Unused adapters
- Unused compatibility wrappers
- Commented-out old implementations

Remove an item only when:

- No caller imports it
- No test relies on it
- Public compatibility is not required
- Relevant tests pass after removal

Use one cleanup reason per commit.

## Step 07.4: Run regression groups

Run tests in groups small enough for the Bridge wait window.

Group A, Operations:

    python -m pytest -q tests/test_operations_domain.py tests/test_operations_db.py tests/test_dashboard_backend_parity.py

Group B, forced charging and KP-NET:

    python -m pytest -q tests/test_forced_charge_state_machine.py tests/test_cloud_job_runner.py
    python -m pytest -q tests/test_kpnet_settings_intent.py tests/test_kpnet_workflow.py

Group C, dashboard:

    python -m pytest -q tests/test_dashboard_data.py tests/test_firestore_dashboard_metrics.py
    python -m pytest -q tests/test_dashboard_backend_parity.py tests/test_dashboard_server.py

Group D, energy model:

    python -m pytest -q tests/test_energy_model.py
    python -m pytest -q tests/test_energy_model_runtime.py
    python -m pytest -q tests/test_soc_cost_optimizer.py tests/test_energy_plan_document.py

Group E, domain primitives and nearby regression tests:

    python -m pytest -q tests/test_domain_primitives.py

If a listed file does not exist, verify the current test filename with `Get-ChildItem tests` or `rg --files tests`. Record the correction in `PROGRESS.md`.

Do not silently omit a relevant test group.

## Step 07.5: Run broader non-external tests

After targeted groups pass, run the repository's established non-external test command when one exists.

If no canonical command exists, construct several bounded groups rather than one command likely to exceed the Bridge window.

Record each result as:

- Passed
- Failed
- Timed out
- Not run, with reason

A timeout is not a pass or a failure.

Do not run tests that require production credentials, real device control, or external writes unless the project already provides a safe isolated mode.

## Step 07.6: Static validation

Run:

    python -m compileall -q app energy_model_main.py cloud_job_runner.py db_pipeline_main.py dashboard_server.py
    git diff --check
    git status --short

Run targeted mypy for changed packages.

Then run the established repository-wide mypy command:

    python -m mypy app energy_model_main.py cloud_job_runner.py db_pipeline_main.py dashboard_server.py

Compare against the original baseline:

    92 errors in 10 files
    51 source files checked

The final result may still contain pre-existing errors, but:

- Changed scope must not introduce new errors.
- New focused modules should be clean where practical.
- Any increased count must be explained and fixed before completion.

Do not suppress errors broadly to improve the count.

## Step 07.7: Verify public contracts

Use tests and focused inspection to confirm:

- Environment key names remain compatible
- Defaults remain compatible
- Database and Firestore schemas are unchanged
- JSON field names are unchanged
- Timezone and accounting-day behavior is unchanged
- Units and rounding are unchanged
- Forced-charge safety behavior is unchanged
- Dashboard output shape is unchanged
- Energy-plan output shape is unchanged
- CLI and callable entrypoints remain available

When a deliberate contract change was approved during a phase, record it explicitly with migration notes.

## Step 07.8: Check architectural outcomes

Confirm with evidence:

- Operations business rules are not duplicated by backend.
- Forced-charge policy is not duplicated in the runner loop.
- Dashboard loaders do not own shared presentation policy.
- `energy_model_main.py` is primarily a composition root.
- External I/O is separated from pure calculations.
- New raw `dict[str, Any]` usage was not introduced in domain code.
- Environment reads moved only within approved phase scope.
- Compatibility wrappers delegate rather than duplicate behavior.
- Broad catches remaining in changed scope have documented justification.

Do not use line count alone as proof of improvement.

## Step 07.9: Update documentation

Update:

- `PROGRESS.md`
- This directory's `README.md`
- Relevant `docs/README.md` navigation entry
- Existing design documentation only when ownership materially changed

Documentation must state:

- What moved
- Which contracts were preserved
- How to run relevant tests
- Remaining debt
- Compatibility wrappers still present
- Known blockers

Do not copy large source excerpts into documentation.

## Step 07.10: Commit and repository state

Before the final commit:

    git diff --check
    git status --short
    git diff --stat

Review only the changed documentation or source ranges.

Use focused commit messages describing one result.

Do not include unrelated pre-existing changes.

After the final commit:

    git status --short
    git log --oneline -10

The worktree should be clean unless unrelated changes were already present and documented.

## Completion gate

The architecture-hardening plan is complete only when:

- Every phase has an evidence-backed status.
- Targeted regression groups pass.
- Broader non-external test results are recorded.
- Compile checks pass.
- `git diff --check` passes.
- Changed scope adds no unexplained mypy regressions.
- Public contracts are verified.
- Temporary migration code is removed.
- Ownership boundaries are documented.
- Remaining debt is explicit and prioritized.
- Final commits are focused.
- Repository state is recorded.

## Final closeout record

Append a final record to `PROGRESS.md` containing:

- Final commit range
- Test results by group
- Static-check results
- Initial and final mypy counts
- Duplicated rules removed
- New boundaries introduced
- Compatibility wrappers retained
- Contract changes, if any
- Remaining high, medium, and low risks
- Recommended next maintenance task
- Worktree status

## Archive rule

Do not move this directory to `docs/completed` until the implementation phases are actually complete.

When all phases are complete:

1. Confirm all links remain valid.
2. Preserve `PROGRESS.md`.
3. Move the directory as one Git operation.
4. Update `docs/README.md`.
5. Commit the archive move separately.

Documentation creation alone does not satisfy implementation completion.
