# Priority and Implementation Plan

## Phase 1: immediate correctness fixes

### Step 1

Correct historical hourly aggregation.

Targets:

- `_historical_hourly_profile()`
- Overnight `hourly_load_forecast_kwh`

### Step 2

Make the overnight discharge guard cap optional.

Targets:

- `energy_model_main.py`
- `.env.example`
- Deployment environment configuration
- Tests

### Step 3

Run focused tests and validate against the latest real KP-NET CSV.

## Phase 2: weather reliability

1. Add weather retrieval diagnostics.
2. Preserve explicit fallback reasons.
3. Split archive requests into smaller periods.
4. Add historical weather caching.
5. Add join-quality eligibility checks.

## Phase 3: temperature model safety

1. Add a high-temperature no-reduction floor.
2. Add diagnostics showing whether the floor was applied.
3. Backtest the safety floor.
4. Redesign correlated temperature features.
5. Add time-block or hourly temperature response.

## Immediate implementation order

1. Add or update tests for hourly aggregation.
2. Fix date-and-hour aggregation.
3. Fix overnight hourly forecast aggregation.
4. Run focused aggregation tests.
5. Add optional-cap behavior and diagnostics.
6. Change example and deployment configuration from 2.0 to 0.
7. Run overnight guard tests.
8. Compare before-and-after results using the latest CSV.
9. Run broader regression tests.

## Out of scope for Phase 1

Do not redesign the following unless required by a directly related failing test:

- Full consumption forecasting model
- Fallback rolling-average algorithm
- PV forecasting
- Occupancy adjustment
- Battery optimization
- Weather retrieval architecture
- Temperature feature model
