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
## Vision alignment for this phase

This phase must be interpreted through `VISION_AND_DECISION_PRINCIPLES.md`.

Before performing any integration or closeout step, answer:

- Do the completed phases form one coherent ownership model?
- Did any phase improve its own area by transferring policy or coupling into another area?
- Which safety, public, persistence, unit, timezone, fallback, and operational contracts must be verified together?
- What pressure to finish could cause temporary structures or unresolved duplication to be accepted as final?
- What evidence will prove that the system is easier and safer to change as a whole?

The objective is not to obtain a green final test run. The objective is to verify that preserved behavior and clearer ownership coexist across the complete system.

## Why this phase is necessary

Each earlier phase is intentionally narrow.

A narrow phase can pass its focused tests while the combined system still contains:

- Policy duplicated across phase boundaries
- New dependency cycles
- Compatibility wrappers that became accidental permanent owners
- Canonical models that conflict across domains
- Entry points that bypass newly established boundaries
- Different fallback behavior between integrated workflows
- Increased total complexity despite cleaner individual files
- Documentation that describes a target no longer reflected in code

Only a final system-level integration review can detect these cross-phase failures.

## Phase-specific final target

At the end of this phase:

- Important business and safety meanings have one identifiable owner.
- Adapters, repositories, orchestrators, domain services, models, and composition roots have distinct responsibilities.
- Public behavior and persistence contracts remain verified.
- Cross-backend and cross-entrypoint parity is demonstrated where required.
- Temporary compatibility structures are either removed or explicitly justified.
- Dependency direction is coherent across the refactored areas.
- Remaining debt and exclusions are recorded precisely.
- A future change can follow the documented ownership model without recreating duplication.
- The architecture-hardening program can be closed with evidence rather than confidence alone.

The target is not architectural perfection.

The target is a stable, explainable system whose remaining limitations are explicit.

## How this phase contributes to the final architecture

The final architecture is a system property. It cannot be proven by adding the results of isolated phases without checking their interaction.

This phase contributes by:

- Verifying that ownership boundaries agree across domains
- Detecting policy that moved rather than disappeared
- Confirming that shared models remain cohesive
- Confirming that orchestrators do not regain decision ownership
- Confirming that adapters do not define domain policy
- Checking that compatibility paths delegate rather than fork behavior
- Measuring whether routine changes require less repository context
- Ensuring that the documentation, tests, and current code describe the same system

## Phase-specific local-optimization risks

Do not optimize this phase by:

- Treating a green full test suite as sufficient closeout evidence
- Removing compatibility code only to make the architecture look complete
- Accepting undocumented exceptions because they affect only one backend
- Ignoring dependency cycles that do not currently fail tests
- Combining final cleanup with unrelated feature work
- Resolving every remaining issue instead of documenting legitimate deferred work
- Weakening tests to eliminate integration failures
- Reporting file-count or line-count reduction as the primary result
- Declaring a phase successful without checking what responsibility moved elsewhere
- Archiving the plan before the current architecture and handoff evidence agree
- Hiding residual static-analysis or test limitations behind a broad completion statement

Completion pressure must not override the decision hierarchy in `VISION_AND_DECISION_PRINCIPLES.md`.

## Required evidence for completion

Behavior evidence must include:

- The documented regression groups and their exact results
- Cross-backend parity where business meaning is shared
- Cross-entrypoint parity where workflows expose the same use case
- Safety and failure-path verification
- Public response, schema, serialization, unit, timezone, and fallback checks
- A clear list of pre-existing or intentionally accepted failures
- Confirmation that no validation scope was silently reduced

Ownership evidence must include:

- A final responsibility map for the refactored areas
- A list of business meanings and their single owners
- Evidence that adapters and orchestrators no longer duplicate those meanings
- A list of compatibility wrappers and the reason each remains
- Evidence that dependency direction matches the intended architecture
- Evidence that routine use cases require less repository context
- A list of residual duplicated ownership, if any, with explicit disposition

Context evidence must include:

- Which files must now be read to change each major use case
- Which former giant modules or backend paths no longer need to be inspected
- Which handoff records make future work possible without reconstructing history
- Which remaining high-context areas were intentionally excluded

## Phase alignment decision

Before marking the full program complete, answer:

1. Does every targeted business or safety meaning have one identifiable owner?
2. Can each external system be recognized as an explicit boundary?
3. Do orchestrators coordinate without independently redefining outcomes?
4. Do all supported backends preserve shared meaning while retaining explicit infrastructure differences?
5. Are public and persistence contracts verified at integrated boundaries?
6. Did any phase create a new generic hub, broad context object, or hidden dependency cycle?
7. Are compatibility paths delegating rather than maintaining separate implementations?
8. Has the context required for routine changes decreased?
9. Are remaining exceptions and debt recorded without overstating completion?
10. Does the implementation still match `VISION_AND_DECISION_PRINCIPLES.md`?

If these answers cannot be supported with concrete evidence, the program is not ready for closeout.

## Conditions that block closeout

Do not close or archive the program when:

- A targeted rule still has multiple active owners.
- One backend or entrypoint bypasses the shared path.
- Safety behavior lacks failure-path evidence.
- Public or persisted contracts changed without an explicit decision.
- A temporary compatibility structure owns new business policy.
- The final documentation describes boundaries not present in code.
- The full validation scope is unknown or materially smaller than the baseline.
- The next maintainer would still need to inspect most of the repository for a routine targeted change.

## What future work must not undo

After closeout, future work must not:

- Add business policy directly to backend adapters or repositories
- Add safety decisions directly to runner loops
- Reintroduce raw storage records into shared domain or presentation code
- Add planning decisions to composition roots
- Create new entrypoints that bypass established owners
- Expand focused models into generic context containers
- Remove parity, characterization, or failure-path tests without replacement evidence
- Treat the archived plan as obsolete when changing the ownership model

Any intentional reversal must include a new architectural decision explaining why the system-level tradeoff has changed.
