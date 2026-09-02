# BUG REPORT

## Incident

Date: 2026-09-02 JST

User-visible symptom: morning battery SOC was 0% again.

Primary investigation classification: `PLAN_GENERATION_OR_PREP_FAILURE`.

## Confirmed evidence

- 03 Scheduler was enabled and created the scheduled execution at 03:00 JST.
- Cloud Run execution succeeded at the platform level, but application control never reached the monitor phase.
- Initial CSV completed.
- `energy_model_main.py` started with `FORECAST_DATE_OVERRIDE=2026-09-02`.
- Marker counts in the execution:
  - `03-plan-provenance`: 0
  - `03-monitor contract`: 0
  - `03-monitor soc`: 0
  - `03-monitor stop reason`: 0
- About 213 seconds after model start, transport timeout warnings appeared; about 242 seconds after start, KP-NET login consistent with fail-safe standby began.
- Official monitoring CSV shows 03:00 through 06:30 SOC=1% and charge=0.000 kWh; 07:00 SOC=0%.
- No forced-mode write/readback failure is proven because forced control was never reached.
- 23 and 07 jobs succeeded and do not explain the missing overnight charge.

## Confirmed code boundary

`app/runtime/cloud_job.py::_ensure_night_plan_available` runs `energy_model_main.py` with an outer maximum of 240 seconds. `app/runtime/slot_orchestration.py::_run_adjust_03` catches prep failure; when no plan exists it performs one fail-safe standby and returns without entering monitor/forced control.

## Strong hypothesis — not yet proven

`app/energy_plan/weather_history.py::archive_weather_history` may consume a large part of the 240-second budget when the local weather cache is cold.

Reasons:

- `.env.example` has explicit KP CSV months `2026-04,2026-05` plus latest-month download.
- Weather-history currently derives `requested_days` as every calendar day from the earliest CSV date through the latest CSV date, including dates with no consumption rows.
- Missing weather days are fetched serially in chunks.
- Defaults are 14 days per chunk and up to 30 seconds per HTTP request.
- The cache path is under local `artifacts/`; a Cloud Run job filesystem should not be assumed durable across executions.

This is a hypothesis until production-like timing or equivalent deterministic reproduction shows that weather-history fetching is the dominant phase.

## Required acceptance outcome

A correct fix must demonstrate all of the following:

1. Plan-generation phase that consumed the budget is identified with evidence.
2. Normal production-like plan generation completes inside the 03 budget with meaningful safety margin, or optional external-data work is bounded so the model falls back and still emits a usable plan.
3. A timeout/no-plan case emits an explicit sanitized prep-failure reason.
4. A timeout/no-plan case performs exactly one fail-safe standby and never enters monitor/forced control.
5. Successful plan generation reaches existing provenance/monitor logic unchanged.
6. No 23/07 ownership, 06:45/06:50/06:55 fences, exact-target stop, forced mode-only/readback, SOC parsing, or optimizer/constraint semantics are changed without separate evidence and approval.
7. No normal 03 manual execution is used for testing.