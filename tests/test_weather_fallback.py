import copy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest
import requests

from app.energy_plan import weather_cache, workflow
from app.energy_plan.ideal_max_adapter import _weather_adjusted_pv


class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 11, 18, 6, tzinfo=timezone.utc).astimezone(tz or timezone.utc)


@pytest.fixture
def setup_weather(monkeypatch, tmp_path):
    monkeypatch.setattr(workflow, 'datetime', Clock)
    monkeypatch.setattr(weather_cache, 'datetime', Clock)
    monkeypatch.setenv('ARTIFACTS_DIR', str(tmp_path))
    monkeypatch.setenv('DATA_BACKEND', 'sqlite')
    monkeypatch.setenv('FORECAST_DATE_OVERRIDE', '2026-11-18')
    monkeypatch.setenv('FORECAST_API_RETRIES', '1')
    monkeypatch.delenv('FORECAST_SUN_HOURS_OVERRIDE', raising=False)
    start = datetime(2026, 11, 18)
    times = [(start + timedelta(hours=hour)).isoformat(timespec='minutes') for hour in range(73)]
    daily = {'time': [(start.date() + timedelta(days=day)).isoformat() for day in range(3)],
             'sunshine_duration': [3600] * 3, 'temperature_2m_mean': [15] * 3,
             'weather_code': [1] * 3}
    hourly = {'time': times, 'shortwave_radiation': [150 if 7 <= datetime.fromisoformat(t).hour <= 17 else 0 for t in times],
              'temperature_2m': [15] * 73, 'weather_code': [1] * 73}
    payload = {'daily': daily, 'hourly': hourly}
    monkeypatch.setattr(workflow.requests, 'get', lambda *_, **__: SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: copy.deepcopy(payload)))
    return tmp_path


def acquire():
    return workflow._forecast_from_env_or_api(lat=35.0, lon=139.0, timezone='Asia/Tokyo')


def offline(*_args, **_kwargs):
    raise requests.Timeout('synthetic provider timeout')


def test_success_caches_today_and_tomorrow_then_uses_same_target_without_network(monkeypatch, setup_weather):
    original = acquire()
    paths = list((setup_weather / 'weather_forecast_cache').glob('*.json'))
    assert len(paths) == 2
    assert {json.loads(path.read_text())['forecast']['date'] for path in paths} == {'2026-11-18', '2026-11-19'}
    monkeypatch.setattr(workflow.requests, 'get', offline)
    monkeypatch.setattr(workflow, '_fetch_open_meteo_previous_day1_forecast', lambda **_: pytest.fail('valid cache avoids another API'))
    fallback = acquire()
    assert fallback['weather_fallback'] == 'saved_forecast'
    for key in ('date', 'pv_forecast_retrieved_at', 'pv_provider_issued_at', 'pv_forecast_input_sha256', 'pv_radiation_rows'):
        assert fallback[key] == original[key]


def test_saved_forecast_does_not_regress_from_today_to_yesterday(monkeypatch, setup_weather):
    today = acquire()
    previous = {**today, 'pv_forecast_retrieved_at': '2026-11-17T03:00:00+00:00'}
    weather_cache.save_forecast(previous, latitude=35.0, longitude=139.0, timezone='Asia/Tokyo')
    monkeypatch.setattr(workflow.requests, 'get', offline)
    result = acquire()
    assert result['date'] == '2026-11-18'
    assert result['pv_forecast_retrieved_at'] == today['pv_forecast_retrieved_at']


def test_yesterdays_forecast_for_today_is_used_when_today_has_no_cache(monkeypatch, setup_weather):
    today = acquire()
    previous = {**today, 'pv_forecast_retrieved_at': '2026-11-17T03:00:00+00:00'}
    weather_cache.save_forecast(previous, latitude=35., longitude=139., timezone='Asia/Tokyo')
    for path in (setup_weather / 'weather_forecast_cache').glob('*.json'):
        if json.loads(path.read_text())['forecast']['pv_forecast_retrieved_at'] == today['pv_forecast_retrieved_at']:
            path.unlink()
    monkeypatch.setattr(workflow.requests, 'get', offline)
    result = acquire()
    assert result['date'] == today['date']
    assert result['pv_forecast_retrieved_at'] == previous['pv_forecast_retrieved_at']


def test_corrupt_cache_uses_other_forecast_with_real_radiation_provenance(monkeypatch, setup_weather):
    previous = acquire()
    for path in (setup_weather / 'weather_forecast_cache').glob('*.json'):
        value = json.loads(path.read_text())
        value['forecast']['pv_forecast_input_sha256'] = '0' * 64
        path.write_text(json.dumps(value))
    monkeypatch.setattr(workflow.requests, 'get', offline)
    monkeypatch.setattr(workflow, '_fetch_open_meteo_previous_day1_forecast', lambda **_: copy.deepcopy(previous))
    result = acquire()
    assert result['weather_fallback'] == 'previous_day_provider'
    weather_cache.validate_forecast(result, target='2026-11-18', latitude=35.0, longitude=139.0, timezone='Asia/Tokyo')


@pytest.mark.parametrize('history_available', [True, False])
def test_total_weather_outage_uses_explicit_estimate_without_fabricating_weather(monkeypatch, setup_weather, history_available):
    monkeypatch.setattr(workflow.requests, 'get', offline)
    monkeypatch.setattr(workflow, '_fetch_open_meteo_previous_day1_forecast', offline)
    forecast = acquire()
    assert forecast['weather_unavailable'] is True and 'pv_radiation_rows' not in forecast
    rows = [{'dt': datetime(2026, 11, 17, hour, minute), 'pv': .2}
            for hour in range(24) for minute in (0, 30)] if history_available else []
    context = SimpleNamespace(forecast=forecast, rows=rows, target_date='2026-11-18',
        config=SimpleNamespace(latitude=35., longitude=139., timezone='Asia/Tokyo'))
    maximum = {hour: 1.0 if 7 <= hour <= 17 else 0.0 for hour in range(24)}
    pv, evidence = _weather_adjusted_pv(context, maximum, as_of=Clock.now(timezone.utc))
    assert len(pv) == 24 and all(0 <= pv[hour] <= maximum[hour] for hour in range(24))
    assert evidence['training_eligible'] is False
    assert evidence['input_kind'] == 'planning_estimate_not_weather'
    assert evidence['method'] == ('historical_pv_estimate' if history_available else 'low_pv_scenario')
    assert pv[12] == (.4 if history_available else 0.0)


def test_persistent_cache_failure_does_not_discard_fresh_forecast(monkeypatch, setup_weather):
    monkeypatch.setattr(weather_cache, '_firestore', lambda: (_ for _ in ()).throw(OSError('store unavailable')))
    result = acquire()
    assert result['source'] == 'open-meteo-forecast'
    assert result['pv_radiation_rows']


def test_persistent_cache_has_bounded_cas_and_never_overwrites_newer_vintage(monkeypatch, setup_weather):
    original = acquire()
    stored = {}
    writes = []

    class Document:
        def get(self, **kwargs):
            assert kwargs == {'timeout': 4, 'retry': None}
            return SimpleNamespace(exists=bool(stored), update_time='native-version', to_dict=lambda: dict(stored))

        def update(self, value, **kwargs):
            assert kwargs == {'timeout': 4, 'retry': None, 'option': 'native-version'}
            writes.append(value)
            stored.update(value)

        def create(self, value, **kwargs):
            assert kwargs == {'timeout': 4, 'retry': None}
            writes.append(value)
            stored.update(value)

    client = SimpleNamespace(collection=lambda _: SimpleNamespace(document=lambda _: Document()),
                             write_option=lambda **kw: kw['last_update_time'])
    monkeypatch.setattr(weather_cache, '_firestore', lambda: client)
    options = dict(latitude=35., longitude=139., timezone='Asia/Tokyo')
    weather_cache.save_forecast(original, **options)
    older = {**original, 'pv_forecast_retrieved_at': '2026-11-18T05:30:00+00:00'}
    stored['forecast'] = older
    weather_cache.save_forecast(original, **options)
    assert len(writes) == 2  # Existing cache is older, so replace under its native version.
    weather_cache.save_forecast(older, **options)
    assert len(writes) == 2 and stored['forecast'] == original


def test_cache_rejects_wrong_target_and_future_acquisition(setup_weather):
    original = acquire()
    with pytest.raises(ValueError, match='target'):
        weather_cache.validate_forecast(original, target='2026-11-19', latitude=35., longitude=139., timezone='Asia/Tokyo')
    with pytest.raises(ValueError, match='time'):
        weather_cache.validate_forecast({**original, 'pv_forecast_retrieved_at': '2027-01-01T00:00:00+00:00'},
            target='2026-11-18', latitude=35., longitude=139., timezone='Asia/Tokyo')


def test_fresh_forecast_repairs_corrupt_cache_without_leftover_files(setup_weather):
    acquire()
    paths = list((setup_weather / 'weather_forecast_cache').glob('*.json'))
    for path in paths:
        path.write_text('{invalid')
    acquire()
    assert all(json.loads(path.read_text())['forecast']['pv_radiation_rows'] for path in paths)
    assert len(list(paths[0].parent.iterdir())) == 2


@pytest.mark.parametrize('history_available', [True, False])
def test_weather_outage_completes_pv_soc_cost_and_serialization(monkeypatch, setup_weather, history_available):
    from app.energy_plan.energy_model import EnergyModelCoefficients
    from app.energy_plan import clear_ghi_adapter
    from app.energy_plan import empirical_planning
    from app.forecasting.consumption import ConsumptionForecast
    from app.operations import pv_ideal_max

    monkeypatch.setenv('SOC_EXPORT_CONTRACT_STATUS', 'inactive')
    monkeypatch.setenv('SOC_EXPORT_VALUE_MODE', 'penalty')
    monkeypatch.setattr(empirical_planning, 'select_hourly_pv_bias',
        lambda **_: pytest.fail('planning estimates must not receive weather residual corrections'))
    monkeypatch.setattr(workflow.requests, 'get', offline)
    monkeypatch.setattr(workflow, '_fetch_open_meteo_previous_day1_forecast', offline)
    forecast = acquire()
    config = workflow.EnergyModelConfig.from_env()
    rows = [{'dt': datetime(2026, 11, 17, hour, minute), 'pv': .2, 'load': .3,
             'buy': .1, 'sell': 0., 'soc': 50.}
            for hour in range(24) for minute in (0, 30)] if history_available else []
    context = workflow.EnergyModelContext(config=config, csv_paths=[], rows=rows,
        coefficients=EnergyModelCoefficients(10., 10., 0., .9, 10., .7, .5, .2, 2., -.01, -.01, .001),
        historical_profile={'morning_pv_ratio': .25, 'midday_surplus_ratio': .375},
        forecast=forecast, target_date='2026-11-18', latest_soc_percent=50., occupancy_events=[])
    daily = ConsumptionForecast(target_date=datetime(2026, 11, 18).date(), morning_load_kwh=2.,
        daytime_load_kwh=8., source='historical', sample_count=len(rows), features=[])
    consumption = workflow.ConsumptionForecastBundle(daily, daily, {}, None)
    maximum = {hour: 1. if 7 <= hour <= 17 else 0. for hour in range(24)}
    trace = {'model_id': 'physical_pv_ideal_max_global_v1', 'basis_version': 'test',
             'calibration_sha256': 'a' * 64, 'coefficient': 1., 'revision': 1,
             'retrieved_at': Clock.now(timezone.utc).isoformat()}
    monkeypatch.setattr(workflow, '_load_execution_context', lambda *_, **__: context)
    monkeypatch.setattr(workflow, '_build_consumption_forecasts', lambda *_, **__: consumption)
    monkeypatch.setattr(pv_ideal_max, 'load_ideal_max_for_forecast', lambda *_, **__: (maximum, dict(trace)))
    monkeypatch.setattr(clear_ghi_adapter, 'monthly_day_buy_kwh_before_target', lambda *_, **__: {})
    monkeypatch.setattr(clear_ghi_adapter, 'expected_rest_of_month_day_buy_kwh', lambda *_, **__: {})
    output = workflow.build_energy_plan(config, historical_input=object(), as_of=Clock.now(timezone.utc))
    payload = output.document.to_payload()
    json.dumps(payload, allow_nan=False)
    assert 0 <= payload['result']['target_soc_7_percent'] <= 100
    assert payload['forecast']['source'] == 'weather-unavailable'
    assert payload['forecast']['sun_hours'] is None
    assert payload['daytime_soc_optimization'] is not None
