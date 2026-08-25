# Hourly Weather-Vector PV Correction: Reproducible Review Package

## 1. Purpose and review status

This document packages an offline experiment for independent technical review. It asks whether sparse weather-vector matches should be handled independently by hour and, when a raw correction has performed poorly at that hour, whether its direction or magnitude should be learned from earlier outcomes.

The experiment is decision support only. It has not changed the production forecast, SOC optimizer, Firestore data, or deployment configuration. The results do not justify production adoption without prospective validation.

Repository state used to prepare this package:

- Git branch: `fix/previous-billing-period-soc-objective`
- Source commit before this report: `5995bc643866f8c8edf842eafd782ca373043c89`
- Python: 3.14.3
- Preparation date: 2026-08-25 (Asia/Tokyo)

## 2. Background and chronology

The investigation began with the observation that weather-vector similarity appeared to produce too few matches. The initial hypothesis was that the system accepted or rejected an entire day as one unit. Code inspection showed that this premise was only partly correct:

- Candidate search in `scripts/backtest_hourly_pv_weather_similarity.py` already compares a target hour with earlier observations at the same hour.
- Production correction in `app/forecasting/correction_model.py` is also hour-oriented.
- The checked-in backtest has a separate daily reporting gate: a day contributes to its aggregate only when at least eight hours can be evaluated.

We therefore simulated an explicit counterfactual whole-day gate and compared it with independent hourly application. With a minimum of two candidates, the hourly gate reduced overall MAE from 0.31898 to 0.30986 kWh on the original replay. It also made 582 of 1,021 evaluable hours usable, whereas only 28 days satisfied the strict all-hours condition. This established that all-or-nothing daily application wastes usable matches.

The second request was to keep using a correction whenever an hour hits, but alter the correction from prior forecast-versus-actual gaps when its historical performance is poor. An early ad hoc replay described the history as "seven prior hits" but used a target-conditioned candidate set as the performance history. That ambiguity was found during preparation of this review package. Those provisional numbers were discarded. The committed experiment below requires every performance-history record to have been a genuine hit under the information available on its own date.

## 3. Review package contents

- `scripts/reproduce_hourly_reverse_vector_correction.py`: executable replay.
- `docs/current/product/evidence/hourly_vector_dataset_2026-08-23.csv`: de-identified frozen input.
- `docs/current/product/evidence/hourly_reverse_vector_correction_results_2026-08-23.json`: expected detailed output.

The frozen dataset is included so the main result does not drift when Firestore receives new rows. It contains only date, hour, hourly actual PV energy, saved forecast PV energy, forecast shortwave radiation, and derived weather class. It contains no project ID, database ID, account ID, device ID, credential, load, battery SOC, purchase, sale, or tariff data.

Snapshot inventory:

- 3,067 actual hourly rows, 2026-04-17 through 2026-08-23.
- 2,032 forecast hourly rows, 2026-05-24 through 2026-08-25.
- Dataset SHA-256: `5333F655E677F556A249C58ECF479D40256130D4C70C97F937AED73C21CB5B76`.
- Expected-result SHA-256: `5F6D69D8094F90917C1F0C0CED30922D91F427A77A8C19E7743CCBEE75E84E08`.

The original local SQLite source aggregated `SUM(COALESCE(pv_kwh, 0))` by the date and hour substrings of `monitoring_samples.ts`. The original forecast source streamed the configured Firestore `forecast_hourly` collection and used `date`, `hour`, `forecast_pv_kwh`, `forecast_shortwave_radiation_w_m2`, and `forecast_weather_code`. The frozen snapshot is the authoritative input for reproduction.

## 4. Exact reproduction

From the repository root, run:

```powershell
python -m py_compile scripts/reproduce_hourly_reverse_vector_correction.py
python scripts/reproduce_hourly_reverse_vector_correction.py `
  --output artifacts/analysis/hourly_reverse_vector_correction_results.json
```

Compare the generated file with the committed expected result. The `parameters.input` field is expected to differ if an absolute or alternative dataset path is supplied; numerical `periods` content should match.

To refresh the de-identified dataset from the currently configured sources, first run the repository's canonical KP-NET import wrapper, then explicitly request live input:

```powershell
pwsh -NoProfile -File scripts/run_kpnet_import_from_env.ps1
python scripts/reproduce_hourly_reverse_vector_correction.py `
  --live `
  --export-dataset artifacts/analysis/hourly_vector_dataset_refresh.csv `
  --output artifacts/analysis/hourly_vector_results_refresh.json
```

This refresh requires the repository's existing environment configuration. It will not be numerically identical if forecast history or monitoring data changed. Do not commit a refreshed dataset without checking its privacy and provenance.

## 5. Evaluation population and leakage controls

Default parameters are:

- Target dates: 2026-06-01 through 2026-08-23, inclusive.
- Target hours: 07:00 through 22:00, inclusive.
- Lookback: 45 strictly preceding calendar days.
- Minimum similar candidates for a hit: 2.
- Minimum genuine historical hits before learning a direction or multiplier: 7.
- Metric: hourly mean absolute error, in kWh.

An hour is evaluable only when both an actual value and a positive saved PV forecast exist. The complete range contains 1,021 evaluable hours. Although the loop includes 07:00-22:00, this snapshot has no positive/evaluable PV forecasts at 20:00-22:00 and only 25 at 19:00.

No target-date actual is used to construct its prediction. For target date `t` and hour `h`:

1. Raw candidates come only from dates `t-1` through `t-45` at hour `h`.
2. A candidate must have a forecast and actual, positive forecast PV, the same weather class as the target, positive target and candidate shortwave radiation, and candidate shortwave within 70%-130% of the target.
3. A performance-history record for sign or multiplier selection is accepted only if that earlier date itself had at least two candidates under its own strictly earlier 45-day history.
4. Thus selection for `t` never uses `t`'s outcome, and selection for a historical date never uses a later date.

Weather codes are grouped exactly as in the existing backtest: 0-3 `clear`, 45-48 `fog`, 51-67 `rain`, 71-77 `snow`, 80-99 `shower`, and all others `other`.

## 6. Algorithms

Let `f(t,h)` be the saved PV forecast and `y(t,h)` the actual PV. For the current target, let `S(t,h)` be the similar candidate set above.

### 6.1 Baseline

```text
prediction_baseline(t,h) = f(t,h)
```

### 6.2 Raw vector-residual correction

For a hit:

```text
c(t,h) = median over i in S(t,h) of [y(i,h) - f(i,h)]
prediction_raw(t,h) = max(0, f(t,h) + c(t,h))
```

For a non-hit, `c(t,h)=0`. This is the existing vector-residual concept evaluated with the requested hourly gate and minimum two candidates.

### 6.3 Method A: sign selection

Choose `q` from `[+1, -1]` by the lower MAE over the genuine same-hour historical-hit records. A tie selects `+1`. Then:

```text
prediction_sign(t,h) = max(0, f(t,h) + q*c(t,h))
```

When fewer than seven genuine hits exist, use `q=+1`. Therefore a current hit always receives the raw correction or its reverse; insufficient learning history does not disable it.

### 6.4 Method B: multiplier selection

Choose `q` from `[-1, -0.5, 0, 0.5, 1, 1.5]` by the lower historical MAE, using the listed order as the tie-break order:

```text
prediction_multiplier(t,h) = max(0, f(t,h) + q*c(t,h))
```

When fewer than seven genuine hits exist, use `q=1`. The inclusion of `q=0` means this method may suppress an unreliable correction even though the current hour hit. This is a deliberate comparison point and differs from a strict interpretation of "always correct on hit."

### 6.5 Method C: correction-error residual

For each genuine prior hit, compute the error remaining after that date's own raw correction. Add the median remaining error to the current raw correction:

```text
e(i,h) = y(i,h) - [f(i,h) + c(i,h)]
prediction_error_residual(t,h) = max(0, f(t,h) + c(t,h) + median(e(i,h)))
```

When fewer than seven genuine hits exist, this method uses only the raw correction.

## 7. Results

All values below are hourly MAE in kWh. Coverage is 582/1,021 = 57.00% over the complete period.

| Evaluation period | Samples | Baseline | Raw | Sign | Multiplier | Error residual |
|---|---:|---:|---:|---:|---:|---:|
| 2026-06-01..08-23 | 1,021 | 0.31784 | 0.30986 | 0.31140 | **0.30707** | 0.31169 |
| 2026-07-01..08-23 | 637 | 0.32199 | 0.30939 | 0.31186 | **0.30493** | 0.31233 |
| 2026-08-01..08-23 | 264 | **0.32231** | 0.34276 | 0.34525 | 0.32895 | 0.34901 |
| 2026-08-10..08-23 | 156 | **0.34658** | 0.37253 | 0.37674 | 0.35747 | 0.38589 |

Over the full period, multiplier selection improves MAE by 3.39% relative to the baseline and 0.90% relative to the raw correction. From July onward it improves baseline MAE by 5.30% and raw-correction MAE by 1.44%.

However, August-only and recent-14-day results remain worse than no correction. Multiplier selection reduces the raw method's August degradation substantially, but does not eliminate it. The recent-14-day MAE is 0.35747 versus baseline 0.34658, a 3.14% degradation.

Across the complete period, multiplier choices were: `-0.5` 7 times, `0` 42, `0.5` 217, `1` 193, and `1.5` 37. `-1` was never selected in the corrected genuine-hit replay. The principal benefit therefore came from attenuation, not full reversal. This is an important correction to the initial expectation that reversing direction would be the dominant remedy.

The detailed JSON contains sample count, hit count, and MAE by hour and period. Full-period multiplier MAE improved baseline at 07, 08, 09, 10, 11, 12, 14, 16, 17, 18, and 19, but worsened it at 13 and 15. Hour 18 remained the strongest consistent improvement. Hours with few samples, especially 19, must not be generalized.

## 8. Interpretation

The evidence supports independent hourly matching. It does not support unconditional use of the unscaled raw residual. A learned multiplier is better than binary sign reversal because most selected factors are `0.5` or `1.0`; the raw correction is more often too large than directionally wrong.

The inclusion of zero is material. If the operational requirement is literally that every hit must change the prediction, rerun with a multiplier grid that excludes zero and treat that as a separate policy-constrained model. The current best result sometimes chooses no correction after a hit because historical evidence says both directions are worse.

## 9. Concerns, limitations, and open review questions

1. **Temporal instability.** Aggregate gains are driven by earlier periods. August and the most recent 14 days remain worse than baseline.
2. **Small and local dataset.** This is one system over roughly three months, with a 45-day warm-up dependency. It does not establish seasonal or cross-site generalization.
3. **Forecast-vintage uncertainty.** The Firestore rows are treated as the saved forecast for that date/hour. The replay does not independently prove the issue time or that no later overwrite occurred.
4. **Timestamp assumption.** Actual dates and hours were originally extracted directly from stored timestamp strings without an explicit timezone conversion.
5. **Selection bias.** Only positive-forecast hours with both saved forecast and actual are evaluated. Late-hour absence means the result is principally a daylight result.
6. **Hyperparameter reuse.** The 45-day lookback, two-candidate threshold, seven-hit learning minimum, shortwave tolerance, weather classes, and multiplier grid were not selected through nested validation. Their apparent performance can be optimistic.
7. **Overlapping windows.** Adjacent target dates share most history. Standard independent-sample uncertainty estimates would be inappropriate without block resampling or date-level analysis.
8. **MAE only.** The study does not report bias, RMSE, quantiles, downstream SOC cost, curtailed energy, purchases, or sales.
9. **No uncertainty gate.** The multiplier is selected by point-estimate MAE without requiring a meaningful margin over alternatives.
10. **No production-equivalence claim.** This script is an experimental replay. It does not prove identical behavior to every production data-loading, forecast-vintage, or correction path.

Questions for expert review:

- Should the operational policy allow multiplier zero after a vector hit?
- Should multiplier selection minimize signed bias, MAE, asymmetric energy cost, or a downstream SOC objective?
- Should selection history use a fixed 45-day window, exponentially decayed weights, or a seasonally matched window?
- Should hours be partially pooled so sparse morning/evening hours borrow strength without losing hour-specific behavior?
- Is the weather vector too coarse, particularly the categorical weather class and ±30% shortwave rule?
- What prospective evaluation length and acceptance threshold should block or allow production use?

## 10. Recommended next step

Do not deploy this correction yet. Run a prospective shadow evaluation in which forecasts and correction decisions are frozen before actuals arrive. Report baseline and candidate MAE by hour, signed bias, hit coverage, multiplier choice, and downstream SOC-cost impact for at least 30 days and preferably across a seasonal transition. Predetermine an acceptance rule that includes recent-window non-inferiority, not only full-period improvement.

## 11. Scope and rollback

This package adds documentation, a read-only experimental replay, a de-identified input snapshot, and an expected result. It changes no runtime imports, APIs, dependencies, database schemas, production configuration, or deployed behavior. Removal is fully reversible by deleting these four review artifacts.
