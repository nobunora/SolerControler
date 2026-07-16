# Overnight Discharge Guard

## Verified current behavior

File: `energy_model_main.py`

Function: `_estimate_remaining_overnight_load_kwh()`

The function already estimates remaining overnight consumption dynamically using:

- A historical lookback window
- Matching remaining overnight time slots
- A configured percentile
- A floor
- A cap

The historical daily samples are correctly summed from 30-minute kWh records.

## Actual production problem

The Python default is:

`OVERNIGHT_DISCHARGE_GUARD_CAP_KWH=12.0`

Production configuration and `.env.example` override it to:

`OVERNIGHT_DISCHARGE_GUARD_CAP_KWH=2.0`

The configuration comment describes a 04:30-to-07:00 remaining-load limit, but the implementation estimates from the latest available timestamp until 07:00.

When planning runs around 21:00, most of the night may remain. A fixed 2.0 kWh cap is therefore inconsistent with the implemented time window.

## Required correction

Allow the cap to be disabled.

Recommended convention:

`OVERNIGHT_DISCHARGE_GUARD_CAP_KWH=0`

Meaning:

`0 = no fixed cap`

Recommended implementation:

    raw_estimate = max(floor_kwh, estimate)

    if cap_kwh > 0.0:
        estimate = min(cap_kwh, raw_estimate)
        cap_applied = estimate < raw_estimate
    else:
        estimate = raw_estimate
        cap_applied = False

## Required diagnostics

Return and persist:

    {
        "uncapped_expected_kwh": round(raw_estimate, 4),
        "cap_kwh": cap_kwh,
        "cap_applied": cap_applied,
    }

## Physical constraints

Do not use a fixed consumption cap as the battery safety boundary.

Apply physical limits in the SOC target calculation using:

- Effective battery capacity
- Current SOC
- Maximum target SOC
- Charge and discharge efficiency
- Reserve SOC

## Implementation order

Complete the hourly profile aggregation fix before validating this change.

The daily `expected_kwh` estimate is mostly correct already, but the hourly overnight shape is currently understated.
