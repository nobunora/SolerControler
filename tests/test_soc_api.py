from datetime import datetime, timedelta
import copy
import json
from types import SimpleNamespace

import pytest

from app.kpnet.soc_api import JST, SocApiError, parse_soc_measurement
from app.kpnet import soc_api


REQUESTED = datetime(2026, 10, 9, 23, 43, 44, tzinfo=JST)
RECEIVED = datetime(2026, 10, 9, 23, 43, 56, tzinfo=JST)


def measurement(value=14, timestamp="2026-10-09T23:43:49"):
    return {"mePrt": [{"dtMe": timestamp, "infoSto": [{"soc": value}]}]}


@pytest.mark.parametrize("value", [0, 14, 100])
def test_api_measurement_preserves_soc_and_device_timestamp(value):
    sample = parse_soc_measurement(measurement(value), requested_at=REQUESTED, retrieved_at=RECEIVED)
    assert sample is not None
    assert sample.value_percent == value
    assert sample.measured_at == datetime(2026, 10, 9, 23, 43, 49, tzinfo=JST)
    assert sample.retrieved_at == RECEIVED


def test_empty_measurement_is_pending_not_zero():
    assert parse_soc_measurement({"mePrt": []}, requested_at=REQUESTED, retrieved_at=RECEIVED) is None


@pytest.mark.parametrize("value", [None, True, False, "14", -1, 101, float("nan"), float("inf")])
def test_invalid_soc_is_rejected(value):
    with pytest.raises(SocApiError, match="percentage"):
        parse_soc_measurement(measurement(value), requested_at=REQUESTED, retrieved_at=RECEIVED)


@pytest.mark.parametrize("timestamp", [None, "not-a-date", "2026-10-09T23:38:49", "2026-10-10T23:43:49"])
def test_missing_invalid_stale_or_future_timestamp_is_rejected(timestamp):
    with pytest.raises(SocApiError):
        parse_soc_measurement(measurement(timestamp=timestamp), requested_at=REQUESTED, retrieved_at=RECEIVED)


def test_slow_response_cannot_accept_old_measurement():
    with pytest.raises(SocApiError, match="stale"):
        parse_soc_measurement(measurement(), requested_at=REQUESTED, retrieved_at=RECEIVED + timedelta(minutes=2))


@pytest.mark.parametrize("data", [
    {}, {"mePrt": None}, {"mePrt": [None]},
    {"mePrt": [{}, {}]}, {"mePrt": [{"infoSto": []}]},
    {"mePrt": [{"infoSto": [{"soc": 14}, {"soc": 80}]}]},
])
def test_missing_or_ambiguous_battery_is_rejected(data):
    with pytest.raises(SocApiError):
        parse_soc_measurement(data, requested_at=REQUESTED, retrieved_at=RECEIVED)


def test_exception_does_not_echo_provider_payload():
    with pytest.raises(SocApiError) as exc:
        parse_soc_measurement(measurement(timestamp="private-provider-text"), requested_at=REQUESTED, retrieved_at=RECEIVED)
    assert "private-provider-text" not in str(exc.value)


@pytest.fixture
def api_harness(monkeypatch):
    credentials = {"authorization": "Basic test-only", "api_key": "private-api-key",
                   "user": {"id": "private-user", "password": "private-user-password"},
                   "gateway": {"id": "private-gateway", "password": "private-gateway-password"}}
    monkeypatch.setenv("KP_SOC_API_CREDENTIALS_JSON", json.dumps(credentials))
    seconds = [0.0]
    monkeypatch.setattr(soc_api.time, "monotonic", lambda: seconds[0])
    monkeypatch.setattr(soc_api.time, "sleep", lambda delay: seconds.__setitem__(0, seconds[0] + delay))

    class Clock(datetime):
        @classmethod
        def now(cls, zone):
            return REQUESTED + timedelta(seconds=seconds[0])

    monkeypatch.setattr(soc_api, "datetime", Clock)
    completed = {"endCd": "00000", "errPrt": [], **measurement(timestamp="2026-10-09T23:43:44")}
    responses = [{"endCd": "20001", "errPrt": [], "mePrt": [], "identifiId": "private-correlation"}, completed]
    calls = []

    class Session:
        def __init__(self):
            self.headers = {}
            self.closed = False

        def post(self, url, **kwargs):
            calls.append((url, copy.deepcopy(kwargs)))
            value = responses.pop(0)
            if isinstance(value, Exception):
                raise value
            value.setdefault("seqNo", kwargs["json"]["data"]["seqNo"])
            return SimpleNamespace(status_code=200, content=b"{}", json=lambda: {"data": value})

        def close(self):
            self.closed = True

    session = Session()
    monkeypatch.setattr(soc_api.requests, "Session", lambda: session)
    return responses, calls, seconds, session


def test_refresh_polls_gateway_and_never_reads_cloud_cache_or_html(api_harness, capsys):
    _, calls, _, session = api_harness
    client = soc_api.KpNetSocClient(deadline_monotonic=60)
    sample = client.read_soc()
    client.close()
    assert sample.value_percent == 14
    assert len(calls) == 2
    assert all(url == "https://api.kp-net.com/gw/get-measurement-data/v2" for url, _ in calls)
    initial, poll = (item[1]["json"]["data"] for item in calls)
    assert initial["measureFlg"] == poll["measureFlg"] == "GW"
    assert initial["oldDataDt"] is None
    assert initial["identifiId"] is None
    assert poll["identifiId"] == "private-correlation"
    assert initial["reqDate"] == poll["reqDate"]
    assert initial["seqNo"] == poll["seqNo"]
    assert all(kwargs["allow_redirects"] is False for _, kwargs in calls)
    assert session.closed
    assert "private-" not in capsys.readouterr().out


@pytest.mark.parametrize("change", [
    {"endCd": "20000"}, {"endCd": "99999"}, {"seqNo": "wrong"},
    {"errPrt": [{"password": "private-error"}]}, {"identifiId": None},
])
def test_pending_error_or_wrong_correlation_fails_closed(api_harness, change):
    responses, _, _, _ = api_harness
    responses[0].update(change)
    with pytest.raises(SocApiError) as exc:
        soc_api.KpNetSocClient(deadline_monotonic=60).read_soc()
    assert "private-error" not in str(exc.value)


def test_timeout_does_not_start_another_poll(api_harness):
    _, calls, _, _ = api_harness
    with pytest.raises(SocApiError, match="deadline"):
        soc_api.KpNetSocClient(deadline_monotonic=2).read_soc()
    assert len(calls) == 1
    assert calls[0][1]["timeout"] <= 1


def test_expired_deadline_does_not_make_a_request(api_harness):
    _, calls, _, _ = api_harness
    with pytest.raises(SocApiError, match="deadline"):
        soc_api.KpNetSocClient(deadline_monotonic=0).read_soc()
    assert calls == []


def test_transport_errors_never_expose_credentials(api_harness):
    responses, _, _, _ = api_harness
    responses[:] = [soc_api.requests.ConnectionError("private-password")]
    with pytest.raises(SocApiError) as exc:
        soc_api.KpNetSocClient().read_soc()
    assert "private-password" not in str(exc.value)


def test_runtime_preserves_measurement_timestamp_and_closes_on_failure(api_harness, monkeypatch):
    from app.runtime.soc_reading import latest_realtime_soc_reading
    responses, _, _, session = api_harness
    responses[:] = [soc_api.requests.ConnectionError("private-password")]
    with pytest.raises(SocApiError):
        latest_realtime_soc_reading(deadline_monotonic=60)
    assert session.closed


@pytest.mark.parametrize("credentials", ["", "{}", "[]", "private-invalid-json"])
def test_missing_credentials_are_redacted(monkeypatch, credentials):
    monkeypatch.setenv("KP_SOC_API_CREDENTIALS_JSON", credentials)
    with pytest.raises(SocApiError) as exc:
        soc_api.KpNetSocClient()
    assert "private-invalid-json" not in str(exc.value)
