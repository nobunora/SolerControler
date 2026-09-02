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

## Hypothesis resolution

The weather-history hypothesis was not confirmed for the incident-shaped production input.

Reasons:

- `.env.example` has explicit KP CSV months `2026-04,2026-05` plus latest-month download.
- Weather-history currently derives `requested_days` as every calendar day from the earliest CSV date through the latest CSV date, including dates with no consumption rows.
- Missing weather days are fetched serially in chunks.
- Defaults are 14 days per chunk and up to 30 seconds per HTTP request.
- The cache path is under local `artifacts/`; a Cloud Run job filesystem should not be assumed durable across executions.

Cold-cache measurement requested 33 actual consumption dates in 3 chunks and completed weather history in 5.625s; the complete model finished in 44.554s. Incident Cloud Logging instead emitted two httplib2 per-request-timeout warnings roughly 213s after model start. The only plan-generation path using googleapiclient/httplib2 is the optional occupancy Google Sheets read, whose transport previously had no request timeout. This is the evidenced bounded-I/O defect boundary; the exact upstream network stall remains historical and cannot be replayed locally.

## Implemented remediation awaiting review

- Bound occupancy Sheet transport to 15s and sanitize failure logs so spreadsheet/resource IDs are not emitted.
- Avoid weather requests for dates without consumption rows and cap total weather archive I/O at 60s, returning partial data plus diagnostics on exhaustion.
- Emit structured sanitized 03 prep outcome evidence while preserving exactly one standby on no-plan failure.
- Post-change cold-cache model smoke: 37.100s total, 202.900s margin versus 240s.

## Required acceptance outcome

A correct fix must demonstrate all of the following:

1. Plan-generation phase that consumed the budget is identified with evidence.
2. Normal production-like plan generation completes inside the 03 budget with meaningful safety margin, or optional external-data work is bounded so the model falls back and still emits a usable plan.
3. A timeout/no-plan case emits an explicit sanitized prep-failure reason.
4. A timeout/no-plan case performs exactly one fail-safe standby and never enters monitor/forced control.
5. Successful plan generation reaches existing provenance/monitor logic unchanged.
6. No 23/07 ownership, 06:45/06:50/06:55 fences, exact-target stop, forced mode-only/readback, SOC parsing, or optimizer/constraint semantics are changed without separate evidence and approval.
7. No normal 03 manual execution is used for testing.
