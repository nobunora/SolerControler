# Codex Task Instructions

## Repository

`C:\VSC\SolerControler`

## Objective

Implement only the two immediate correctness fixes described in:

- `01_HOURLY_LOAD_AGGREGATION.md`
- `02_OVERNIGHT_DISCHARGE_GUARD.md`

Do not implement the weather and temperature redesign documents unless explicitly requested.

## Required workflow

1. Read `00_INDEX.md`.
2. Inspect the current source before editing.
3. Check `git status`.
4. Preserve unrelated local changes.
5. Add or update focused tests.
6. Run the smallest relevant test set first.
7. Run broader regression tests only after focused tests pass.
8. Report changed files and test results.

## Required implementation

### Hourly aggregation

- Aggregate 30-minute kWh values by date and hour.
- Average completed hourly totals across days.
- Use corrected values in overnight hourly forecasts.
- Preserve existing daily aggregation.
- Preserve daytime total normalization.

### Overnight cap

- Interpret a non-positive cap as disabled.
- Change documented and deployment configuration from 2.0 to 0.
- Add cap diagnostics.
- Preserve percentile estimation behavior.

## Safety constraints

- Do not deploy.
- Do not modify cloud resources.
- Do not update production environment variables directly.
- Do not ingest or overwrite Firestore data.
- Do not change battery operating settings.
- Do not remove existing fallback behavior.
- Do not expose secrets.

## Completion report

Include:

- Confirmed root cause
- Files changed
- Tests added or changed
- Test commands and outcomes
- Before-and-after numerical examples
- Remaining risks
