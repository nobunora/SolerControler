# BUG REPORT

## Incident

Date range: 2026-08-29 through 2026-09-02 JST

User-visible symptom: recent PV forecasts are missing from the production dashboard.

Primary investigation classification: `FORECAST_NOT_GENERATED` for 2026-08-30 through 2026-09-02.

## Read-only production evidence

| JST date | Stored plan / extracted hourly rows | Mutable `forecast_hourly` | Immutable snapshots | `sunshine_daily` | Dashboard slice / browser |
| --- | --- | --- | --- | --- | --- |
| 2026-08-29 | exists; 24 rows; 5.2862 kWh | 24 rows; 5.2862 kWh | 2 runs; 48 rows; latest issued 2026-08-28T21:31:39Z | exists; 5.2862 kWh | received and rendered as the latest hourly plan |
| 2026-08-30 | absent | 0 | 0 | absent | no forecast rows available |
| 2026-08-31 | absent | 0 | 0 | absent | no forecast rows available |
| 2026-09-01 | absent | 0 | 0 | absent | no forecast rows available |
| 2026-09-02 | absent | 0 | 0 | absent | no forecast rows available |

- The 2026-08-29 plan is an archived immutable GCS detail with a matching `forecast.date`, 24 extracted hourly rows, and the same PV sum as mutable storage. This rules out the mutable-delete-without-replacement risk for that date.
- The production browser displayed the 2026-08-29 hourly plan and its 5.29 kWh PV total. The frontend receives and renders available rows; it is not the cause of the later missing dates.
- Scheduled 23 Cloud Run executions from 2026-08-29 through 2026-09-02 all completed successfully. Firestore `pipeline_runs` after the 2026-08-29 plan contains only `manual-csv` entries, whose canonical wrapper sets `DATA_PIPELINE_INCLUDE_NIGHT_PLAN=false`; it contains no later 23 forecast-ingestion run.

## Confirmed source trace and root cause

`night_charge_plan.json` -> `extract_hourly_forecast_from_plan()` -> `ingest_sunshine_from_night_plan()` -> mutable `forecast_hourly` -> `persist_forecast_snapshots()` -> `_firestore_forecast_hourly_between()` -> dashboard slice -> `static/dashboard.js` is intact for the 2026-08-29 evidence.

The current failure is upstream of this trace. `app/runtime/slot_orchestration.py::_run_night_23` is protected to perform exactly one standby write and explicitly forbids plan/CSV/Firestore dependencies. `app/runtime/cloud_job.py::_ensure_night_plan_available` keeps 03 plan generation local and `tests/test_cloud_job_runner.py` forbids plan persistence there. Consequently, after the 2026-08-29 isolation change, no permitted scheduled owner generates and persists new dashboard forecast vintages. Do not alter the accepted 03 remediation or add persistence to 03.

## Remaining decision

The evidenced repair requires a separate non-control forecast generation and persistence owner. Adding a new scheduled Cloud Run job or scheduler is an operational architecture change, while the task also freezes existing Scheduler times and 23/03/07 ownership. No production data has been changed and no historical forecast will be fabricated pending that decision.

## Implementation status

The authorized dedicated forecast-only owner is implemented on the Draft branch. Local and focused validation passed, including a 24-hour replacement contract that leaves existing mutable rows intact on invalid input. A non-control production-like smoke did not reach plan output because the local production environment's `SOC_EXPORT_CONTRACT_STATUS` was invalid for the existing optimizer; no configuration was guessed or changed. This is a deployment-review blocker, not evidence of a dashboard or 03-control regression.
