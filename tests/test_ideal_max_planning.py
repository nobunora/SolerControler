"""Clear-ledger selection, read-only provenance and nonblocking fallback."""
import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import PreconditionFailed

from app.energy_plan import ideal_max_adapter as adapter
from app.forecasting.pv_ideal_max import HourlyPvObservation, IdealMaxArray, IdealMaxGeometry, calculate_ideal_max, ideal_max_base
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


def context(tmp_path):
    return SimpleNamespace(target_date="2026-10-31", rows=[], consumption_rows=[],
        config=SimpleNamespace(artifacts_dir=tmp_path, latitude=35, longitude=135, timezone="Asia/Tokyo"),
        forecast={"weather_code": 95, "shortwave_radiation_sum_mj_m2": 0.0})


def test_adapter_uses_ceiling_directly_and_keeps_load_forecast(monkeypatch, tmp_path):
    pv = {h: 2.0 if 8 <= h < 16 else 0.0 for h in range(24)}
    trace = {"model_id":NEW_MODEL, "basis_version":"test", "calibration_sha256":"a"*64,
             "coefficient":1.5, "revision":7}
    monkeypatch.setattr(operations, "load_ideal_max_for_forecast", lambda *_args, **_kwargs: (pv, trace))
    monkeypatch.setattr(adapter, "build_hourly_load_forecast", lambda *_args, **_kwargs: {h:1.0 for h in range(24)})
    monkeypatch.setattr(adapter, "build_load_forecast_correction", lambda **kw: {"hourly_load_kwh":kw["hourly_load_forecast"]})
    consumption = SimpleNamespace(daily=SimpleNamespace(daytime_load_kwh=16, morning_load_kwh=3), occupancy_adjustment=None)
    bundle = adapter.build_ideal_max_forecast_bundle(context(tmp_path), consumption, as_of=NOW)
    assert bundle.hourly_pv_kwh == pv  # Even a zero-radiation weather input cannot reduce this basis.
    assert bundle.hourly_load_kwh == {h:1.0 for h in range(24)}
    assert bundle.source == NEW_MODEL and bundle.ideal_max_provenance == trace
    assert bundle.clear_ghi_forecast is None


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


def test_ready_ledger_bypasses_weather_pv_calibration_gate(monkeypatch):
    from app.energy_plan import workflow
    selected, expected = object(), object()
    monkeypatch.setattr(workflow, "_load_execution_context", lambda *_args, **_kw: object())
    monkeypatch.setattr(workflow, "_build_consumption_forecasts", lambda *_args: object())
    monkeypatch.setattr(workflow, "build_ideal_max_forecast_bundle", lambda *_args, **_kw: selected)
    monkeypatch.setattr(workflow, "load_calibration_for_context", lambda *_args: pytest.fail("weather PV calibration must not gate the ready ledger"))
    for name in ("_prepare_night_charge_from_hourly", "_build_soc_constraints", "_run_legacy_soc_optimization", "_run_soc_optimization"):
        monkeypatch.setattr(workflow, name, lambda *_args: object())
    def output(*args):
        assert args[3] is selected
        return expected
    monkeypatch.setattr(workflow, "_build_energy_model_output", output)
    assert workflow.build_energy_plan(object()) is expected
