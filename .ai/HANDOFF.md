# HANDOFF

## Active workspace

- Persistent Draft branch: `codex/dashboard-history-restore`.
- Base: merged master `19aae9c9291ad830788556a5c59c03199e463174` (PR #36 complete).
- Active task: restore historical dashboard forecast-vs-actual state for both PV generation and household consumption.

## User-visible defect

The current dashboard has the recent forecast owner restored, but historical forecast/actual series for PV generation and consumption are still missing or incomplete.

## Confirmed source findings

1. `static/dashboard.js` renders the PV and load forecast-vs-actual charts from `energy_daily` (`forecast_pv_kwh` vs `actual_pv_kwh`, and `forecast_load_kwh` vs `actual_load_kwh`).
2. `app/dashboard/firestore_repository.py::_firestore_monitoring_daily()` first reads `dashboard_daily_metrics`; if that query returns even one row, it immediately returns those rows and never fills missing dates from `monitoring_samples`. This is an all-or-nothing fallback and can erase older actual PV/load days from the dashboard when `dashboard_daily_metrics` is only partially backfilled.
3. `app/dashboard/aggregation.py::_build_energy_daily()` gets historical PV forecast from `sunshine_daily` and load forecast from mutable `forecast_hourly`; absent hourly load forecast falls back to a rolling 14-day estimate.
4. `forecast_hourly_snapshots` is immutable forecast evidence and includes both `forecast_pv_kwh` and `forecast_load_kwh` plus `forecast_run_id`, `issued_at`, `date`, and `hour`. The current dashboard historical path does not use it to restore missing forecast vintages.
5. Original chart commit `da2a4c5c54743a2016e814af937f801547b456f6` explicitly introduced historical forecast-vs-actual PV/load charts using monitoring actuals. Preserve that product behavior.

## Primary files to inspect first

- `app/dashboard/firestore_repository.py`
- `app/dashboard/aggregation.py`
- `app/dashboard/service.py`
- `app/dashboard/slice_assembler.py`
- `static/dashboard.js`
- `app/operations/forecast_snapshot.py`
- `tests/test_dashboard_data.py`
- forecast snapshot/persistence tests

Expand only when production evidence requires it.

## Required next action

Before changing production data, build a read-only production matrix over the longest practical historical range currently intended by the dashboard. At minimum sample:
- a recent complete day;
- dates where `dashboard_daily_metrics` exists;
- older dates where it is absent but `monitoring_samples` exists;
- dates with mutable `forecast_hourly`;
- dates with immutable `forecast_hourly_snapshots` but no mutable forecast;
- dates with neither trustworthy forecast source.

Then implement the smallest repair that restores historical chart rows without fabricating old forecasts. See `.ai/BUG_REPORT.md` and `.ai/DECISIONS.md`.

## Read-only matrix and implementation in progress

- Firestore evidence: complete daily metrics and monitoring aggregates agree on representative dates; mutable hourly forecasts exist for 2026-05-24; snapshots exist only for 2026-08-28, 2026-08-29, and 2026-09-03, all as complete 24-row runs. No current production date has snapshots without mutable rows, so no persistent backfill is justified.
- Dashboard read-path patch: complete `dashboard_daily_metrics` remain authoritative; missing/incomplete days are filled from same-day monitoring aggregates. When mutable hourly forecasts are absent, one complete immutable snapshot run issued no later than 03:30 JST is selected; later vintages are excluded and PV/load are restored together.
- Focused validation: `python -m ruff check app/dashboard/firestore_repository.py app/dashboard/aggregation.py tests/test_dashboard_data.py` passed; `python -m pytest tests/test_dashboard_data.py -q` passed (48).
