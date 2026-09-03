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

## Read-only production evidence matrix

The following is sanitized, read-only evidence. The patched local branch read the production Firestore backend on 2026-09-03; no document writes, deployment, or job execution occurred.

| JST date | Daily/monitoring actual | Mutable hourly / snapshot evidence | Sunshine PV | Patched `energy_daily` result | Classification |
| --- | --- | --- | --- | --- | --- |
| 2026-05-24 | complete daily metric and raw aggregate agree (representative pre-backfill check) | 24 mutable rows | present when recorded | stored mutable PV/load pair | `ACTUAL_COMPLETE_DAILY_METRIC`, `FORECAST_MUTABLE_AVAILABLE` |
| 2026-08-28 | actual PV 11.409, load 39.229 | mutable 24 rows; snapshot run: issued 2026-08-27T18:01:36Z, 24 rows, PV 9.3266, load 36.8573 | 9.3266 | PV 9.3266 / load 36.8573, actuals present | complete actual + mutable forecast |
| 2026-08-29 | actual PV 2.259, load 40.216 | mutable 24 rows; two complete snapshots, latest issued 2026-08-28T21:31:39Z (06:31 JST), PV 5.2862, load 31.3648 | 5.2862 | PV 5.2862 / load 31.3648, actuals present | complete actual + mutable forecast; snapshot fallback unit-tested because mutable exists |
| 2026-08-30 | actual PV 8.574, load 30.736 | no mutable or immutable row | absent | PV missing; load 39.731 labeled legacy estimate | `FORECAST_EVIDENCE_MISSING` / explicit legacy load |
| 2026-08-31 | actual PV 5.991, load 32.545 | no mutable or immutable row | absent | PV missing; load 39.343 labeled legacy estimate | `FORECAST_EVIDENCE_MISSING` / explicit legacy load |
| 2026-09-01 | actual PV 8.311, load 35.772 | no mutable or immutable row | absent | PV missing; load 39.100 labeled legacy estimate | `FORECAST_EVIDENCE_MISSING` / explicit legacy load |
| 2026-09-02 | no dashboard `energy_daily` source row | no mutable, immutable, or sunshine evidence | absent | no row (no values invented) | `ACTUAL_EVIDENCE_MISSING`, `FORECAST_EVIDENCE_MISSING` |
| 2026-09-03 | incomplete/current-day actual intentionally missing | mutable 24-row forecast; snapshot run issued 2026-09-02T16:33:01Z, 24 rows, PV 7.6845, load 43.2533 | 7.6845 | PV 7.6845 / load 43.2533; actuals missing | current mutable forecast; actual not promoted |

The raw-monitoring recovery branch has no production snapshot-only counterpart and must remain read-only: deterministic unit coverage proves a missing/incomplete daily metric is reconstructed only from 48 unique JST half-hour slots per requested field. A complete daily metric triggers zero raw reads; non-contiguous recovery dates are queried as tight contiguous ranges only.

### Forecast-vintage eligibility

Before the dedicated 02:30 owner, commit `2c45574` persisted snapshots via the `db_pipeline` night-plan ingestion path. The dedicated `forecast_job.py` owner was introduced only in `894e9f3`; the current 02:30 schedule is `scripts/deploy_gcp_jobs.ps1`. The old path has confirmed 06:31 JST issuance but no evidence that late-day target-date reruns were valid forecasts. Therefore the selector accepts a complete snapshot only through 07:00 JST on its target date: this includes the confirmed legacy 06:31 run, rejects later ambiguous/hindsight vintages, and is deliberately narrower than target-date end.

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
