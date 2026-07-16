# Test Plan

## 1. Hourly aggregation tests

### Complete hour

Input:

- 00:00 = 1.59 kWh
- 00:30 = 1.69 kWh

Expected hourly value:

`3.28 kWh`

### Multiple historical days

Day 1:

- 00:00 = 1.0 kWh
- 00:30 = 1.2 kWh
- Hour total = 2.2 kWh

Day 2:

- 00:00 = 1.4 kWh
- 00:30 = 1.6 kWh
- Hour total = 3.0 kWh

Expected historical hourly mean:

`2.6 kWh`

The result must not be `1.3 kWh`.

### Incomplete hour

Input:

- 00:00 = 1.0 kWh
- 00:30 record missing

Expected behavior:

- Exclude the incomplete hour from the complete-hour historical mean.
- Record one missing interval in diagnostics.
- Do not replace the missing interval with zero.

### Valid zero consumption

Two valid zero records must produce:

`0.0 kWh`

They must not be treated as missing data.

## 2. Overnight discharge guard tests

### Disabled cap

Set:

`OVERNIGHT_DISCHARGE_GUARD_CAP_KWH=0`

Raw historical estimate:

`14.8 kWh`

Expected result:

- Final estimate = 14.8 kWh
- `cap_applied = false`
- `uncapped_expected_kwh = 14.8`

### Positive cap

Set:

`OVERNIGHT_DISCHARGE_GUARD_CAP_KWH=10`

Raw historical estimate:

`14.8 kWh`

Expected result:

- Final estimate = 10.0 kWh
- `cap_applied = true`
- `uncapped_expected_kwh = 14.8`
- `cap_kwh = 10.0`

### Existing behavior to preserve

Retain coverage for:

- Lookback days
- Minimum sample days
- Percentile selection
- Remaining-slot matching
- Past-cutoff behavior
- Floor behavior

## 3. Real CSV validation

Use the latest downloaded KP-NET CSV.

Compare before and after:

- Hourly overnight load forecast
- Remaining overnight total estimate
- Morning SOC requirement
- Simulated SOC depletion time
- Forecast versus actual load by hour

At minimum, verify that a complete hour equals the sum of its two 30-minute records.

## 4. Weather reliability tests

Future phase tests:

- Timeout
- Connection error
- HTTP error
- Invalid JSON
- Partial period response
- Missing dates
- Date-join mismatch
- Cached partial recovery

## 5. Temperature correction tests

Future phase tests:

- High-temperature correction cannot reduce the forecast when the safety floor is active.
- Moderate-temperature behavior remains unchanged.
- Diagnostics identify when the floor is applied.
- Low effective sample count selects the prior or suppresses data correction.

## 6. Regression checks

Run the smallest relevant tests first.

Then run:

- Energy model tests
- Forecast correction tests
- Consumption forecast tests
- Any SOC planning tests affected by overnight load

Report every test command and its result.
