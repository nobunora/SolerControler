"""Planning integration and acquisition provenance, without network or device I/O."""
from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.energy_plan.clear_ghi_adapter import _prepare_night_charge_from_hourly, _valid_hourly
from app.energy_plan.result_builder import _clear_ghi_trace
from app.energy_plan.workflow import _radiation_provenance
from app.forecasting.clear_ghi_observations import (
    fetch_satellite_original, observed_day_records, training_records,
)
from app.forecasting.pv_clear_ghi import (
    BASIS_VERSION, MODEL_ID, calibration_from_bytes, canonical_pack_bytes, forecast_clear_ghi,
)

DAY = date(2026, 10, 10)
NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)


def pack():
    value = {"schema_version": 1, "model_id": MODEL_ID, "basis_version": BASIS_VERSION,
             "site_fingerprint": "test-site", "terms": ["1", "x", "cos_phi", "sin_phi"],
             "theta4": [2.0, 0.1, 0.2, 0.1], "training_dates": ["2026-01-01", "2026-04-01", "2026-07-01"],
             "training_samples": 39, "rank": 4, "condition_number": 3.0,
             "training_as_of": NOW.isoformat(), "created_at": NOW.isoformat(), "usable_from": NOW.isoformat(),
             "actual_source_sha256": "a" * 64, "certification_source_sha256": "b" * 64,
             "certification_rule_version": "synthetic-v1"}
    raw = canonical_pack_bytes(value)
    return calibration_from_bytes(raw, sha256=hashlib.sha256(raw).hexdigest())


def inputs():
    actual = [{"dt": datetime.combine(DAY, datetime.min.time()) + timedelta(hours=h, minutes=m), "pv": 0.1}
              for h in range(5, 18) for m in (0, 30)]
    satellite = {"hourly_units": {"shortwave_radiation": "W/m²", "sunshine_duration": "s"},
                 "hourly": {"time": [f"{DAY}T{h + 1:02d}:00" for h in range(5, 18)],
                            "shortwave_radiation": [1000.0] * 13, "sunshine_duration": [3600.0] * 13}}
    return actual, satellite


def records(actual, satellite, previous=(), *, available=NOW):
    return observed_day_records(actual, satellite, target_date=DAY + timedelta(days=1),
        actual_available_at=available, certification_available_at=available,
        latitude=35, longitude=135, timezone_name="Asia/Tokyo", fingerprint="test-site", previous=list(previous))


def test_observed_pair_zero_and_conservative_first_availability():
    actual, satellite = inputs()
    actual[0]["pv"] = 0.0
    result = records(actual, satellite)
    assert len(result) == 1
    assert result[0]["hours"][0]["actual_halfhour_kwh"] == [0.0, 0.1]
    assert result[0]["actual_available_at"] == NOW.isoformat()
    assert training_records(result)[0]["date"] == DAY
    unchanged = records(actual, satellite, result, available=NOW + timedelta(days=1))
    assert unchanged == result
    actual[1]["pv"] = 0.2
    changed = records(actual, satellite, result, available=NOW + timedelta(days=1))
    assert changed[0]["actual_sha256"] != result[0]["actual_sha256"]
    assert changed[0]["actual_available_at"] != result[0]["actual_available_at"]
    assert changed[0]["certification_available_at"] == result[0]["certification_available_at"]


def test_missing_actual_invalidates_but_observation_gap_retains_verified_original():
    actual, satellite = inputs()
    old = records(actual, satellite)
    assert records(actual[:-1], satellite, old) == []
    satellite["hourly"]["shortwave_radiation"][2] = None
    assert records(actual, satellite, old) == old


def test_cloudy_day_is_not_certified_and_all_previous_clear_days_are_preserved():
    actual, satellite = inputs()
    old = records(actual, satellite)
    satellite["hourly"]["sunshine_duration"] = [0.0] * 13
    assert records(actual, satellite, old) == []
    assert records([], satellite, old) == old


@pytest.mark.parametrize("invalid", [True, -1.0, float("nan"), float("inf")])
def test_observed_invalid_values_are_not_silent_zero(invalid):
    actual, satellite = inputs()
    satellite["hourly"]["shortwave_radiation"][5] = invalid
    with pytest.raises(ValueError):
        records(actual, satellite)


def test_observed_acquisition_one_bounded_request_no_future_date(monkeypatch):
    _, satellite = inputs()
    calls = []
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return satellite
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()
    monkeypatch.setattr("app.forecasting.clear_ghi_observations.requests.get", get)
    result, retrieved = fetch_satellite_original(start_date=DAY, end_date=DAY, latitude=35, longitude=135, timezone_name="Asia/Tokyo")
    assert len(calls) == 1 and calls[0][1]["timeout"] == (5, 25)
    assert calls[0][1]["params"]["end_date"] == str(DAY)
    assert retrieved.tzinfo is not None and result["hourly"] == satellite["hourly"]


def test_same_response_tail_preserved_temperature_unshifted_and_trace_single_owner():
    day = date(2026, 10, 15)
    times = [(datetime.combine(day, datetime.min.time()) + timedelta(hours=h)).isoformat(timespec="minutes") for h in range(25)]
    hourly = {"time": times, "shortwave_radiation": [400.0] * 25, "temperature_2m": list(range(25))}
    original_temperature = hourly["temperature_2m"][:]
    captured = _radiation_provenance(hourly, target_date=str(day), timezone="Asia/Tokyo")
    assert len(captured["pv_radiation_rows"]) == 25
    assert hourly["temperature_2m"] == original_temperature
    forecast = forecast_clear_ghi(target_date=day, as_of=NOW, latitude=35, longitude=135, timezone="Asia/Tokyo",
        calibration=pack(), source_rows=tuple(captured["pv_radiation_rows"]), forecast_hash=captured["pv_forecast_input_sha256"],
        retrieved_at=NOW, provider_issued_at=None)
    trace = _clear_ghi_trace(forecast, timezone="Asia/Tokyo", hourly_load_kwh=dict.fromkeys(range(24), 0.1))
    assert trace["hourly"][8]["source_time"].endswith("09:00:00+09:00")
    assert trace["hourly"][23]["interval_end"].startswith("2026-10-16T00:00")
    assert trace["totals"]["decision_day_pv_kwh"] == sum(forecast.pv_kwh[7:23])
    assert trace["totals"]["midday_load_kwh"] == pytest.approx(0.6)
    assert trace["calibration_sha256"] == hashlib.sha256(canonical_pack_bytes(trace["calibration_pack"])).hexdigest()


def test_radiation_provenance_keeps_original_offset_tail_and_rejects_unknown_time():
    original = "2026-10-15T15:00:00+00:00"
    result = _radiation_provenance({"time": [original], "shortwave_radiation": [10.0]}, target_date="2026-10-15", timezone="Asia/Tokyo")
    assert result["pv_radiation_rows"] == [{"time": original, "shortwave_radiation_w_m2": 10.0}]
    for invalid in (None, 123, "not-a-timestamp"):
        with pytest.raises(ValueError):
            _radiation_provenance({"time": [invalid], "shortwave_radiation": [10.0]}, target_date="2026-10-15", timezone="Asia/Tokyo")


def test_final_hourly_pv_drives_night_target_once_with_exact_windows(monkeypatch):
    from app.energy_plan.workflow import EnergyModelConfig
    config = replace(EnergyModelConfig.from_env(), battery_temp_c=None)
    context = SimpleNamespace(config=config, forecast={"temp_c": 0.0, "sun_hours": 5.0}, rows=[], billing_rows=None,
        target_date="2026-10-15", latest_soc_percent=20, coefficients=object(),
        historical_profile={"morning_pv_ratio": 0.25, "midday_surplus_ratio": 0.375})
    pv = {h: float(h) / 10 for h in range(24)}
    bundle = SimpleNamespace(hourly_pv_kwh=pv, hourly_load_kwh=dict.fromkeys(range(24), 0.1))
    calls = []
    monkeypatch.setattr("app.energy_plan.clear_ghi_adapter.monthly_day_buy_kwh_before_target", lambda *a, **k: {})
    monkeypatch.setattr("app.energy_plan.clear_ghi_adapter.expected_rest_of_month_day_buy_kwh", lambda *a, **k: {})
    def compute(coefficients, actual):
        calls.append(actual)
        return SimpleNamespace()
    monkeypatch.setattr("app.energy_plan.clear_ghi_adapter.compute_night_charge_target", compute)
    monkeypatch.setattr("app.energy_plan.clear_ghi_adapter.to_dict", lambda result: {})
    prepared = _prepare_night_charge_from_hourly(context, object(), bundle)
    assert len(calls) == 1 and calls[0] is prepared.inputs
    assert prepared.inputs.predicted_pv_kwh_override == sum(pv[h] for h in range(7, 23))
    assert prepared.inputs.predicted_morning_pv_kwh_override == sum(pv[h] for h in range(7, 10))
    assert prepared.inputs.predicted_midday_surplus_kwh_override == max(0, sum(pv[h] - 0.1 for h in range(10, 16)))
    assert prepared.inputs.temp_forecast_c == prepared.inputs.battery_temp_c == 0.0
    assert prepared.inputs.cycle_count == config.cycle_count


def test_hourly_validation_keeps_zero_and_rejects_bool_missing_nonfinite():
    good = dict.fromkeys(range(24), 0.0)
    assert _valid_hourly(good) == good
    for bad in ({**good, 5: True}, {**good, 5: float("nan")}, {h: 0 for h in range(23)}):
        with pytest.raises(ValueError):
            _valid_hourly(bad)


@pytest.mark.parametrize("existing", [False, True])
def test_update_failure_retains_only_a_valid_existing_pack(monkeypatch, tmp_path, existing, capsys):
    import json
    from requests import RequestException
    from app.energy_plan.clear_ghi_update import load_calibration_for_context
    from app.energy_plan.workflow import EnergyModelConfig
    calibration = pack()
    state = {"calibration_pack": json.loads(calibration.raw_bytes), "calibration_sha256": calibration.sha256, "clear_days": []}
    class Store:
        def __init__(self, *args, **kwargs):
            pass
        def read_current(self):
            return (state if existing else None), 7
        def check_deadline(self):
            pass
        def remaining_timeout(self, **kwargs):
            return 25.0
        def publish(self, *args, **kwargs):
            pytest.fail("failed refresh must not publish")
    actual, _ = inputs()
    context = SimpleNamespace(config=replace(EnergyModelConfig.from_env(), artifacts_dir=tmp_path), target_date="2026-10-15", billing_rows=actual, rows=actual)
    monkeypatch.setattr("app.energy_plan.clear_ghi_update.site_fingerprint", lambda **kwargs: "test-site")
    monkeypatch.setattr("app.energy_plan.clear_ghi_update.load_pv_array_configs", lambda: [])
    monkeypatch.setattr("app.energy_plan.clear_ghi_update.night_plan_archive_prefix", lambda: "")
    monkeypatch.setattr("app.energy_plan.clear_ghi_update.ClearGhiStore", Store)
    monkeypatch.setattr("app.energy_plan.clear_ghi_update._bootstrap_actual_rows", lambda context, **kwargs: actual)
    def fail(**kwargs):
        raise RequestException("synthetic provider failure")
    monkeypatch.setattr("app.energy_plan.clear_ghi_update.fetch_satellite_original", fail)
    if existing:
        retained, cutoff = load_calibration_for_context(context)
        assert retained.raw_bytes == calibration.raw_bytes and cutoff >= retained.usable_from
        assert "pv_calibration_retained" in capsys.readouterr().out
    else:
        with pytest.raises(RequestException):
            load_calibration_for_context(context)
        assert "pv_calibration_failed" in capsys.readouterr().out


def test_corrupt_current_is_not_treated_as_cold_start(monkeypatch, tmp_path):
    from app.energy_plan.clear_ghi_update import load_calibration_for_context
    from app.energy_plan.workflow import EnergyModelConfig
    class Store:
        def __init__(self, *args, **kwargs):
            pass
        def read_current(self):
            raise ValueError("corrupt current")
    monkeypatch.setattr("app.energy_plan.clear_ghi_update.ClearGhiStore", Store)
    monkeypatch.setattr("app.energy_plan.clear_ghi_update.load_pv_array_configs", lambda: [])
    monkeypatch.setattr("app.energy_plan.clear_ghi_update.night_plan_archive_prefix", lambda: "")
    def forbidden(**kwargs):
        pytest.fail("corrupt current must stop before acquisition")
    monkeypatch.setattr("app.energy_plan.clear_ghi_update.fetch_satellite_original", forbidden)
    context = SimpleNamespace(config=replace(EnergyModelConfig.from_env(), artifacts_dir=tmp_path), target_date="2026-10-15")
    with pytest.raises(ValueError, match="corrupt current"):
        load_calibration_for_context(context)


def test_update_shares_deadline_and_only_publishes_completed_pack(monkeypatch, tmp_path, capsys):
    import json
    import time
    from app.energy_plan.clear_ghi_update import load_calibration_for_context
    from app.energy_plan.workflow import EnergyModelConfig
    actual, satellite = inputs()
    captured = {}
    class Store:
        published_state_sha256 = "e" * 64
        def __init__(self, *args, **kwargs):
            captured["deadline"] = kwargs["deadline"]
        def read_current(self):
            return None, 0
        def check_deadline(self):
            assert time.monotonic() < captured["deadline"]
        def remaining_timeout(self, *, cap):
            return min(cap, captured["deadline"] - time.monotonic())
        def publish(self, calibration, records, *, expected_generation):
            assert calibration.usable_from >= calibration.training_as_of
            assert expected_generation == 0
            captured["published"] = calibration
    monkeypatch.setattr("app.energy_plan.clear_ghi_update.ClearGhiStore", Store)
    monkeypatch.setattr("app.energy_plan.clear_ghi_update.site_fingerprint", lambda **kwargs: "test-site")
    monkeypatch.setattr("app.energy_plan.clear_ghi_update.load_pv_array_configs", lambda: [])
    monkeypatch.setattr("app.energy_plan.clear_ghi_update.night_plan_archive_prefix", lambda: "")
    def bootstrap(context, *, deadline):
        assert deadline == captured["deadline"]
        return actual
    monkeypatch.setattr("app.energy_plan.clear_ghi_update._bootstrap_actual_rows", bootstrap)
    def acquire(**kwargs):
        assert 0 < kwargs["timeout_seconds"] <= 30
        return satellite, datetime.now(timezone.utc)
    monkeypatch.setattr("app.energy_plan.clear_ghi_update.fetch_satellite_original", acquire)
    def fit(records, *, as_of, **kwargs):
        raw_pack = json.loads(pack().raw_bytes)
        raw_pack.update(training_as_of=as_of.isoformat(), created_at=as_of.isoformat(), usable_from=as_of.isoformat())
        raw = canonical_pack_bytes(raw_pack)
        return calibration_from_bytes(raw, sha256=hashlib.sha256(raw).hexdigest())
    monkeypatch.setattr("app.energy_plan.clear_ghi_update.fit_clear_ghi_calibration", fit)
    context = SimpleNamespace(config=replace(EnergyModelConfig.from_env(), artifacts_dir=tmp_path), target_date="2026-10-15", rows=[], billing_rows=[])
    result, cutoff = load_calibration_for_context(context)
    assert result is captured["published"] and cutoff >= result.usable_from
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    event = next(item for item in events if item["event"] == "pv_calibration_updated")
    assert event["state_sha256"] == "e" * 64


@pytest.mark.parametrize("cost_enabled", [False, True])
def test_real_optimizer_to_plan_persistence_keeps_single_trace_and_hourly_source(monkeypatch, tmp_path, cost_enabled):
    import json
    from app.energy_plan.workflow import (
        EnergyModelConfig, EnergyModelContext, ConsumptionForecastBundle, build_energy_plan,
    )
    from app.energy_plan.energy_model import EnergyModelCoefficients
    from app.forecasting.consumption import ConsumptionForecast
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("KP_NIGHT_PLAN_PATH", raising=False)
    monkeypatch.setenv("FORECAST_CORRECTION_ENABLED", "false")
    monkeypatch.setenv("SOC_EXPORT_CONTRACT_STATUS", "inactive")
    config = replace(EnergyModelConfig.from_env(), artifacts_dir=tmp_path, latitude=35, longitude=135, timezone="Asia/Tokyo", cost_optimization_enabled=cost_enabled)
    coefficients = EnergyModelCoefficients(soc_per_kwh_charge=10.0, soc_per_kwh_discharge=10.0, soc_drift_per_slot=0.0,
        battery_round_trip_efficiency=0.9, battery_usable_capacity_kwh=10.0, pv_self_consumption_ratio=0.7,
        pv_direct_use_ratio=0.5, pv_to_battery_ratio=0.2, pv_kwh_per_sunhour=2.0,
        pv_temp_coeff_per_deg=-0.01, battery_temp_coeff_per_deg=-0.01, battery_cycle_capacity_fade_per_cycle=0.001)
    rows = [{"dt": datetime(2026, 10, 8) + timedelta(minutes=30 * i), "pv": 0.1, "load": 0.1, "buy": 0.0, "sell": 0.0, "soc": 30.0}
            for i in range(48 * 7)]
    day = date(2026, 10, 15)
    times = [(datetime.combine(day, datetime.min.time()) + timedelta(hours=h)).isoformat(timespec="minutes") for h in range(25)]
    weather = {"date": str(day), "source": "synthetic", "temp_c": 20.0, "sun_hours": 8.0,
               **_radiation_provenance({"time": times, "shortwave_radiation": [400.0] * 25}, target_date=str(day), timezone="Asia/Tokyo")}
    weather["pv_forecast_retrieved_at"] = NOW.isoformat()
    context = EnergyModelContext(config=config, csv_paths=[], rows=rows, coefficients=coefficients,
        historical_profile={"morning_pv_ratio": 0.25, "midday_surplus_ratio": 0.375}, forecast=weather,
        target_date=str(day), latest_soc_percent=30, occupancy_events=[])
    consumption = ConsumptionForecast(target_date=day, morning_load_kwh=0.6, daytime_load_kwh=3.2, source="synthetic", sample_count=7, features=[])
    monkeypatch.setattr("app.energy_plan.workflow._load_execution_context", lambda config: context)
    monkeypatch.setattr("app.energy_plan.workflow._build_consumption_forecasts", lambda context: ConsumptionForecastBundle(consumption, consumption, {}, None))
    monkeypatch.setattr("app.energy_plan.workflow.load_soc_decision_prior_from_firestore", lambda **kwargs: {"applied": False})
    def obsolete(*args, **kwargs):
        pytest.fail("old PV generation path was invoked")
    monkeypatch.setattr("app.energy_plan.workflow._build_selected_pv_forecast", obsolete)
    monkeypatch.setattr("app.energy_plan.workflow._prepare_night_charge", obsolete)
    output = build_energy_plan(config, calibration=pack(), as_of=NOW)
    output.persist()
    document = json.loads(output.output_path.read_text(encoding="utf-8"))
    trace = document["decision_rationale"]["pv_clear_ghi"]
    assert len(trace["hourly"]) == 24
    assert document["inputs"]["predicted_pv_kwh_override"] == trace["totals"]["decision_day_pv_kwh"]
    assert document["result"]["final_pv_forecast_source"] == MODEL_ID
    assert "pv_clear_ghi" not in document["decision_rationale"]["pv_physical_forecast"]
    assert document["coefficients"]["pv_forecast_error_ratio_sample_count"] == 0.0
    assert 0 <= document["result"]["target_soc_7_percent"] <= 100
    assert set(document["daytime_soc_optimization"]["hourly_pv_forecast_kwh"]) == {str(h) for h in range(24)}
    assert all(document["daytime_soc_optimization"]["hourly_pv_forecast_kwh"][str(h)] == round(trace["hourly"][h]["pv_kwh"], 4)
               for h in range(24))


def test_missing_timestamped_radiation_stops_before_decision_or_output(monkeypatch, tmp_path, capsys):
    from app.energy_plan.workflow import EnergyModelConfig, build_energy_plan
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FORECAST_CORRECTION_ENABLED", "false")
    context = SimpleNamespace(config=EnergyModelConfig.from_env(), rows=[], target_date="2026-10-15", forecast={})
    consumption = SimpleNamespace(daily=SimpleNamespace(daytime_load_kwh=3.2, morning_load_kwh=0.6), occupancy_adjustment=None)
    monkeypatch.setattr("app.energy_plan.workflow._load_execution_context", lambda config: context)
    monkeypatch.setattr("app.energy_plan.workflow._build_consumption_forecasts", lambda context: consumption)
    def forbidden(*args, **kwargs):
        pytest.fail("invalid forecast must not reach a decision or output")
    for name in ("_build_soc_constraints", "_run_legacy_soc_optimization", "_run_soc_optimization", "_build_energy_model_output", "_build_selected_pv_forecast"):
        monkeypatch.setattr(f"app.energy_plan.workflow.{name}", forbidden)
    with pytest.raises(ValueError, match="timestamped radiation"):
        build_energy_plan(context.config, calibration=pack(), as_of=NOW)
    assert '"stage": "pv_forecast_bundle"' in capsys.readouterr().out


def test_observation_missing_reason_is_logged_without_changing_source_values(capsys):
    actual, satellite = inputs()
    satellite["hourly"]["shortwave_radiation"][4] = None
    assert records(actual, satellite) == []
    assert '"satellite_hour_missing"' in capsys.readouterr().out
    assert satellite["hourly"]["shortwave_radiation"][4] is None
