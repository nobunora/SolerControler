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
- If no trustworthy forecast evidence exists, leave the original forecast missing. The retained 14-day load estimate is explicitly labeled `legacy_rolling_14d_estimate`, never as a historical prediction.

## Predicted SOC display contract

The 02:30 forecast-only owner is allowed to persist **display metadata** needed to render predicted SOC, but this must not restore control-plan persistence or create device ownership.

Permanent contract:

- `forecast_plans/{date}` may persist `planned_target_soc_percent` and `planned_night_charge_kwh` extracted from the forecast-only plan result;
- these fields are display/read-path evidence only and do not authorize any settings/device write;
- do not write forecast-only metadata to `night_charge_plans/latest` or any control-owner namespace;
- dashboard forecast reads may join `forecast_plans` metadata by the same target date and expose it as `forecast_target_soc_percent` / `forecast_night_charge_kwh` on hourly evidence;
- the dashboard may use that metadata to restore `latest_schedule.planned_target_soc_percent` only when stronger existing control-plan metadata is absent;
- inconsistent forecast-only SOC metadata fails closed instead of choosing a value;
- existing control-plan/applied-setting evidence always wins over forecast-only display metadata;
- the frontend predicted-SOC algorithm remains unchanged: this repair restores its missing finite SOC anchor rather than introducing a new SOC model.

This closes the regression where PR #36 intentionally removed forecast-only control-plan persistence, leaving the dashboard with complete PV/load forecast rows but no finite SOC target and therefore an all-null `予想SOC(%)` series.

## Later reconstruction semantic class

A later model replay is permitted only after original forecast evidence has been searched and found absent. It is a separate semantic class, not an original forecast.

Permanent contract:

- persist later replays only in `forecast_hourly_reconstructed` plus metadata in `forecast_reconstructions`;
- `source = historical_reconstructed_estimate`;
- `is_reconstructed = true`;
- keep `forecast_reconstruction_id`, `forecast_reconstructed_at`, `forecast_reconstruction_model_version`, `forecast_reconstruction_basis`, and sanitized input provenance;
- never write reconstructed rows into `forecast_hourly_snapshots`, `forecast_hourly`, `sunshine_daily`, or `night_charge_plans/latest`;
- never synthesize an original `forecast_run_id` or contemporaneous `issued_at` for a later replay;
- reconstructed PV and load must come from the same complete 24-hour reconstruction run;
- original complete mutable/snapshot evidence always takes precedence over a reconstructed run;
- incomplete reconstructed runs are not shown;
- the dashboard must visibly warn when reconstructed history is being displayed.

Allowed reconstruction bases are deliberately narrow: `historical_archive`, `historical_model_replay`, and `legacy_model_replay`. Execution must use defensible historical inputs and must not use actual PV/load as the forecast target.

## Future self-maintaining history

- The 02:30 non-control forecast owner is the authoritative creator of current-day 24-hour forecast evidence.
- It must keep immutable snapshot evidence so a completed day remains recoverable after mutable current rows change.
- Completed-day actual PV/load remain authoritative only when daily metrics are present or all 48 unique JST half-hour field observations can reconstruct the total.
- The dashboard emits recent-history health warnings when original forecast evidence or complete actual evidence is missing.
- Reconstruction is a repair mechanism for historical gaps, not a replacement for future original forecast persistence.

## Dashboard-only production deployment contract

Dashboard-only rollout is a non-control production operation.

- `DeploymentScope=dashboard` may build/update only the dashboard Cloud Run service.
- It must not deploy or execute 23/03/07, the 02:30 forecast job, Scheduler, KP-NET import, Drive backup, or inverter settings operations.
- The protected settings round-trip is therefore `skipped_not_applicable`, not `skipped_manual`.
- `runner` and `full` deployments continue to require the existing protected 50% settings round-trip, 60-second hold, exact snapshot restoration/read-back, zero Cloud Run retries, and no Scheduler.
- Dashboard-only acceptance is production API/browser evidence plus proof that control resources/device state were not changed.

This is an applicability correction, not a weakening of the device-contract test. Running the settings round-trip solely for a dashboard revision would itself violate the non-control boundary.

## HISTORICAL_FAILURE_LOCK

`tests/test_dashboard_history_contract.py` is a named permanent regression contract. `tests/test_dashboard_forecast_soc_contract.py` and `tests/test_dashboard_only_deployment_contract.py` extend that named operational contract for predicted SOC and dashboard-only deployment.

`HISTORICAL_FAILURE_LOCK (2026-09-04): do not weaken without an explicit migration decision.`

The integration pre-release gate runs the historical contract within the broader validation flow. Any intentional change to these semantics requires an explicit migration decision in this file and corresponding contract-test changes. Do not silently relax the lock as part of unrelated dashboard/control work.

## Repair strategy

- Prefer restoring the dashboard read path from existing immutable/raw evidence before performing persistent production backfills.
- A backfill is allowed only when it copies or deterministically aggregates proven historical evidence, is idempotent, and is separately validated before mutation.
- Keep current-day/future forecast behavior unchanged except for restoring the missing predicted-SOC display metadata contract described above.
- Preserve dashboard pagination and existing chart/UI semantics unless a small provenance indicator is needed to distinguish restored sources.

## Validation

At minimum require tests for:

1. partial `dashboard_daily_metrics` range + older `monitoring_samples` restores both dates;
2. complete daily metric wins over monitoring fallback;
3. incomplete daily metric can be completed from monitoring evidence without dropping fields;
4. immutable snapshot fallback selects exactly one complete eligible forecast run;
5. multiple vintages do not get mixed;
6. no eligible original snapshot never becomes a fabricated original historical value;
7. PV and load daily forecast totals are both restored from the selected run;
8. later reconstructed estimates remain explicitly reconstructed and cannot masquerade as original evidence;
9. original mutable/snapshot forecast takes precedence over reconstruction;
10. incomplete reconstruction is rejected;
11. dashboard API and warnings contain restored historical PV/load values and provenance;
12. current forecast-only owner and protected 23/03/07 tests remain green;
13. forecast-only persistence stores SOC display metadata without creating `night_charge_plans`;
14. forecast metadata restores a finite predicted-SOC anchor but never overrides stronger control-plan evidence;
15. dashboard-only deployment skips settings round-trip only as `skipped_not_applicable`, while runner/full retain the mandatory round-trip.

Run the named historical/SOC/deployment locks, the standard repository quality workflow, and production read-only verification before deployment/merge.
