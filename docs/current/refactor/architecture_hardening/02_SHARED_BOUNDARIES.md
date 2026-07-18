# Phase 02: Shared Boundaries

## Objective

Remove only proven exact duplication and create small typed boundaries that later phases can reuse.

This phase is intentionally conservative. It must not redesign Operations, forced charging, dashboard assembly, or the energy model.

## Prerequisites

Phase 01 must be completed.

Read only:

1. `AGENTS.md`
2. `00_EXECUTION_PROTOCOL.md`
3. This file
4. The latest Phase 01 handoff in `PROGRESS.md`
5. Files and symbols explicitly named by that handoff

Do not repeat the repository-wide clone search unless the Phase 01 handoff is missing.

## Scope

Allowed work:

- Exact helper clones with identical behavior
- Small immutable value objects
- Parsers and serializers at existing I/O boundaries
- Narrow Protocol definitions justified by at least two existing consumers
- The corrupted `FileNotFoundError` message in `app/main.py`, if the change is text-only
- Local type improvements required by extracted helpers

Not allowed:

- Operations business-rule consolidation
- Forced-charge state-machine redesign
- Dashboard backend migration
- Energy-model algorithm changes
- Repository-wide environment-variable migration
- General naming cleanup
- Mass formatting
- Broad exception-policy changes

## Step 02.1: Confirm candidate clones

Use the Phase 01 handoff first.

When verification is necessary, search exact candidate names and short bodies:

    rg -n "def |datetime|timezone|csv|json|clamp|round|normalize" app energy_model_main.py cloud_job_runner.py

Do not print entire files.

A candidate may be extracted only when all are true:

- Inputs have the same meaning.
- Outputs have the same meaning.
- Missing and error behavior match.
- Timezone behavior matches.
- Rounding behavior matches.
- Side effects are absent or identical.
- Existing callers can retain their public behavior.

Similar-looking code with different domain meaning must remain separate.

## Step 02.2: Add parity tests before extraction

For each selected helper:

1. Create a compact table of representative inputs.
2. Call each existing implementation.
3. Assert equal output or equal exception behavior.
4. Include boundary and missing-value cases.

Commit the parity test before moving implementation code when the helper affects dates, money, SoC, or persisted output.

For trivial formatting-only helpers, test and extraction may share one commit if the diff remains small.

## Step 02.3: Choose the destination module

Use an existing focused module when possible.

Preferred destinations:

- General stateless primitives: `app/utils.py`, only if already appropriate
- Operations concepts: defer to Phase 03
- Forced-charge concepts: defer to Phase 04
- Dashboard concepts: defer to Phase 05
- Energy-plan document concepts: `app/energy_plan`
- Settings parsing: the nearest existing `app/settings` module

Do not create a generic `helpers.py`, `common.py`, `manager.py`, or `service.py` solely to hold unrelated functions.

Create a new module only when its name describes one cohesive concept.

## Step 02.4: Extract one helper at a time

For each helper:

1. Add the new pure implementation.
2. Add direct unit tests.
3. Migrate one caller.
4. Run that caller's nearest tests.
5. Migrate the next caller.
6. Run parity tests again.
7. Remove old copies only after every caller uses the shared helper.

Do not migrate more than one conceptual helper per commit.

Keep compatibility wrappers temporarily when external imports may exist.

## Step 02.5: Establish small typed boundaries

Add typed models only where they replace repeated raw structures.

A new model must:

- Represent one domain concept
- Be immutable when practical
- Contain no I/O
- Avoid optional fields that merely combine unrelated use cases
- Have an explicit parser or mapper at the boundary
- Preserve existing serialized field names

Good examples:

- One timestamped energy sample
- One tariff calculation input
- One forced-charge observation
- One dashboard schedule event
- One settings group used by a single bounded context

Bad examples:

- A replacement global `AppConfig`
- A universal execution context
- A dictionary wrapper with no semantic validation
- A model containing database clients, loggers, and business data together

Do not force existing callers to adopt a model when only one call site uses the structure.

## Step 02.6: Settings boundary rule

When a touched helper reads environment variables directly:

1. Record the current key, default, blank-string behavior, and conversion error.
2. Add a focused settings parser near the owning bounded context.
3. Parse once at the entrypoint or composition boundary.
4. Pass the typed setting to pure logic.
5. Preserve the old public entrypoint.

Do not search and migrate unrelated environment access.

Do not read or print secret values.

## Step 02.7: Repair the corrupted message

Inspect only the relevant `FileNotFoundError` line in `app/main.py`.

The repair is allowed only when:

- It changes message text, not exception type or control flow.
- No caller or test parses the corrupted text as a protocol.
- The corrected text is clear and does not expose secrets or paths beyond current behavior.

Use a separate commit from helper extraction.

If message compatibility is uncertain, leave it unchanged and record a follow-up.

## Step 02.8: Required checks

After each extraction:

    python -m pytest -q <nearest test files>
    python -m compileall -q <changed Python files>
    git diff --check

For changed or new focused modules, run targeted mypy when available:

    python -m mypy <changed modules>

A known repository-wide mypy baseline of 92 errors is not a reason to ignore new local errors.

## Stop conditions

Stop and record a blocker when:

- Candidate implementations differ on any parity fixture.
- The helper name would erase distinct domain meaning.
- Extraction requires coordinated changes in Phase 03 through Phase 06 files.
- Public imports cannot be preserved safely.
- A typed model would need many unrelated optional fields.
- Environment defaults conflict between callers.
- A helper contains hidden I/O or current-time access that cannot be injected without redesign.

Do not expand Phase 02 to solve the discovered architectural issue.

## Completion gate

Phase 02 is complete only when:

- Every removed duplicate has a parity test.
- Each shared helper has one cohesive destination.
- Callers preserve their public behavior.
- No Operations, forced-charge, dashboard, or energy algorithm changed.
- New focused modules pass targeted type checking when available.
- Relevant tests and `git diff --check` pass.
- Each conceptual extraction is committed separately.
- `PROGRESS.md` records exact remaining duplication and Phase 03 target symbols.

## Required handoff to Phase 03

Provide:

- Shared helpers added
- Old copies removed
- Compatibility wrappers retained
- Typed models added
- Environment reads moved
- Exact Operations symbols to inspect
- Existing parity fixtures Phase 03 can reuse
- Files and ranges Phase 03 must not reread
