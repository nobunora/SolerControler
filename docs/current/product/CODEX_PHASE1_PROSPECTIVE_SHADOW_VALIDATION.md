# Codex Instructions: Phase 1 Prospective Shadow Validation

## Objective

Phase 0 has established an append-only forecast-vintage evidence store and has passed local validation for generated/issued time, lead time, weather/shortwave evidence, provider/physical-PV evidence, idempotency, multi-vintage retention, cutoff leakage protection, and compatibility with the existing `forecast_hourly` latest-value contract.

The next task is **Phase 1: prospective shadow validation of the fixed adaptive correction gate**.

This phase must collect causal evidence without changing any production forecast or control behavior.

The fixed candidate policy is:

```text
selector granularity: per hour
selector score window: trailing 21 calendar days
required improvement over baseline: 2% MAE
fallback: baseline
```

This policy is frozen for prospective validation. Do not retune it during the collection period.

## Mandatory repository instructions

Before changing code, read and follow:

- `AGENTS.md`
- `docs/current/agent/agent_working_rules.md`
- `docs/current/product/HOURLY_PV_ADAPTIVE_GATE_SIMULATION.md`
- `docs/current/product/CODEX_FORECAST_SNAPSHOT_VALIDATION_JA.md`
- `docs/current/product/CODEX_FORECAST_SNAPSHOT_VALIDATION_RESULT_JA.md`

Follow the repository-required code-quality audit sequence before tests.

## Precondition: verify the current master quality result

The local Phase 0 report could not run every repository quality tool. GitHub Actions does run the full repository quality workflow.

Before implementing Phase 1:

1. inspect the latest `quality` workflow result on `master`;
2. confirm there are no remaining forecast-snapshot-specific failures;
3. if a snapshot-related failure remains, fix only that failure first in a small, separate commit or PR;
4. do not expand this task into unrelated repository-wide quality cleanup.

Existing unrelated quality debt must be reported separately rather than silently modified.

## Strict non-goals

Do **not** change any of the following in this phase:

- production PV forecast values;
- `app/forecasting/correction_model.py` production correction behavior or parameters;
- the physical PV model equations or calibration coefficients;
- forecast provider weights;
- SOC optimizer objectives or constraints;
- charge/discharge commands or battery-control behavior;
- the existing mutable `forecast_hourly` latest-value contract;
- the frozen `21d / 2% / per-hour / baseline-fallback` policy parameters.

The output of this phase is evidence only.

## Required architecture

Implement the shadow path as a side channel:

```text
immutable forecast snapshot
        |
        +--> baseline candidate
        +--> production-shaped residual candidate
        +--> same-hour bias candidate, 7-day half-life
        +--> same-hour bias candidate, 14-day half-life
        |
        +--> fixed causal selector
                |
                +--> immutable shadow decision

actual PV arrives later
        |
        +--> append-only shadow outcome/evaluation
```

No shadow-selected value may be fed back into production forecasting, SOC planning, or battery control in this PR.

## Candidate forecasts

For each eligible target hour, freeze the same four candidates used by the adaptive-gate simulation:

1. `baseline`
2. `production_like_45d`
3. `same_hour_bias_45d_hl7d`
4. `same_hour_bias_45d_hl14d`

Where production code can reproduce the current correction exactly from available causal inputs, use a clearly named production-equivalent implementation. If exact equivalence cannot be proved, retain the `production_like` terminology and document the difference.

All candidate calculations must use only information available at the shadow-decision cutoff.

## Selector rules

The selector is fixed and causal:

```text
per-hour model selection
trailing 21 calendar days of prior out-of-sample model errors
correction candidate must beat baseline MAE by at least 2%
otherwise select baseline
```

For target date `D`, selector scoring must use only outcome records with target dates `< D`.

Never use:

- actual PV from the target date;
- weather observations from after the decision cutoff;
- forecast vintages issued after the operational cutoff;
- retroactively recomputed candidate predictions in place of the prediction that was actually frozen.

If there is insufficient clean history, select `baseline` and record an explicit reason such as `insufficient_shadow_history`.

### Prospective-history rule

For acceptance evidence, selector performance history should come from decisions that were themselves frozen prospectively under this Phase 1 mechanism.

Do not silently seed the 21-day selector score window with overwritten/latest historical forecasts whose original issue-time vintage cannot be proved.

If legacy data is used only for diagnostics or warm-up comparison, tag it explicitly and exclude it from the primary prospective acceptance metrics.

## Immutable shadow decision record

Create an append-only decision store for each target hour. Use the repository's existing persistence architecture and support SQLite first; preserve a backend-neutral contract so PostgreSQL/Firestore can follow the same semantics.

The record should include, at minimum:

```text
shadow_decision_id
policy_name
policy_version
decision_at
cutoff_at
target_at
target_date
target_hour
forecast_snapshot_id
forecast_run_id
forecast_issued_at
lead_minutes
baseline_prediction_kwh
candidate_predictions_json
candidate_score_summary_json
selected_model
selected_prediction_kwh
baseline_fallback_reason
selector_window_days
required_margin_fraction
history_window_start
history_window_end
history_sample_counts_json
decision_quality_flags_json
source_code_version or equivalent provenance when available
recorded_at
```

The identity must be deterministic for the same immutable decision inputs so retrying persistence does not create duplicates.

A regenerated forecast vintage must produce a distinct shadow decision when the operational system would legitimately make a new decision for that vintage.

Do not overwrite an older decision merely because a newer forecast arrives.

## Freeze-before-actual requirement

A shadow decision is valid only if it is persisted before the corresponding actual PV outcome is available to the evaluator.

Persist enough timing information to prove this ordering.

The decision row must not contain actual PV or post-outcome weather observations.

## Append-only outcome/evaluation record

After actual PV becomes available, create a separate append-only outcome/evaluation record keyed to the immutable shadow decision.

Include at minimum:

```text
shadow_decision_id
target_at
actual_pv_kwh
baseline_error_kwh
selected_error_kwh
candidate_errors_json
baseline_absolute_error_kwh
selected_absolute_error_kwh
selected_minus_baseline_absolute_error_kwh
selected_model
correction_applied
outcome_recorded_at
outcome_quality_flags_json
```

If reliable realized weather/irradiance data is already available from an existing source without adding a new dependency, also attach or reference realized weather evidence needed for later decomposition, for example:

```text
observed_shortwave_or_analysis_shortwave
observed_cloud-related fields when available
weather observation provenance
```

Do not add a new external weather provider or dependency without explicit approval.

Never mutate the original shadow decision when the outcome arrives.

## Weather/model error decomposition readiness

The Phase 1 data contract should make the following later analysis possible:

```text
forecast weather / GTI
        -> frozen physical PV forecast
        -> frozen final baseline PV forecast
        -> frozen shadow candidate predictions
        -> actual PV
```

Do not implement a new meteorological correction model in this PR. Only preserve the evidence required to distinguish likely weather-input error from PV-conversion/model error later.

## Persistence requirements

### SQLite

Implement and test the complete contract first.

Requirements:

- append-only semantics;
- retry idempotency;
- multiple legitimate vintages retained;
- indexes suitable for target hour, decision time, policy version, and outcome lookup;
- migration is idempotent;
- existing tables and consumers remain compatible.

### PostgreSQL / Firestore

If the existing backend architecture allows a minimal equivalent implementation without production credentials, implement contract parity and test with mocks/fakes.

Do not write to production cloud resources during validation.

If live non-production infrastructure is unavailable, report `NOT RUN` rather than weakening the SQLite evidence.

## Required tests

Add focused regression tests covering at least:

1. decision uses the intended forecast snapshot vintage;
2. future-issued forecast snapshots are never selected;
3. same immutable decision retry inserts zero duplicates;
4. a legitimate regenerated forecast vintage can produce a second decision;
5. candidate predictions are frozen before actuals;
6. outcome persistence does not mutate the decision row;
7. selector uses only dates strictly before the target date;
8. insufficient history falls back to baseline;
9. a correction candidate must clear the 2% MAE margin;
10. per-hour selection does not pool a different hour's score history;
11. the fixed policy parameters cannot drift silently;
12. existing production forecast output is byte-for-byte or value-for-value unchanged for the same input fixture where practical;
13. existing SOC/control paths do not read the shadow-selected value;
14. SQLite migration and indexes are idempotent;
15. backend parity tests for Firestore/PostgreSQL where supported.

Run focused tests first, then the full suite according to repository instructions.

## Shadow reporting

Add a reporting path that can summarize prospective outcomes without altering the model.

At minimum report:

```text
sample count
coverage / valid-decision count
baseline MAE
shadow-selected MAE
relative MAE improvement
baseline signed bias
shadow-selected signed bias
correction application rate
baseline fallback rate
selection count by candidate model
selection count by hour
quality-flag counts
lead-time distribution
forecast-vintage count
```

Also report rolling/recent windows when enough data exists, especially:

```text
last 7 days
last 14 days
last 21 days
```

Do not tune policy parameters in response to these reports during the prospective collection period.

## Downstream SOC / purchase-cost evaluation

If the repository already provides a safe offline replay/simulation interface, record or compute the counterfactual impact of the shadow-selected PV forecast on:

- projected SOC;
- grid purchase energy;
- purchase cost;
- unmet/constraint penalties where applicable.

This must remain simulation-only. Do not inject the shadow forecast into live planning or device control.

If a clean offline interface is not already available, document this as a follow-up instead of broadening this PR.

## Prospective validation window

Do not recommend production adoption from a few days of data.

Freeze the policy and collect at least **30 calendar days** of prospective decisions/outcomes before any production-adoption decision, unless the user explicitly changes the validation horizon.

During this period:

- do not retune 21 days, 2%, per-hour selection, or the candidate set;
- do not delete unfavorable outcomes;
- do not backfill decisions after actuals are known and count them as prospective;
- mark missing or invalid rows rather than silently excluding them.

## Acceptance criteria for Phase 1 implementation

The implementation PR is acceptable when all of the following are true:

```text
[ ] production forecast values are unchanged
[ ] SOC/control behavior is unchanged
[ ] fixed policy is exactly per-hour / 21d / 2% / baseline fallback
[ ] all four candidate predictions are frozen before actuals
[ ] decisions are append-only and idempotent
[ ] regenerated forecast vintages remain distinct
[ ] selector history is strictly causal
[ ] future-issued snapshots cannot leak into selection
[ ] actuals are stored separately from decisions
[ ] prospective outcome reports can be generated
[ ] quality flags expose missing/invalid evidence
[ ] SQLite contract is fully tested
[ ] Firestore/PostgreSQL parity is implemented/tested where safely available, otherwise explicitly NOT RUN
[ ] repository-focused tests pass
[ ] full suite status is reported
[ ] unrelated quality debt is not mixed into the PR
```

## Required PR result report

Create a separate implementation PR. In its description, report:

```text
code-quality audit: PASS / FAIL
focused tests: N passed / N failed
full tests: N passed / N skipped / N failed
production forecast regression: PASS / FAIL
SOC/control non-interference: PASS / FAIL
shadow decision idempotency: PASS / FAIL
regenerated-vintage separation: PASS / FAIL
cutoff leakage protection: PASS / FAIL
strict-prior selector history: PASS / FAIL
2% margin behavior: PASS / FAIL
baseline fallback on insufficient history: PASS / FAIL
outcome append-only contract: PASS / FAIL
SQLite migration: PASS / FAIL
Firestore parity: PASS / FAIL / NOT RUN
PostgreSQL parity: PASS / FAIL / NOT RUN
shadow report generation: PASS / FAIL
offline downstream SOC/cost evaluation: PASS / FAIL / DEFERRED
```

Include only non-sensitive aggregate diagnostics. Do not publish credentials, site/project identifiers, private generation traces, or household-specific operational data.

## Stop condition

Once the shadow recording/evaluation infrastructure is implemented and validated, stop.

Do **not** enable adaptive correction in production in this PR.

Production adoption must be a separate decision after the frozen policy has accumulated sufficient prospective evidence.