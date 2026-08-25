# Hourly PV adaptive correction gate simulation

## 1. Purpose

The root-cause simulation found that historical PV residual corrections improve
the aggregate period but lose predictive value in August and the most recent 14
days. This follow-up asks a narrower question:

> Can a strictly causal selector preserve correction gains when they are useful,
> while falling back to the physical baseline when recent out-of-sample evidence
> no longer supports correction?

This is an offline simulation only. It does not change production forecasting,
SOC optimization, persistence, Firestore, SQLite schemas, or deployment
behavior.

## 2. Candidate forecasts

Every target hour has four candidate predictions:

1. `baseline`: uncorrected saved PV forecast.
2. `production_like_45d`: current hard weather-vector rule with the production
   count / local-variance shrinkage shape.
3. `same_hour_bias_45d_hl7d`: same-hour additive residual with 45-day history
   and 7-day decay half-life, no weather-vector gate.
4. `same_hour_bias_45d_hl14d`: same as above with 14-day decay half-life.

`production_like_45d` is intentionally not called production-equivalent because
the frozen dataset cannot prove original forecast issue time / vintage.

Candidate predictions themselves use only strictly earlier calendar dates.

## 3. Causal selector

For target day `D`, selector scoring uses only prediction errors from dates `< D`.
No actual from `D` is available when the model is selected.

The grid contains 24 selectors:

- trailing performance window: 3 / 7 / 14 / 21 days,
- required MAE improvement against baseline: 0 / 2 / 5%,
- granularity:
  - `pooled`: one model selected for the whole target day,
  - `per_hour`: each clock hour selects independently from prior same-hour
    out-of-sample errors.

If there is insufficient history or no correction model clears the required
improvement margin, the selector returns `baseline`.

## 4. Static candidates reproduce the previous limitation

| Model | All | July+ | August | Recent 14d |
|---|---:|---:|---:|---:|
| Baseline MAE | 0.31784 | 0.32199 | 0.32231 | 0.34658 |
| Production-like 45d | 0.30297 (+4.68%) | 0.29916 (+7.09%) | 0.32451 (-0.68%) | 0.35139 (-1.39%) |
| Same-hour bias hl7d | 0.29017 (+8.71%) | 0.29607 (+8.05%) | 0.32776 (-1.69%) | 0.34883 (-0.65%) |
| Same-hour bias hl14d | 0.29100 (+8.44%) | 0.29633 (+7.97%) | 0.32750 (-1.61%) | 0.34833 (-0.50%) |

A single static correction still cannot satisfy both aggregate improvement and
recent non-inferiority.

## 5. Best recent-robust causal selector

The strongest selector satisfying non-inferiority in both August and the recent
14-day window is:

```text
gate_per_hour_window21d_margin02pct
```

Parameters:

- selection is per clock hour,
- trailing model-score window is 21 days,
- a correction model must beat baseline trailing MAE by at least 2%,
- otherwise use baseline.

Results:

| Period | Baseline MAE | Adaptive gate MAE | Improvement |
|---|---:|---:|---:|
| All | 0.31784 | **0.29205** | **+8.11%** |
| July+ | 0.32199 | **0.29359** | **+8.82%** |
| August | 0.32231 | **0.32119** | **+0.35%** |
| Recent 14d | 0.34658 | **0.34275** | **+1.10%** |

This is materially different from the static candidates: the selector keeps
most of the aggregate gain while no longer degrading the two recent evaluation
windows.

### Application behavior

| Period | Correction application rate | Baseline fallbacks |
|---|---:|---:|
| All | 70.91% | 297 / 1,021 |
| August | 59.47% | 107 / 264 |
| Recent 14d | 51.28% | 76 / 156 |

The gate automatically becomes more conservative as the residual regime loses
predictability. This behavior matches the root-cause diagnosis: recent history
contains weaker correction signal, so forcing a correction on every hit is the
wrong operational policy.

Recent-14-day model selections were:

- baseline: 76,
- production-like 45d: 35,
- same-hour bias hl14d: 25,
- same-hour bias hl7d: 20.

The gain therefore does not come from choosing one universally superior
correction. It comes from allowing the selected model to change by hour and
explicitly retaining no-correction as a valid outcome.

## 6. Why a confidence margin matters

The all-period MAE-optimal selector without a margin is:

```text
gate_per_hour_window21d_margin00pct
```

It improves the full period by 8.54%, but still degrades:

- August by 0.38%,
- recent 14d by 0.54%.

Requiring a 2% trailing advantage reduces correction application and changes the
recent result from degradation to improvement.

This is evidence that the selector should not act on tiny historical MAE
differences. A minimum evidence margin functions as a practical confidence / safety
gate against noisy residual histories.

## 7. Diagnostic upper bound

A deliberately non-causal oracle chooses the best candidate only after each
day's actual outcomes are known. It is not deployable and is included only to
measure theoretical switching headroom.

| Period | Oracle improvement vs baseline |
|---|---:|
| All | +20.48% |
| July+ | +16.27% |
| August | +8.00% |
| Recent 14d | +8.57% |

The gap between the causal selector (+8.11% all / +1.10% recent) and the oracle
(+20.48% all / +8.57% recent) is important. It suggests that there is meaningful
forecasting value in predicting **which correction regime will work**, even
though the residual magnitude itself is weakly predictable in the recent data.

This points toward regime classification / model selection rather than more
aggressive residual extrapolation as the next algorithmic research direction.

## 8. Interpretation

The combined simulations now support a clearer architecture:

```text
physical PV forecast
        |
        +--> candidate residual corrections
        |       - production-shaped weather analog
        |       - recent same-hour bias
        |
        +--> causal performance gate
                - baseline is always an explicit candidate
                - require evidence margin
                - choose per hour
                - fall back to baseline on weak evidence
```

The key correction to the earlier design is that **correction applicability is a
first-class prediction problem**. The system should not assume that a weather
vector hit implies that a residual correction is useful.

## 9. Limits and overfitting warning

This result is still exploratory, not production evidence.

- The selector grid (24 arms) was evaluated on the same frozen period used for
  earlier diagnostic work.
- The two same-hour candidate specifications were themselves motivated by
  results from the same dataset.
- Forecast vintages / issue times are not immutable in the historical store.
- There is no independently observed irradiance field to decompose weather
  forecast error from PV conversion error.
- The recent 14-day set has only 156 hourly samples.

Therefore `21d + 2% + per-hour` must be treated as a **candidate fixed policy for
prospective validation**, not a production-tuned optimum.

## 10. Recommended engineering sequence

### Next: Phase 0 forecast evidence integrity

Now that simulation has identified a viable architecture, implement immutable
forecast snapshots before production adoption:

- `issued_at`,
- target timestamp,
- lead time,
- provider / model provenance,
- PV model version,
- weather feature-quality flags.

Retain the existing latest `forecast_hourly` behavior for compatibility.

### Then: prospective shadow gate

Freeze one selector policy before observing new outcomes. The current candidate
is:

```text
per-hour selector
21-day trailing out-of-sample MAE
2% minimum advantage over baseline
baseline fallback
```

Run it in shadow mode with no production forecast mutation. Record candidate
predictions and the gate decision before actuals arrive. Evaluate at least MAE,
signed bias, correction application rate, and downstream SOC / purchase-cost
impact.

The selector must be rejected or revised if recent-window non-inferiority does
not hold prospectively.

## 11. Reproduction

```powershell
python -m py_compile scripts/diagnose_hourly_pv_adaptive_gate.py
python scripts/diagnose_hourly_pv_adaptive_gate.py `
  --output artifacts/analysis/hourly_pv_adaptive_gate_diagnostic.json
```

The repository `pv-correction-diagnostic` GitHub Actions workflow also runs this
simulation against the frozen dataset without contacting live services.
