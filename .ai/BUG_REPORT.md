# BUG REPORT

## Incident

User-visible symptom: after restoring current PV forecast generation, the dashboard still does not restore the historical forecast-vs-actual state for either PV generation or household consumption.

Current classification: `HISTORICAL_DASHBOARD_SERIES_PARTIALLY_DROPPED`.

## Confirmed code evidence

### A. Historical actuals can be dropped by an all-or-nothing Firestore fallback

`app/dashboard/firestore_repository.py::_firestore_monitoring_daily()` behaves as follows:

1. query `dashboard_daily_metrics` for the requested range;
2. if the result contains any rows, return them immediately;
3. only when the result is completely empty, aggregate `monitoring_samples`.

Therefore a range containing recent `dashboard_daily_metrics` plus older `monitoring_samples` can return only the recent subset. The missing dates then have no `actual_pv_kwh` / `actual_load_kwh` in `energy_daily` even though raw monitoring evidence may exist.

### B. Historical forecasts are not restored from immutable forecast evidence

`app/dashboard/aggregation.py::_build_energy_daily()` currently uses:

- PV forecast: `sunshine_daily.forecast_pv_total_kwh`;
- load forecast: sum of mutable `forecast_hourly.forecast_load_kwh`;
- if mutable load forecast is absent: rolling 14-day fallback.

But `forecast_hourly_snapshots` stores immutable per-run forecast evidence including both `forecast_pv_kwh` and `forecast_load_kwh`. The dashboard historical reader does not currently select an eligible historical snapshot vintage when mutable latest-value rows are absent.

### C. Frontend contract expects these historical values

`static/dashboard.js` builds two forecast-vs-actual chart series from `energy_daily`:

- PV: `forecast_pv_kwh` vs `actual_pv_kwh`;
- consumption: `forecast_load_kwh` vs `actual_load_kwh`.

The original feature commit `da2a4c5c54743a2016e814af937f801547b456f6` introduced exactly these historical charts. The product contract is therefore to retain historical comparison data, not only the current-day forecast.

## Production evidence still required

Do not assume every historical date is recoverable. For representative dates, prove read-only:

- `dashboard_daily_metrics` existence/completeness and actual PV/load values;
- `monitoring_samples` row count and aggregated actual PV/load values;
- mutable `forecast_hourly` row count and sums;
- immutable `forecast_hourly_snapshots` available runs, `issued_at`, row counts, and PV/load sums;
- `sunshine_daily` forecast values;
- final dashboard API `energy_daily` values for the same date.

Classify each sampled date as one of:

- `ACTUAL_COMPLETE_DAILY_METRIC`
- `ACTUAL_RECOVERABLE_FROM_MONITORING_SAMPLES`
- `ACTUAL_EVIDENCE_MISSING`
- `FORECAST_MUTABLE_AVAILABLE`
- `FORECAST_RECOVERABLE_FROM_IMMUTABLE_SNAPSHOT`
- `FORECAST_DAILY_SUMMARY_ONLY`
- `FORECAST_EVIDENCE_MISSING`

## Expected repair boundary

The likely minimum source repair is dashboard-side:

1. merge actuals per date instead of all-or-nothing backend fallback;
2. prefer complete `dashboard_daily_metrics`, filling absent/incomplete dates from `monitoring_samples` without replacing trustworthy complete rows;
3. add deterministic historical forecast selection from one immutable `forecast_hourly_snapshots` run when mutable forecast is unavailable;
4. never mix hours from different forecast runs;
5. derive PV/load daily sums from that selected run when appropriate;
6. keep explicit source/provenance labels so reconstructed dashboard rows are distinguishable from mutable latest rows;
7. do not invent historical forecasts where no trustworthy contemporaneous evidence exists.

Production data backfill is optional and must not be the first fix. Prefer a read-path repair if existing evidence already allows the dashboard to reconstruct the intended historical series.
