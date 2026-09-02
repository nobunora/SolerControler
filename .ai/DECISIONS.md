# DECISIONS

## Persistent collaboration

- Use this Draft PR as the long-lived Web ChatGPT ↔ Local Codex workspace.
- Do not create a new PR for each iteration.
- Instructions are posted as PR comments after bootstrap.
- Review latest handoff + latest relevant diff; do not restart full-repository analysis every iteration.
- Do not merge until the incident fix, validation, and production-readiness evidence are complete.

## Protected runtime contracts

The following remain unchanged unless a new, directly evidenced defect requires a separate decision:

- 23:00 is one unconditional standby candidate/read-back.
- 03:00 remains standalone; no Firestore/DB lease, ownership, persistence, manual handoff, or cross-slot tail work.
- 07:00 is one unconditional green candidate/read-back.
- 03 monitor cutoff / final standby / hard I/O fences remain 06:45 / 06:50 / 06:55 JST.
- Exact SOC target stopping remains exact; no legacy stop margin is reintroduced.
- Forced mode-only current-snapshot + read-back contract remains unchanged.
- SOC parser/realtime fallback semantics remain unchanged.
- Optimizer, SOC constraints, and target semantics are outside this bug fix unless direct evidence proves they caused plan-preparation failure.

## Plan-preparation remediation decisions

- Do not fix this incident by only increasing the 240-second outer timeout.
- First measure/reproduce the slow phase with production-like inputs.
- Prefer bounding or removing unnecessary optional external I/O over extending the global control budget.
- External-data degradation may use the model's existing fallback semantics when safe; do not silently substitute invented forecast values beyond existing contracts.
- A no-usable-plan failure must stay fail-safe: one standby attempt, no monitor/forced entry.
- Add explicit sanitized prep-failure observability so the next incident identifies exception type/stage without secrets, cookies, full env values, HTML, or resource IDs.
- Do not use normal 03 manual execution for validation.
- Production-like timing disproved weather history as the incident's dominant phase; retain sparse-date and total-budget guards because optional archive I/O must remain bounded.
- Bound the evidenced occupancy Sheets transport at 15 seconds. Measured non-occupancy plan work leaves ample margin, and occupancy data already has an established empty-schedule fallback.
- Bound weather-history optional I/O at 60 seconds. Baseline non-weather work was about 39 seconds, so the worst configured weather budget still leaves roughly 140 seconds inside the unchanged 240-second child limit.

## Deployment decision

If source behavior changes, production deployment is required only after focused tests and the repository's standard quality gate pass. Scheduled 03 acceptance should use the next natural Scheduler execution, not a manual normal 03 run.

## Dedicated forecast owner decision

- Dashboard PV forecasts are owned by a separate non-control Cloud Run job, `solar-forecast-daily`, scheduled daily at 02:30 JST as `solar-forecast-daily-0230`.
- The job may obtain read-only CSV input and run the energy model, but must not import or call control orchestration, settings/device writes, or 03 persistence.
- It writes only forecast-specific stores: immutable `forecast_hourly_snapshots`, mutable `forecast_hourly`, `sunshine_daily`, and `forecast_plans`. It must not write `night_charge_plans/latest`.
- Forecast date and the exact 24-row hourly contract are validated before any mutable replacement. Invalid input leaves existing rows untouched; no historical forecast is fabricated.
- Task timeout is 600 seconds with zero retries. The measured 37.100-second plan generation leaves substantial margin before the independent 03 owner at 03:00 JST.
