# Index

## Immediate fixes

1. [Hourly load aggregation](01_HOURLY_LOAD_AGGREGATION.md)
2. [Overnight discharge guard](02_OVERNIGHT_DISCHARGE_GUARD.md)

## Design proposals

3. [Weather archive reliability](03_WEATHER_ARCHIVE_RELIABILITY.md)
4. [Temperature correction](04_TEMPERATURE_CORRECTION.md)

## Execution guidance

5. [Priority and implementation plan](05_PRIORITY_AND_IMPLEMENTATION_PLAN.md)
6. [Test plan](06_TEST_PLAN.md)
7. [Codex task instructions](07_CODEX_TASK.md)

## Verified facts

- KP-NET CSV values are 30-minute interval energy values in kWh.
- Daily consumption totals already sum the 30-minute records correctly.
- The hourly-profile bug mainly affects overnight hourly forecasts.
- The Python default overnight cap is 12.0 kWh.
- Production deployment and `.env.example` override the cap to 2.0 kWh.
- Weather archive exceptions are silently converted to an empty list.
- Major high-temperature regression coefficients can currently become negative.
