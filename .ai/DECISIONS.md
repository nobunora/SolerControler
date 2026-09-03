# DECISIONS

## Persistent collaboration

- Use one Draft PR for this dashboard-history restoration workstream.
- New instructions go into PR comments; do not open a new PR per iteration.
- Review latest handoff + latest relevant diff; avoid full-repository rereads unless evidence requires them.
- Do not merge until historical actual and forecast restoration is validated against production read-only evidence.

## Protected production contracts

The accepted PR #36 production architecture remains frozen unless direct regression evidence requires otherwise:

- 23:00 standby-only owner;
- 03:00 standalone control owner;
- 07:00 green owner;
- 06:45 / 06:50 / 06:55 fences;
- exact-target SOC stop;
- forced mode-only current-snapshot/read-back contract;
- dedicated non-control forecast owner `solar-forecast-daily` at 02:30 JST;
- no forecast-only write to `night_charge_plans/latest`.

This task is a dashboard/history-data repair. Do not change battery control or optimizer semantics.

## Historical actual-data decision

- Actual PV/load are facts and may be reconstructed only from trustworthy measured sources.
- Prefer a complete `dashboard_daily_metrics` row for a day.
- If the daily row is absent or incomplete, fill only its missing actual fields from `monitoring_samples` when that field has a complete 48-sample day.
- The fallback is per-day/per-field, not all-or-nothing for the whole requested range.
- Do not overwrite a complete authoritative daily metric with a weaker reconstruction.
- If neither source has sufficient evidence, leave the actual value missing and expose the missing-data condition rather than inventing zero.

## Historical forecast-data decision

- Do not fabricate past forecasts from today's weather, actual PV/load, or hindsight data.
- Mutable `forecast_hourly` may be used when it is the stored forecast for the target date.
- When mutable forecast rows are absent, immutable `forecast_hourly_snapshots` may restore the historical forecast only by selecting one complete, deterministic eligible `forecast_run_id` for that target date.
- Never combine hours from different forecast runs to make an artificial 24-hour day.
- Historical snapshots are eligible only when issued no later than 07:00 JST on their target date. Legacy production evidence confirms a 06:31-JST issuance; the legacy producer has no evidenced late-day contract, so later target-day runs remain ineligible to avoid hindsight. Select the latest eligible complete run deterministically.
- Daily PV/load forecast totals may be derived from the selected immutable run and must carry a source/provenance label.
- If only `sunshine_daily` contains trustworthy contemporaneous PV forecast evidence, preserve it as a lower-resolution source with an explicit source label.
- If no trustworthy forecast evidence exists, leave the forecast missing. The retained 14-day load estimate is explicitly labeled `legacy_rolling_14d_estimate`, never as a historical prediction.

## Repair strategy

- Prefer restoring the dashboard read path from existing immutable/raw evidence before performing persistent production backfills.
- A backfill is allowed only when it copies or deterministically aggregates proven historical evidence, is idempotent, and is separately validated before mutation.
- Keep current-day/future forecast behavior unchanged.
- Preserve dashboard pagination and existing chart/UI semantics unless a small provenance indicator is needed to distinguish restored sources.

## Validation

At minimum require tests for:

1. partial `dashboard_daily_metrics` range + older `monitoring_samples` restores both dates;
2. complete daily metric wins over monitoring fallback;
3. incomplete daily metric can be completed from monitoring evidence without dropping fields;
4. immutable snapshot fallback selects exactly one complete eligible forecast run;
5. multiple vintages do not get mixed;
6. no eligible snapshot leaves forecast missing rather than inventing a historical value;
7. PV and load daily forecast totals are both restored from the selected run;
8. dashboard API and frontend chart inputs contain restored historical PV/load forecast and actual values;
9. current forecast-only owner and protected 23/03/07 tests remain green.

Run the standard repository quality workflow before deployment.
