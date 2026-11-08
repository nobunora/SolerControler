"""Clear-ledger selection, read-only provenance and nonblocking fallback."""
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from google.api_core.exceptions import PreconditionFailed

from app.energy_plan import ideal_max_adapter as adapter
from app.forecasting.pv_ideal_max import HourlyPvObservation, IdealMaxArray, IdealMaxGeometry, calculate_ideal_max, ideal_max_base
from app.forecasting.pv_clear_ghi import clear_ghi_hour
from app.forecasting.pv_ideal_max_store import IdealMaxStore
from app.operations import pv_ideal_max as operations
from app.separated_runtime.plan_store import _check_provenance, _validate_incoming_provenance

NOW = datetime(2026, 10, 31, tzinfo=timezone.utc)
NEW_MODEL = "physical_pv_ideal_max_global_v1"


def test_local_forecast_reads_learned_state_without_issuing_or_writing(monkeypatch, tmp_path):
    geometry = IdealMaxGeometry(35, 135, "Asia/Tokyo", (IdealMaxArray(180, 20, 3, .8, 1),))
    monkeypatch.setattr(operations, "ideal_max_archive_prefix", lambda: "")
    monkeypatch.setattr(operations, "configured_ideal_max_geometry", lambda: geometry)
    with IdealMaxStore(tmp_path, geometry, initialize_at=NOW-timedelta(days=1)) as store:
        base = ideal_max_base(date(2026, 10, 31), 7, geometry)
        store.ingest(HourlyPvObservation(NOW-timedelta(hours=2), base*.75, base*.75, NOW), as_of=NOW)
        state = store.read_current()
        assert state.coefficient == pytest.approx(1.5)
        database = store.path
    before = database.read_bytes()
    pv, trace = operations.load_ideal_max_for_forecast(date(2026, 10, 31), as_of=NOW, artifacts_dir=tmp_path)
    assert tuple(pv.values()) == calculate_ideal_max(date(2026, 10, 31), geometry, state, as_of=NOW)
    assert database.read_bytes() == before
    assert trace["revision"] == state.revision
    assert trace["calibration_pack"]["coefficient"] == state.coefficient
    assert trace["weather_adjustment_applied"] is False


def context(tmp_path, weather_factor=1.0):
    start = datetime(2026, 10, 31, tzinfo=ZoneInfo("Asia/Tokyo"))
    rows = [{"time": (start + timedelta(hours=h+1)).isoformat(),
             "shortwave_radiation_w_m2": weather_factor * 1000 * clear_ghi_hour(
                 start.date(), h, latitude=35, longitude=135, timezone="Asia/Tokyo")}
            for h in range(24)]
    return SimpleNamespace(target_date="2026-10-31", rows=[], consumption_rows=[], billing_rows=None,
        config=SimpleNamespace(artifacts_dir=tmp_path, latitude=35, longitude=135, timezone="Asia/Tokyo"),
        forecast={"weather_code": 95, "pv_radiation_rows": rows,
                  "pv_forecast_retrieved_at": (NOW-timedelta(hours=1)).isoformat(),
                  "pv_forecast_input_sha256": "b"*64, "pv_provider_issued_at": None})


@pytest.mark.parametrize("weather_factor", [0.0, 0.5, 1.0, 1.5])
def test_adapter_applies_weather_without_changing_capability_or_load(monkeypatch, tmp_path, weather_factor):
    pv = {h: 2.0 if 8 <= h < 16 else 0.0 for h in range(24)}
    trace = {"model_id":NEW_MODEL, "basis_version":"test", "calibration_sha256":"a"*64,
             "coefficient":1.5, "revision":7, "retrieved_at":NOW.isoformat()}
    monkeypatch.setattr(operations, "load_ideal_max_for_forecast", lambda *_args, **_kwargs: (pv, trace))
    monkeypatch.setattr(adapter, "build_hourly_load_forecast", lambda *_args, **_kwargs: {h:1.0 for h in range(24)})
    monkeypatch.setattr(adapter, "build_load_forecast_correction", lambda **kw: {"hourly_load_kwh":kw["hourly_load_forecast"]})
    consumption = SimpleNamespace(daily=SimpleNamespace(daytime_load_kwh=16, morning_load_kwh=3), occupancy_adjustment=None)
    bundle = adapter.build_ideal_max_forecast_bundle(context(tmp_path, weather_factor), consumption, as_of=NOW)
    assert bundle.hourly_pv_kwh == pytest.approx({h:v*min(weather_factor, 1.0) for h,v in pv.items()})
    assert bundle.hourly_pv_max_kwh == pv
    assert bundle.hourly_load_kwh == {h:1.0 for h in range(24)}
    assert bundle.source == NEW_MODEL
    assert bundle.ideal_max_provenance["coefficient"] == trace["coefficient"]
    assert bundle.ideal_max_provenance["weather_adjustment_applied"] is True
    assert bundle.ideal_max_provenance["retrieved_at"] == (NOW-timedelta(hours=1)).isoformat()
    assert bundle.ideal_max_provenance["capability_retrieved_at"] == NOW.isoformat()
    assert bundle.ideal_max_provenance["forecast_sha256"] == "b"*64
    assert bundle.ideal_max_provenance["hourly_maximum_kwh"] == {str(h):v for h,v in pv.items()}
    assert "weather_forecast" not in trace  # The loaded capacity snapshot is unchanged.
    assert bundle.clear_ghi_forecast is None


@pytest.mark.parametrize("invalid", ["missing", "future", "naive", "negative", "missing_endpoint"])
def test_weather_is_required_and_cannot_silently_become_clear_sky(tmp_path, invalid):
    ctx = context(tmp_path)
    if invalid == "missing":
        ctx.forecast.pop("pv_radiation_rows")
    elif invalid == "future":
        ctx.forecast["pv_forecast_retrieved_at"] = (NOW+timedelta(seconds=1)).isoformat()
    elif invalid == "naive":
        ctx.forecast["pv_forecast_retrieved_at"] = NOW.replace(tzinfo=None).isoformat()
    elif invalid == "negative":
        ctx.forecast["pv_radiation_rows"][10]["shortwave_radiation_w_m2"] = -1
    else:
        ctx.forecast["pv_radiation_rows"][6]["shortwave_radiation_w_m2"] = None
    with pytest.raises(ValueError):
        adapter._weather_adjusted_pv(ctx, {h:2.0 for h in range(24)}, as_of=NOW)


@pytest.mark.parametrize("error", [TimeoutError, ValueError, OSError, PreconditionFailed])
def test_ledger_failure_returns_to_existing_forecast_without_retry(monkeypatch, tmp_path, capsys, error):
    calls = []
    def fail(*_args, **_kwargs):
        calls.append(1)
        raise error("private identifier")
    monkeypatch.setattr(operations, "load_ideal_max_for_forecast", fail)
    assert adapter.build_ideal_max_forecast_bundle(context(tmp_path), object(), as_of=NOW) is None
    assert calls == [1]
    event = json.loads(capsys.readouterr().out)
    assert event["status"] == "fallback" and event["severity"] == "ERROR"
    assert "private identifier" not in json.dumps(event)


def plan(model, *, site="same-site", key="pv_clear_ghi"):
    trace = {"model_id":model, "basis_version":"test", "site_fingerprint":site,
        "retrieved_at":NOW.isoformat(), "provider_issued_at":None,
        "calibration_pack":{"training_as_of":NOW.isoformat()}}
    return SimpleNamespace(raw_json=json.dumps({"generated_at":NOW.isoformat(), "decision_rationale":{key:trace}}))


@pytest.mark.parametrize("reverse", [False, True])
def test_approved_same_site_model_switch_and_fallback_preserve_provenance(reverse):
    old, new = plan("physical_clear_ghi_annual1"), plan(NEW_MODEL, key="pv_ideal_max")
    if reverse:
        old, new = new, old
    _validate_incoming_provenance(new)
    _check_provenance(new, old)


def test_model_switch_cannot_change_site_or_accept_unknown_model():
    old = plan("physical_clear_ghi_annual1")
    with pytest.raises(ValueError, match="site"):
        _check_provenance(plan(NEW_MODEL, site="different", key="pv_ideal_max"), old)
    with pytest.raises(ValueError, match="model/site/basis"):
        _check_provenance(plan("unknown_model"), old)


@pytest.mark.parametrize("as_of", [None, NOW])
def test_ready_ledger_uses_same_route_for_live_and_frozen_as_of(monkeypatch, as_of):
    from app.energy_plan import workflow
    from app.energy_plan import empirical_planning
    selected, expected = object(), object()
    monkeypatch.setattr(workflow, "_load_execution_context", lambda *_args, **_kw: object())
    monkeypatch.setattr(workflow, "_build_consumption_forecasts", lambda *_args: object())
    monkeypatch.setattr(workflow, "build_ideal_max_forecast_bundle", lambda *_args, **_kw: selected)
    monkeypatch.setattr(workflow, "load_calibration_for_context", lambda *_args: pytest.fail("weather PV calibration must not gate the ready ledger"))
    for name in ("_prepare_night_charge_from_hourly", "_build_soc_constraints", "_run_legacy_soc_optimization", "_run_soc_optimization"):
        monkeypatch.setattr(workflow, name, lambda *_args: object())
    monkeypatch.setattr(empirical_planning, "prepare_empirical_planning",
                        lambda context, consumption, pv, night, **kwargs: (context, pv, night))
    def output(*args):
        assert args[3] is selected
        return expected
    monkeypatch.setattr(workflow, "_build_energy_model_output", output)
    assert workflow.build_energy_plan(object(), as_of=as_of) is expected
