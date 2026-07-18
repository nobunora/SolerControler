from __future__ import annotations

import math
import os
from datetime import date
from types import SimpleNamespace

import pytest
import requests

from app.forecast_correction import _fetch_hourly_weather
from app.kpnet_workflow import KpNetClient
from app.pv_array_forecast import (
    PVArrayConfig,
    _fetch_hourly,
    build_pv_array_forecast,
    forecast_pv_arrays,
)


class _Response:
    def __init__(self, payload: object, *, json_error: ValueError | None = None) -> None:
        self.payload = payload
        self.json_error = json_error
        self.status_checked = False

    def raise_for_status(self) -> None:
        self.status_checked = True

    def json(self) -> object:
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class _HttpErrorResponse(_Response):
    def __init__(self, status_code: int) -> None:
        super().__init__({})
        self.status_code = status_code

    def raise_for_status(self) -> None:
        self.status_checked = True
        error = requests.HTTPError(f"HTTP {self.status_code}")
        error.response = self
        raise error


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_Response([]), "non-object JSON"),
        (_Response({}, json_error=ValueError("broken")), "invalid JSON"),
        (_Response({"hourly": []}), "hourly payload is not an object"),
    ],
)
def test_open_meteo_pv_boundary_rejects_malformed_success_payload(
    response: _Response,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        _fetch_hourly(
            endpoint="https://api.open-meteo.com/v1/forecast",
            lat=35.0,
            lon=139.0,
            timezone="Asia/Tokyo",
            start_date="2026-07-18",
            end_date="2026-07-18",
            array=PVArrayConfig("south", 0.0, 20.0, 1.0),
            http_get=lambda *args, **kwargs: response,
        )

    assert response.status_checked is True


def test_pv_forecast_falls_back_when_forecast_solar_returns_malformed_json(monkeypatch) -> None:
    monkeypatch.setenv("PV_ARRAY_PROVIDER", "forecast_solar,open_meteo")
    monkeypatch.setenv("PV_ARRAY_PROVIDER_MODE", "fallback")
    monkeypatch.setenv("PV_ARRAY_CALIBRATION_MIN_DAYS", "99")

    def fake_get(url: str, *, params=None, timeout: int):
        if "forecast.solar" in url:
            return _Response([])
        return _Response(
            {
                "hourly": {
                    "time": ["2026-07-18T12:00"],
                    "global_tilted_irradiance": [1000.0],
                    "temperature_2m": [25.0],
                }
            }
        )

    result = build_pv_array_forecast(
        arrays=[PVArrayConfig("south", 0.0, 20.0, 1.0, performance_ratio=1.0)],
        rows=[],
        target_date="2026-07-18",
        lat=35.0,
        lon=139.0,
        timezone="Asia/Tokyo",
        http_get=fake_get,
    )

    assert result is not None
    assert result["provider"] == "open_meteo"
    assert result["provider_attempts"][0]["provider"] == "forecast_solar"
    assert result["provider_attempts"][0]["ok"] is False
    assert "non-object JSON" in result["provider_attempts"][0]["error"]


def test_open_meteo_retries_one_transient_server_error(monkeypatch) -> None:
    monkeypatch.setenv("PV_HTTP_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("PV_HTTP_RETRY_DELAY_SECONDS", "0")
    responses = iter(
        [
            _HttpErrorResponse(503),
            _Response(
                {
                    "hourly": {
                        "time": ["2026-07-18T12:00"],
                        "global_tilted_irradiance": [1000.0],
                        "temperature_2m": [25.0],
                    }
                }
            ),
        ]
    )
    calls = 0

    def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        return next(responses)

    rows = _fetch_hourly(
        endpoint="https://api.open-meteo.com/v1/forecast",
        lat=35.0,
        lon=139.0,
        timezone="Asia/Tokyo",
        start_date="2026-07-18",
        end_date="2026-07-18",
        array=PVArrayConfig("south", 0.0, 20.0, 1.0),
        http_get=fake_get,
    )

    assert calls == 2
    assert rows[0]["gti_w_m2"] == 1000.0


def test_open_meteo_does_not_retry_client_error(monkeypatch) -> None:
    monkeypatch.setenv("PV_HTTP_MAX_ATTEMPTS", "3")
    calls = 0

    def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _HttpErrorResponse(400)

    with pytest.raises(RuntimeError, match="after 1 attempt"):
        _fetch_hourly(
            endpoint="https://api.open-meteo.com/v1/forecast",
            lat=35.0,
            lon=139.0,
            timezone="Asia/Tokyo",
            start_date="2026-07-18",
            end_date="2026-07-18",
            array=PVArrayConfig("south", 0.0, 20.0, 1.0),
            http_get=fake_get,
        )

    assert calls == 1


def test_forecast_correction_treats_timeout_as_unavailable_and_uses_bounded_timeout(monkeypatch) -> None:
    observed: dict[str, object] = {}

    def timeout_get(url: str, *, params: dict[str, object], timeout: int):
        observed.update(url=url, timeout=timeout, params=params)
        raise requests.Timeout("offline")

    monkeypatch.setattr("app.forecast_correction.requests.get", timeout_get)

    result = _fetch_hourly_weather(
        lat=35.0,
        lon=139.0,
        timezone="Asia/Tokyo",
        start_date="2026-07-18",
        end_date="2026-07-18",
        archive=False,
    )

    assert result == {}
    assert observed["url"] == "https://api.open-meteo.com/v1/forecast"
    assert observed["timeout"] == 20


def test_kpnet_http_wrapper_uses_configured_timeout_and_checks_status() -> None:
    response = _Response({})
    calls: list[tuple[str, int]] = []

    class Session:
        def get(self, url: str, *, timeout: int, **kwargs):
            calls.append((url, timeout))
            return response

    client = object.__new__(KpNetClient)
    client.cfg = SimpleNamespace(base_url="https://ctrl.kp-net.com/", timeout_sec=17)
    client.session = Session()

    assert client._get("login") is response
    assert calls == [("https://ctrl.kp-net.com/login", 17)]
    assert response.status_checked is True


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (_Response([]), "non-object JSON"),
        (_Response({}, json_error=ValueError("broken")), "invalid JSON"),
    ],
)
def test_kpnet_json_boundary_reports_provider_context(response: _Response, message: str) -> None:
    with pytest.raises(RuntimeError, match=f"KP-NET settings read .*{message}"):
        KpNetClient._json_object(response, operation="settings read")


@pytest.mark.external
@pytest.mark.skipif(
    os.getenv("RUN_EXTERNAL_SITE_TESTS", "").strip().lower() not in {"1", "true", "yes", "on"},
    reason="set RUN_EXTERNAL_SITE_TESTS=true to access the public Open-Meteo API",
)
def test_live_open_meteo_pv_forecast_contract() -> None:
    target_date = date.today().isoformat()
    result = forecast_pv_arrays(
        arrays=[PVArrayConfig("south", 0.0, 20.0, 1.0)],
        target_date=target_date,
        lat=35.6812,
        lon=139.7671,
        timezone="Asia/Tokyo",
    )

    assert result["provider"] == "open_meteo"
    assert result["target_date"] == target_date
    assert result["hourly"]
    assert math.isfinite(float(result["totals"]["total_kwh"]))
    assert float(result["totals"]["total_kwh"]) >= 0.0
