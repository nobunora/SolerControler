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
## Vision alignment for this phase

This phase must be interpreted through `VISION_AND_DECISION_PRINCIPLES.md`.

Before performing any step in this phase, answer:

- Which duplicated element represents the same business meaning rather than merely similar text?
- Which boundary should own that meaning after extraction?
- Which call-site differences must remain explicit?
- What contract must be preserved while introducing the shared boundary?
- What evidence will prove reduced ownership duplication rather than only reduced line count?

The extraction target is a business or boundary concept, not a duplication metric.

## Why this phase is necessary

Several later phases depend on small, trustworthy shared concepts such as typed values, exact helper clones, configuration boundaries, and stable conversion rules.

If those concepts remain duplicated:

- Later domain extraction may build on inconsistent primitives.
- Backend implementations may continue to interpret units or fields differently.
- Orchestrators may retain repeated policy fragments.
- Type improvements may be applied separately and drift.
- A larger refactor may accidentally combine unrelated meanings because no narrow shared boundary exists.

This phase creates the smallest reliable building blocks required by the later structural work.

## Phase-specific final target

At the end of this phase:

- Exact semantic clones have one implementation where sharing is justified.
- Important repeated values have explicit types, units, or narrow models.
- Environment and configuration access begins to move behind focused boundaries.
- Shared code expresses one stable meaning and has no backend-specific policy.
- Call sites retain meaningful differences explicitly.
- Later phases can depend on these boundaries without importing giant modules or raw dictionaries.

The target is not a universal utility layer.

The target is a small set of cohesive boundaries that reduce duplicated ownership.

## How this phase contributes to the final architecture

The final architecture requires one clear owner for each business meaning.

This phase contributes by identifying foundational meanings that currently appear in multiple places and assigning each to a narrow owner.

Examples may include:

- Unit conversion
- Time-window representation
- Common result or request models
- Exact helper behavior
- Focused configuration values
- Shared validation rules that are truly domain-invariant

These boundaries make later extraction safer because higher-level modules can coordinate around stable concepts instead of copying low-level assumptions.

## Phase-specific local-optimization risks

Do not optimize this phase by:

- Creating a generic helper only because two code blocks look similar
- Combining functions that have different domain reasons to change
- Building a large `utils` module
- Hiding many unrelated parameters in one context object
- Moving backend-specific mapping into shared domain code
- Introducing inheritance where a narrow function or data model is sufficient
- Replacing explicit units with unlabelled numeric wrappers
- Creating abstractions before tests prove stable shared behavior
- Expanding scope into the larger orchestration or backend phases

Textual duplication is weaker evidence than semantic ownership.

## Required evidence for completion

Behavior evidence must include:

- Focused tests for every extracted shared behavior
- Old-versus-new parity where an existing helper is replaced
- Unit, timezone, nullability, and error behavior where applicable
- Confirmation that backend-specific differences remain preserved

Ownership evidence must include:

- The exact duplicated owners removed
- The new single owner and its responsibility
- Why the shared concept is stable across current call sites
- Evidence that no unrelated policy moved into the shared boundary
- Evidence that call sites now require less duplicated interpretation

## Phase alignment decision

Before marking this phase complete, answer:

1. Does every new shared abstraction represent one domain or boundary meaning?
2. Would the abstraction still make sense if only one current call site remained?
3. Are meaningful backend or workflow differences still visible?
4. Has duplicated decision ownership decreased?
5. Did the change avoid creating a broad dependency hub?
6. Can later phases use the boundary without importing unrelated responsibilities?

If the abstraction exists mainly to reduce line count, reject or narrow it.

## What later phases must not undo

Later phases must not:

- Copy extracted shared behavior back into backend or orchestrator modules
- Expand a focused boundary into a general-purpose utility collection
- Add backend-specific policy to shared domain primitives
- Replace typed values with raw dictionaries for convenience
- Use a shared model as a container for unrelated phase-specific data
