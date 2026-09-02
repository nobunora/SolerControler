# HANDOFF

## Active workspace

- Persistent Draft PR branch: `codex/persistent-workspace`
- Baseline master at workspace bootstrap: `1c07e4ba51dfbd60902972baeb19915a93cf33b4`
- Active incident: 2026-09-02 morning battery SOC reached 0%.
- Latest investigation report: `docs/completed/reports/zero_soc_investigation_2026-09-02.md`
- Investigation classification: `PLAN_GENERATION_OR_PREP_FAILURE`
- Investigation result: `INVESTIGATION_COMPLETE_CAUSE_NARROWED`

## Current objective

Make the scheduled 03 plan-preparation path bounded, observable, and reliable enough to reach the existing monitor/forced-charge controller under normal production conditions. Preserve the existing one-standby fail-safe if no usable plan can be produced.

Do not treat a larger outer timeout as a sufficient fix. First identify which plan-generation phase consumed the 240-second budget using production-like inputs and sanitized phase timing/evidence. Then implement the smallest change that removes the demonstrated bottleneck while preserving model semantics as far as possible.

## Confirmed incident facts

- Scheduled 03 execution existed and started at 2026-09-02 03:00 JST.
- Initial CSV phase completed.
- `energy_model_main.py` was launched with `FORECAST_DATE_OVERRIDE=2026-09-02`.
- No `03-plan-provenance`, `03-monitor contract`, `03-monitor soc`, or `03-monitor stop reason` line appeared.
- Roughly 240 seconds after model launch, the execution entered behavior consistent with the existing no-plan fail-safe standby path.
- Official KP-NET CSV shows 03:00-06:30 SOC stayed at 1% and charge energy was 0.000 kWh; 07:00 SOC was 0%.
- 23 and 07 scheduled mode-only jobs succeeded.

## Current task boundary

Primary files to inspect first:

- `app/runtime/slot_orchestration.py::_run_adjust_03`
- `app/runtime/cloud_job.py::_ensure_night_plan_available`
- `app/energy_plan/workflow.py::_build_consumption_forecasts`
- `app/energy_plan/weather_history.py::archive_weather_history`
- `tests/test_cloud_job_runner.py`

Only expand the source-change set when evidence proves the bottleneck is owned by another directly related file.

## Required workflow

1. Read `.ai/BUG_REPORT.md` and `.ai/DECISIONS.md`.
2. Review only the latest relevant Draft PR diff plus the files above.
3. Reproduce/measure plan-generation phases without a normal 03 manual execution.
4. Distinguish confirmed findings from hypotheses in the PR discussion.
5. Implement the smallest proven fix.
6. Add focused regression tests and run the existing quality suite.
7. Push to this same branch/Draft PR.
8. Update these handoff files when the evidence or decision changes materially.

Do not create another PR for this incident.