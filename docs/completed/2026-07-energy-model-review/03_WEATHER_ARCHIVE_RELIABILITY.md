# Weather Archive Reliability

## Verified current behavior

File: `energy_model_main.py`

Function: `_archive_weather_rows()`

The function requests the full historical period from the Open-Meteo Archive API in one call.

Current failure handling:

    except Exception:
        return []

## Problem

The current code cannot distinguish between:

- Timeout
- Connection failure
- HTTP failure
- Invalid JSON
- Partial API response
- Missing dates
- Empty history
- Date-join failure

This can silently produce:

`sample_count = 0`

and force:

`fallback_rolling_average`

## Phase A: diagnostics

Add structured diagnostics first.

Record:

- Requested start date
- Requested end date
- Requested day count
- Received day count
- Missing dates
- HTTP status when available
- Exception type
- Exception message
- Consumption-history day count
- Joined training day count
- Fallback reason

Do not log secrets or authentication values.

## Phase B: structured result

Replace an unqualified list return with a result object.

Suggested structure:

    @dataclass
    class WeatherHistoryFetchResult:
        rows: list[dict[str, object]]
        requested_dates: list[str]
        received_dates: list[str]
        missing_dates: list[str]
        errors: list[dict[str, str]]

A compatibility wrapper may be used during migration.

## Phase C: partial retrieval

Split large historical ranges into smaller requests, such as 7 or 14 days.

Benefits:

- Partial success remains usable.
- Retries are limited to failed periods.
- Missing dates become identifiable.

## Phase D: cache

Historical weather should be cached because past daily data changes infrequently.

Suggested flow:

1. Load cached historical days.
2. Request only missing dates.
3. Merge successful responses.
4. Preserve missing-date diagnostics.
5. Continue only when join quality is sufficient.

## Model eligibility

Model selection should consider:

- Joined sample count
- Join coverage ratio
- Missing-date concentration
- Minimum history requirement

Do not treat API failure and insufficient historical data as the same fallback reason.
