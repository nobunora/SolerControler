from __future__ import annotations

import math
import os
import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from app.forecasting.correction import fetch_hourly_weather
from app.kpnet.client import KpNetUnknownWriteError
from app.kpnet.workflow import KpNetClient
from app.forecasting.pv_array import (
    PVArrayConfig,
    fetch_open_meteo_hourly,
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
        fetch_open_meteo_hourly(
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

    rows = fetch_open_meteo_hourly(
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
        fetch_open_meteo_hourly(
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

    monkeypatch.setattr("app.forecasting.correction.requests.get", timeout_get)

    result = fetch_hourly_weather(
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


def test_kpnet_write_timeout_emits_secret_free_unknown_write_telemetry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    class Session:
        def post(self, *_args, **_kwargs):
            raise requests.Timeout("password=must-not-appear")

    client = object.__new__(KpNetClient)
    client.cfg = SimpleNamespace(base_url="https://ctrl.kp-net.com/", timeout_sec=17)
    client.session = Session()
    client.operation_id = "test-operation"
    client.deadline_monotonic = None

    with pytest.raises(requests.Timeout):
        client._post("remotesetting/pcssetting/write/request", data={"_csrf": "secret"})

    event = json.loads(capsys.readouterr().out)
    assert event["message"] == "kpnet-http"
    assert event["stage"] == "post:remotesetting/pcssetting/write/request"
    assert event["classification"] == "unknown_write"
    assert event["exception_class"] == "Timeout"
    assert "secret" not in json.dumps(event)


@pytest.mark.parametrize(
    ("path", "classification"),
    [
        ("remotesetting/pcssetting/read/response", "failed"),
        ("remotesetting/pcssetting/write/response", "unknown_write"),
    ],
)
def test_kpnet_poll_http_errors_are_classified_without_request_data(
    path: str, classification: str, capsys: pytest.CaptureFixture[str]
) -> None:
    class Session:
        def post(self, *_args, **_kwargs):
            raise requests.HTTPError("HTTP 500")

    client = object.__new__(KpNetClient)
    client.cfg = SimpleNamespace(base_url="https://ctrl.kp-net.com/", timeout_sec=17)
    client.session = Session()
    client.operation_id = "test-operation"
    client.deadline_monotonic = None

    with pytest.raises(requests.HTTPError):
        client._poll_json(path, {"value": "secret"}, headers={})

    event = json.loads(capsys.readouterr().out)
    assert event["classification"] == classification
    assert "secret" not in json.dumps(event)


def test_kpnet_provider_pending_status_is_logged_then_times_out(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = object.__new__(KpNetClient)
    client.operation_id = "test-operation"
    client.deadline_monotonic = None
    client._post = lambda *_args, **_kwargs: _Response({"status": 0})
    times = iter([0.0, 0.0, 1.0])
    monkeypatch.setattr("app.kpnet.client.time.time", lambda: next(times))
    monkeypatch.setattr("app.kpnet.client.time.sleep", lambda _seconds: None)

    with pytest.raises(TimeoutError, match="Polling timeout"):
        client._poll_json("remotesetting/pcssetting/write/response", {}, headers={}, max_wait_sec=0.5)

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-2]["provider_status"] == 0
    assert events[-2]["classification"] == "pending"
    assert events[-1]["stage"].endswith(":terminal")
    assert events[-1]["classification"] == "unknown_write"
    assert events[-1]["exception_class"] == "TimeoutError"


def test_kpnet_poll_invalid_json_has_terminal_telemetry(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = object.__new__(KpNetClient)
    client.operation_id = "test-operation"
    client.deadline_monotonic = None
    client._post = lambda *_args, **_kwargs: _Response({}, json_error=ValueError("login HTML"))

    with pytest.raises(RuntimeError, match="invalid JSON"):
        client._poll_json("remotesetting/pcssetting/write/response", {}, headers={})

    event = json.loads(capsys.readouterr().out)
    assert event["stage"].endswith(":terminal")
    assert event["classification"] == "unknown_write"
    assert event["exception_class"] == "RuntimeError"


def test_kpnet_uncertain_write_does_not_issue_a_second_set() -> None:
    client = object.__new__(KpNetClient)
    client.cfg = SimpleNamespace(base_url="https://ctrl.kp-net.com/", timeout_sec=17)
    client.operation_id = "test-operation"
    client._extract_form_data = lambda _html: ({"_csrf": "secret"}, "secret")
    calls: list[str] = []

    def fail_once(path: str, **_kwargs):
        calls.append(path)
        raise requests.Timeout("response lost")

    client._post = fail_once

    with pytest.raises(KpNetUnknownWriteError):
        client.write_setting("unused")

    assert calls == ["remotesetting/pcssetting/write/request"]


def test_production_slot_modules_do_not_import_playwright() -> None:
    root = Path(__file__).resolve().parents[1]
    for relative_path in ("app/runtime/cloud_job.py", "app/runtime/slot_orchestration.py", "app/kpnet/workflow.py"):
        assert "playwright" not in (root / relative_path).read_text(encoding="utf-8").lower()


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
