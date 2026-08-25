# Hourly PV correction root-cause simulation

## 1. Purpose

This document records the offline diagnostic performed after
`HOURLY_VECTOR_CORRECTION_REVIEW.md` showed only a small aggregate gain and a
regression in August / the most recent 14 days.

The objective is not to select a production algorithm yet. It is to determine
whether the limiting factor is primarily:

1. sparse / poor analog matching,
2. an inappropriate residual representation or lookback,
3. a recent regime bias that a simpler online filter can track, or
4. a lack of repeatable information in past PV residuals.

All experiments use the repository-tracked, de-identified frozen dataset only.
No production forecast, SOC optimization, database schema, live Firestore data,
or deployment behavior is changed.

## 2. Frozen input and evaluation population

Input:

- `docs/current/product/evidence/hourly_vector_dataset_2026-08-23.csv`
- 3,123 CSV data rows excluding the header.
- 1,137 rows have both an actual value and a positive saved PV forecast.
- 363 of those positive-forecast rows have non-positive saved forecast
  shortwave radiation.
- The first positive saved forecast shortwave value in the evaluable snapshot is
  2026-06-21.

The main evaluation periods are:

| Period | Samples | Baseline hourly MAE |
|---|---:|---:|
| 2026-06-01..2026-08-23 | 1,021 | 0.31784 kWh |
| 2026-07-01..2026-08-23 | 637 | 0.32199 kWh |
| 2026-08-01..2026-08-23 | 264 | 0.32231 kWh |
| 2026-08-10..2026-08-23 | 156 | 0.34658 kWh |

The simulations are walk-forward. A target prediction uses only strictly earlier
calendar dates.

## 3. Diagnostic A: is the baseline error a stable bias?

Define the residual as:

```text
residual = actual_pv_kwh - forecast_pv_kwh
```

Results:

| Period | Mean residual | Median residual | abs(mean residual) / MAE |
|---|---:|---:|---:|
| All | +0.05645 kWh | -0.00530 kWh | 17.76% |
| July+ | +0.02695 kWh | -0.00460 kWh | 8.37% |
| August | +0.11882 kWh | +0.06565 kWh | 36.87% |
| Recent 14d | +0.10865 kWh | +0.02990 kWh | 31.35% |

Interpretation:

- Across the complete period, a single persistent additive bias explains only a
  small fraction of absolute error.
- The sign changes over time. July+ is close to unbiased, while August has a
  meaningful positive residual, i.e. actual PV tends to exceed the saved
  forecast.
- Therefore a static correction learned over the full 45-day history is exposed
  to concept / regime drift.

The August bias is real enough to motivate an adaptive filter, but its existence
alone does not prove that the next hour's residual is predictable.

## 4. Diagnostic B: does the residual repeat from day to day?

For each evaluable target, the additive residual was paired with the residual at
the same clock hour on preceding days.

Lag-1 Pearson correlation:

| Period | Same-hour lag-1 residual correlation |
|---|---:|
| All | 0.166 |
| August | 0.064 |
| Recent 14d | 0.068 |

These are weak correlations. In particular, the recent regime has almost no
one-day same-hour persistence.

This matters because any residual post-processor ultimately depends on some
repeatability of the forecast error. If yesterday's / recent residual contains
little information about today's residual, more complicated matching cannot
manufacture that information.

## 5. Diagnostic C: does the current weather-vector analog predict the target residual?

The existing review's hard matching rule was replayed:

- same clock hour,
- same coarse weather class,
- target and candidate shortwave > 0,
- candidate shortwave within 70%-130% of target,
- at least two candidates.

For each hit, the median candidate residual was compared directly with the
actual target residual.

| Period / lookback | Coverage | Additive residual r | Sign agreement | Log-residual r |
|---|---:|---:|---:|---:|
| All / 45d | 57.00% | 0.253 | 64.95% | 0.479 |
| August / 45d | 88.64% | **0.004** | 55.98% | 0.204 |
| Recent 14d / 45d | 87.18% | **-0.056** | 55.15% | 0.263 |
| August / 14d | 70.08% | **-0.116** | 52.43% | 0.014 |
| Recent 14d / 14d | 67.31% | **-0.115** | 48.57% | 0.155 |

This is the strongest root-cause finding in this experiment.

The matching rule has high recent coverage, so recent degradation is not caused
primarily by a lack of hits. Instead, the residual selected by those hits has
almost no additive predictive relationship with the realized August residual.
Shortening the lookback to 14 days does not restore the signal; it makes the
additive relationship negative.

## 6. Diagnostic D: can analog redesign recover the signal?

`diagnose_hourly_pv_correction_limits.py` compares 121 arms:

- lookback: 7 / 14 / 21 / 30 / 45 days,
- current hard match,
- continuous shortwave distance,
- continuous shortwave distance plus weather-class penalty,
- additive kWh residual,
- log(actual/forecast) residual,
- no recency decay / 7-day half-life / 14-day half-life,
- normalized-residual variants with same-hour or ±1-hour pooling,
- plus a production-shaped 45-day reference arm using count and local-variance
  shrinkage.

The best complete-period arm is the production-shaped reference:

| Period | Baseline | Production-like 45d | Relative change |
|---|---:|---:|---:|
| All | 0.31784 | 0.30297 | **+4.68%** |
| July+ | 0.32199 | 0.29916 | **+7.09%** |
| August | 0.32231 | 0.32451 | **-0.68%** |
| Recent 14d | 0.34658 | 0.35139 | **-1.39%** |

The best non-production-shaped analog redesign improves the complete period by
less than 1% and materially degrades the recent windows. No tested analog
redesign solves the August / recent-14-day problem.

This is evidence against spending the next implementation cycle merely tuning
weather-class boundaries, shortwave thresholds, or residual scaling.

## 7. Diagnostic E: remove weather similarity entirely

A second control experiment asks whether a broad recent regime bias is easier to
learn than a weather-matched residual.

`diagnose_hourly_pv_regime_bias.py` compares 48 same-hour filters:

- no weather-vector gate,
- lookback: 3 / 7 / 14 / 21 / 30 / 45 days,
- additive and log-ratio residuals,
- no decay / 3-day / 7-day / 14-day half-life.

The strongest complete-period result is:

```text
same_hour_bias_lb45_additive_hl7d
```

| Period | Baseline | Same-hour bias | Relative change |
|---|---:|---:|---:|
| All | 0.31784 | 0.29017 | **+8.71%** |
| July+ | 0.32199 | 0.29607 | **+8.05%** |
| August | 0.32231 | 0.32776 | **-1.69%** |
| Recent 14d | 0.34658 | 0.34883 | **-0.65%** |

The best arm for the recent-14-day MAE is:

```text
same_hour_bias_lb45_additive_hl14d
```

but it is still worse than baseline:

```text
baseline       0.34658 kWh
candidate      0.34833 kWh
relative       -0.50%
```

None of the 48 same-hour bias variants beats baseline in the recent 14-day
window.

This control is important because it shows that the August failure is not just a
bad weather-vector similarity definition. Even after removing the analog gate,
the recent residual is not stable enough for these simple historical filters to
produce a MAE gain.

## 8. Root-cause conclusion

The current evidence supports the following ordering of causes.

### Primary: recent residual predictability is weak

The forecast residual is not sufficiently repeatable in the recent regime:

- same-hour lag-1 r is only about 0.06-0.07,
- current 45-day analog additive residual r is approximately zero in August,
- current 14-day analog additive residual r is negative in August and recent
  14d,
- 121 analog variants do not restore recent MAE improvement,
- 48 weather-independent same-hour bias filters also fail to beat recent
  baseline.

The dominant limitation is therefore not simply the magnitude or sign of the
current correction. The historical residual being supplied to the correction
has low recent predictive information.

### Secondary: regime drift

The mean residual changes materially between July and August. A long static
history mixes regimes. This explains why historical correction can look useful
in the aggregate while failing prospectively in August.

### Secondary: early shortwave data quality

363 positive-forecast rows have non-positive saved forecast shortwave and the
first positive shortwave in the evaluable snapshot is 2026-06-21. This reduces
and distorts early analog history.

It does **not** explain the August failure, because August analog coverage is
already high.

## 9. What this experiment cannot prove

A plausible physical explanation is that much of the remaining PV error comes
from meteorological forecast error, especially cloud / irradiance error whose
sign and timing are not repeatable from one historical analog day to another.

The frozen dataset cannot prove that decomposition because it contains forecast
shortwave but **does not contain independently observed realized irradiance**.
It also does not prove the original forecast issue time / lead time because the
existing forecast store can overwrite forecast vintages.

Therefore this document does not claim "weather forecast error is X% of the
problem." That requires additional evidence.

## 10. Recommended next simulation

Do not deploy a more complicated analog model yet.

The next offline experiment should test an **adaptive safety gate / online model
selector**:

1. Keep baseline as an explicit zero-correction arm.
2. Keep the production-shaped residual correction as one candidate.
3. Keep the simple same-hour bias filter as a second candidate.
4. For each target day, select / enable a correction only from strictly earlier
   out-of-sample performance over a trailing window.
5. Fall back to baseline when the correction does not beat baseline by a
   predetermined margin.
6. Evaluate all-period gain and August / recent-window non-inferiority together.

This does not create missing predictive signal. Its purpose is to preserve gains
when a correction is demonstrably useful and stop applying it when the regime
changes.

If that adaptive gate cannot retain a useful aggregate gain without recent
regression, residual post-processing should be treated as a minor optimization,
not the main route to forecast improvement.

## 11. Data needed to identify the physical forecast error

Before a production algorithm decision, add an immutable forecast-vintage data
contract so future analysis can compare what was actually known at decision
time.

Required fields include:

- forecast issued timestamp,
- target timestamp,
- lead time,
- weather provider / model provenance,
- PV model version,
- forecast shortwave / cloud fields,
- feature-quality flags.

Where available, also retain realized / analysis irradiance or a suitable
post-event weather observation. That enables decomposition between:

```text
weather forecast error
    vs.
PV conversion / calibration error
    vs.
site-specific effects
```

Without this, further statistical tuning can improve MAE but cannot reliably
identify the physical source of the error.

## 12. Reproduction

The diagnostics are intentionally standard-library-only:

```powershell
python -m py_compile scripts/diagnose_hourly_pv_correction_limits.py
python scripts/diagnose_hourly_pv_correction_limits.py `
  --output artifacts/analysis/hourly_pv_correction_diagnostic.json

python -m py_compile scripts/diagnose_hourly_pv_regime_bias.py
python scripts/diagnose_hourly_pv_regime_bias.py `
  --output artifacts/analysis/hourly_pv_regime_bias_diagnostic.json
```

The dedicated `pv-correction-diagnostic` GitHub Actions workflow runs both
against the frozen snapshot. It does not contact live services.
