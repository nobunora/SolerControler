"""HTTP provider adapters for PV array forecasts.

This module owns retrying requests and validating provider JSON.  Domain
calibration and provider selection remain outside this I/O boundary.
"""

from __future__ import annotations

import os
import time
from typing import Any, Callable

import requests


HttpGet = Callable[..., Any]


def response_json_object(response: Any, *, provider: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f"{provider} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{provider} returned a non-object JSON payload")
    return payload


def http_get_with_retry(
    http_get: HttpGet,
    url: str,
    *,
    provider: str,
    **kwargs: Any,
) -> Any:
    """Request one provider endpoint, retrying only transient HTTP failures."""
    max_attempts = max(1, int(os.getenv("PV_HTTP_MAX_ATTEMPTS", "2")))
    retry_delay_seconds = max(0.0, float(os.getenv("PV_HTTP_RETRY_DELAY_SECONDS", "0.5")))
    for attempt in range(1, max_attempts + 1):
        try:
            response = http_get(url, **kwargs)
            response.raise_for_status()
            return response
        except (requests.Timeout, requests.ConnectionError, requests.HTTPError) as exc:
            status_code = getattr(getattr(exc, "response", None), "status_code", None)
            retryable = status_code is None or 500 <= int(status_code) < 600
            if attempt >= max_attempts or not retryable:
                raise RuntimeError(f"{provider} request failed after {attempt} attempt(s)") from exc
            if retry_delay_seconds > 0:
                time.sleep(retry_delay_seconds)
    raise AssertionError("unreachable")
