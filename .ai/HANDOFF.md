# HANDOFF

## Active workspace

- Persistent Draft PR branch: `codex/persistent-workspace`, Draft PR #36.
- Accepted 03 plan-preparation remediation: `8250353c704ac189c8dd61dd45d2eb48975e04f4`; freeze it unless a direct regression is proven.
- Active task: missing recent PV forecasts in the production dashboard.

## Confirmed dashboard finding

- Read-only production matrix for 2026-08-29 to 2026-09-02 JST is in `.ai/BUG_REPORT.md`.
- 2026-08-29 has a stored immutable plan, 24 mutable rows, two immutable snapshot runs, and browser-rendered forecast data.
- 2026-08-30 through 2026-09-02 have no plan, mutable row, snapshot, or sunshine forecast record. Classify each as `FORECAST_NOT_GENERATED`.
- The Dashboard Firestore reader, slice assembly, and frontend store/render path work for available rows; do not change them for this defect.
- Scheduled 23 jobs succeeded but are protected to be one standby write only. Scheduled 03 is protected to have no Firestore/DB persistence. No permitted scheduled owner currently creates/persists dashboard forecast vintages after the isolation change.

## Relevant files and contracts

- `app/runtime/slot_orchestration.py::_run_night_23`: protected, no plan/CSV/Firestore dependency.
- `app/runtime/cloud_job.py::_ensure_night_plan_available`: 03 local plan only; no persistence.
- `app/operations/domain.py::extract_hourly_forecast_from_plan`
- `app/operations/firestore.py::ingest_sunshine_from_night_plan`
- `app/operations/forecast_snapshot.py::persist_forecast_snapshots`
- `app/dashboard/firestore_repository.py::_firestore_forecast_hourly_between`
- `static/dashboard.js::mergeHourlyRows`

## Current status and next action

- No source change or production mutation was made for this dashboard investigation.
- The mutable-writer empty replacement risk is real code risk but is not the proven cause of these missing dates.
- A repair needs a separately owned non-control forecast generation/persistence pipeline. Clarify whether a new scheduled Cloud Run job/scheduler is authorized, because existing scheduler times and 23/03/07 ownership are frozen.
- Commit and push this concise evidence update to the existing Draft PR, then obtain Web ChatGPT direction before changing runtime or deployment topology.
