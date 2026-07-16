# Temperature Correction

## Verified current behavior

File: `app/forecast_correction.py`

Relevant functions:

- `_temperature_feature_vector()`
- `_temperature_prior_log_multiplier()`
- `_evening_temperature_correction()`
- `_temperature_correction_hours()`

Default correction range:

`LOAD_TEMPERATURE_CORRECTION_HOURS=0-23`

Despite the function name, the correction is applied across the full day by default.

## Current feature order

1. Intercept
2. Cooling degree hours above 28 C
3. Evening temperature EWMA
4. Night minimum temperature
5. Cooling degree hours above 24 C
6. Cooling degree hours above 32 C
7. Hours above 35 C
8. Evening temperature above 30 C
9. Night minimum temperature above 26 C

## Verified coefficient behavior

After ridge regression, only coefficients from index 4 onward are forced non-negative.

Current logic:

    coefficients = [
        *coefficients[:4],
        *(max(0.0, value) for value in coefficients[4:]),
    ]

Therefore these major heat-related coefficients may become negative:

- Cooling degree hours above 28 C
- Evening temperature EWMA
- Night minimum temperature

The final multiplier can also fall below 1.0 because the blended result is multiplied by `residual_median`.

## Short-term safety proposal

When a configured high-temperature condition is active, the temperature correction layer should not reduce the load forecast.

Example:

    high_temperature = (
        cdh28 >= configured_cdh28_threshold
        or max_temp_c >= configured_max_temp_threshold
    )

    if high_temperature and multiplier < 1.0:
        multiplier = 1.0
        monotonic_floor_applied = True

This constraint applies only to the temperature correction layer.

Other correction layers may continue to operate normally.

## Medium-term redesign

Avoid highly correlated cumulative features such as CDH24, CDH28, and CDH32 in the same small-sample regression.

Use non-overlapping temperature bands, for example:

- 24 to 28 C degree-hours
- 28 to 32 C degree-hours
- Above 32 C degree-hours

Apply non-negative constraints only to physically justified cooling-load features.

## Confidence gate

When effective similar-temperature samples are insufficient:

- Prefer the temperature prior.
- Suppress the data regression.
- Record the reason.

Thresholds must be selected by backtesting rather than assumption.

## Time-shape limitation

The current correction applies one multiplier to all configured hours.

It cannot independently raise afternoon and evening load shape.

A future design should support hour-specific or time-block-specific temperature response.
