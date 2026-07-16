# Hourly Load Aggregation

## Verified source locations

File: `energy_model_main.py`

Relevant functions:

- `_historical_hourly_profile()`
- `_build_hourly_load_forecast()`
- `_estimate_remaining_overnight_load_kwh()`

## Verified input format

The latest KP-NET CSV uses 30-minute timestamps and reports interval energy in kWh.

Example:

- 00:00 = 1.384 kWh
- 00:30 = 1.152 kWh
- Correct hourly energy = 2.536 kWh

## Current problem

`_historical_hourly_profile()` groups records by `dt.hour` and divides accumulated energy by the number of 30-minute records.

This returns average 30-minute interval energy, not average hourly energy.

For daytime profiles, later normalization often cancels the common factor when all hours contain two complete intervals.

For overnight hours, values are used directly without normalization. The hourly forecast can therefore be approximately half of the actual hourly energy.

The same problem exists in `hourly_load_forecast_kwh` returned by `_estimate_remaining_overnight_load_kwh()`.

## Required correction

Aggregate in this order:

1. Group records by date and hour.
2. Sum the 00-minute and 30-minute interval values.
3. Produce one hourly kWh value for each date and hour.
4. Average the hourly values across historical days.

Conceptual implementation:

    by_day_hour: dict[tuple[date, int], float] = {}

    for row in rows:
        dt = row.get("dt")
        if not isinstance(dt, datetime):
            continue

        value = max(0.0, float(row.get(key, 0.0) or 0.0))
        group = (dt.date(), dt.hour)
        by_day_hour[group] = by_day_hour.get(group, 0.0) + value

    values_by_hour: dict[int, list[float]] = {}

    for (_, hour), hourly_kwh in by_day_hour.items():
        values_by_hour.setdefault(hour, []).append(hourly_kwh)

Then calculate the historical mean from these hourly totals.

## Missing interval policy

Do not fill missing intervals with zero.

Recommended behavior:

- Track interval count for every date and hour.
- Normally require two records per hour.
- Exclude incomplete hours from historical averaging.
- Record incomplete-hour diagnostics.
- Treat two valid zero-consumption records as complete data.

## Required scope

Apply the corrected aggregation to:

- `_historical_hourly_profile()`
- Overnight `hourly_load_forecast_kwh`

## Do not change

Do not alter `_historical_profile()` daily aggregation.

The following existing logic is already correct because it sums all 30-minute interval values:

    d["day_load"] += load
    d["morning_load"] += load

Do not alter:

- Daytime forecast totals
- Morning forecast totals
- `_normalize_profile()`
- PV aggregation
