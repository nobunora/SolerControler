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
- Configuration provenance (read-only, 2026-09-03): `.env.example`, `scripts/deploy_gcp_jobs.ps1`, and deployed `solar-battery-03` all set `SOC_EXPORT_CONTRACT_STATUS=inactive` and `SOC_EXPORT_VALUE_MODE=neutral`; the local `.env`/post-import process omitted both. Classification: `LOCAL_ENV_DRIFT_ONLY`. Persistent `.env` was not changed.
- Non-control, non-persisting production-like smoke then used only a temporary process overlay of those verified values. `build_energy_plan()` for 2026-09-03 completed in 29.758 seconds; the normalized forecast contained exactly hours 0--23 (24 rows) and 7.114 kWh PV. `EnergyPlanOutput.persist`, forecast persistence, Firestore, battery control, and settings writes were not invoked. The optional occupancy Sheets read logged its established `HttpError` fallback and did not prevent completion.
- No deployment, normal 03 run, or merge has been performed. Next action: obtain Web review of this evidence before any production mutation or PR merge.

## Production rollout evidence (2026-09-03 JST)

- Official runner-scope rollout is complete for `7102bdc`; pre-release gate passed (603 passed, 1 skipped; mypy/security passed), the 07 DryRun met all four Cloud Run terminal/readiness conditions, and the mandatory settings round-trip completed successfully. Existing 23/03/07 schedules were preserved; dashboard, 23/07 job updates, KP-NET import, and Drive backup were skipped.
- One authorized manual `solar-forecast-daily` run completed. It persisted 24 `forecast_hourly` rows for 2026-09-03 (hours 0--23, 7.6845 kWh), 24 immutable snapshot rows, `sunshine_daily`, and `forecast_plans` metadata with `hourly_row_count=24`. The dashboard Firestore slice returned the same 24 rows and PV total.
- The forecast job log recorded only forecast persistence. `night_charge_plans/latest` remains the prior 2026-08-29 plan (updated 2026-08-28T21:31:41Z), confirming the forecast-only execution did not alter it. No normal manual 03 execution was performed.
- Remaining acceptance: await the next natural scheduled 03 execution; then verify bounded prep outcome, provenance/monitor path or one-standby fallback, and unchanged time fences/23/07 ownership. Keep PR #36 Draft and unmerged until then.
