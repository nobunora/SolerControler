# Common Execution Protocol

This protocol applies to every architecture-hardening phase.

## 1. Start checks

Run these two commands:

    git status --short
    git log -1 --oneline

Then read only:

1. `AGENTS.md`
2. This protocol
3. The assigned phase file
4. The latest status and handoff in `PROGRESS.md`

When unrelated changes already exist:

- Never reset, checkout, stash, or overwrite them.
- Stop if they overlap the assigned files.
- Record the conflict in `PROGRESS.md`.
- If they do not overlap, continue and mention them in the final report.

## 2. Context limits

Use this order:

1. Locate the target symbol with `rg -n`.
2. Locate direct callers and direct tests.
3. Read only required line ranges.
4. Expand scope only for a concrete unresolved question.

Do not read by default:

- Other phase instructions
- `docs/completed`
- `docs/archive`
- Entire oversized modules
- All tests
- Generated files, caches, artifacts, or logs
- `.env` values

Use the risk map and prior handoff in `PROGRESS.md`. Do not repeat the repository-wide review.

## 3. Change size

Use one reason per commit.

Good units:

- Add characterization tests.
- Add one pure domain calculation.
- Migrate one backend adapter.
- Remove one proven duplicate.
- Extract one orchestration step.

Do not combine:

- Three backend migrations
- Refactoring and feature changes
- Renaming and behavior changes
- Type cleanup across unrelated modules

## 4. Contracts to preserve

Unless explicitly approved, preserve:

- Public function and CLI behavior
- Environment key names and defaults
- Database and Firestore field names
- JSON field names
- Timezone and date boundaries
- Units such as kWh, W, percent, and yen
- Rounding location and method
- `None`, missing, empty, and zero semantics
- Fallback order
- Retry, timeout, cutoff, and fail-safe rules
- Dashboard and energy-plan output shapes

When current behavior appears questionable, freeze it with a test and record a follow-up. Do not silently correct it during structural work.

## 5. Pure core and imperative shell

New shared business logic should be pure.

Pure code must not directly use:

- `os.getenv`
- Filesystem I/O
- Database access
- HTTP access
- Current time
- Sleep
- Global mutable state

Pass external values through typed inputs. Return typed immutable results. Keep serialization and persistence at boundaries.

## 6. External data boundary

Use this flow:

    env / JSON / DB row
        -> parser or mapper
        -> typed model
        -> domain logic
        -> typed result
        -> serializer or repository

Do not introduce new internal `dict[str, Any]` structures. Raw dictionaries are acceptable only at compatibility and I/O boundaries.

## 7. Test order

Use this sequence:

1. Characterization test for current behavior
2. Unit test for extracted pure logic
3. Old-versus-new parity test
4. Nearest adapter or orchestrator tests
5. Phase-level regression tests

Compare relevant return values, planned writes, missing and zero behavior, boundary times, float precision, diagnostics, errors, and fallbacks.

Do not weaken an existing assertion to make a refactor pass.

## 8. Static checks

Minimum checks for changed Python code:

    python -m compileall -q app energy_model_main.py cloud_job_runner.py
    git diff --check

Mypy currently has a known baseline of 92 errors.

- Do not increase errors in the assigned scope.
- New modules should be type-check clean when practical.
- Do not mix unrelated mypy cleanup into the phase.

## 9. Stop conditions

Stop and record a blocker when:

- Money, tariff, or SoC safety behavior is not provable from code and tests.
- Existing backends already produce different results.
- Environment defaults conflict between modules.
- Persistence compatibility is unclear.
- Real external writes would be required for validation.
- Relevant tests already fail before the change.
- Large edits are required in another phase's owned files.

## 10. Handoff

After each step, append a record to `PROGRESS.md` containing:

- Date, phase, and step
- Status and commit
- Intent and changed files
- Preserved contracts
- Tests and static checks
- Behavior differences
- New typed boundaries
- Removed duplication
- Remaining risks
- Next files and target symbols
- Files or ranges that should not be reread
- Blockers

Limit the next agent's required reading to six files or symbol ranges. For large modules, provide symbol names and search commands instead of requesting a full-file read.
