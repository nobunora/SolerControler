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

## Current implementation status

- New non-control entrypoint: `forecast_job_main.py` / `app.runtime.forecast_job`.
- It only acquires read-only CSV input, runs the energy model for the intended JST date, validates a complete 24-hour forecast, and persists forecast-specific data.
- `app.operations.forecast_persistence.persist_forecast_only_plan` writes immutable snapshots, `sunshine_daily`, `forecast_hourly`, and `forecast_plans`; it never writes `night_charge_plans`.
- Validation happens before any mutable deletion. A target-date mismatch or incomplete row set raises without touching current rows; valid mutable replacement is one Firestore batch.
- Deployment tooling adds `solar-forecast-daily` and `solar-forecast-daily-0230` at 02:30 JST, retries=0, task timeout=600 seconds. Existing 23/03/07 names, schedules, and ownership are unchanged.

## Validation and next action

- Focused forecast/persistence/dashboard/protected suites: 138 passed.
- New-module Ruff and ty: pass; new-module mypy: pass; security check: pass.
- Full Python suite: 603 passed, 1 skipped; JS tests, full mypy (176 files), and security check passed. Shared CodebaseMemory index refreshed: ready, 6,349 nodes / 19,124 edges.
- The non-control local production-like smoke reached the existing SOC optimizer but stopped because the local production environment supplied an invalid `SOC_EXPORT_CONTRACT_STATUS`. No value was overridden, no Firestore write occurred, and no deployment or normal 03 run was performed. Resolve/verify that production configuration before a successful smoke or deployment review.
