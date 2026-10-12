"""Selected forecast identity affects plan fallbacks, never the PV/load pair."""

from copy import deepcopy

import pytest

from app.dashboard import firestore_repository as repository
from app.dashboard.aggregation import _build_energy_daily
from app.dashboard.models import DashboardRawData
from app.dashboard.service import assemble_dashboard_slice

DAY = "2026-10-06"
HASH = "a" * 64
OTHER_HASH = "b" * 64
MARKERS = {"_raw_plan_sha256", "_control_version_id", "_plan_generated_at"}


class Document:
    def __init__(self, name, value):
        self.id, self.value = name, value
        self.exists = value is not None

    def to_dict(self):
        return deepcopy(self.value)


class Query:
    def __init__(self, client, name, filters=()):
        self.client, self.name, self.filters = client, name, filters

    def document(self, name):
        return Document(name, self.client.data.get(self.name, {}).get(name))

    def where(self, field, operator, value):
        return Query(self.client, self.name, (*self.filters, (field, operator, value)))

    def order_by(self, *args):
        return self

    def stream(self):
        self.client.reads.append(self.name)
        for name, row in self.client.data.get(self.name, {}).items():
            if all(str(row.get(field, "")) >= value if operator == ">=" else
                   str(row.get(field, "")) <= value if operator == "<=" else
                   str(row.get(field, "")) < value for field, operator, value in self.filters):
                yield Document(name, row)


class Client:
    def __init__(self, data):
        self.data, self.reads = data, []

    def collection(self, name):
        return Query(self, name)

    def get_all(self, refs):
        self.reads.append("night_charge_plans")
        return iter(refs)


def hourly(*, count=24, raw_hash=HASH, run="selected"):
    return [{"date": DAY, "hour": hour, "forecast_pv_kwh": 0.2,
             "forecast_load_kwh": 0.3, "forecast_charge_kwh": 0.0,
             "source": "forecast_hourly", "forecast_run_id": run,
             "forecast_issued_at": f"{DAY}T03:00:00+09:00",
             "_raw_plan_sha256": raw_hash, "_control_version_id": "v1",
             "_plan_generated_at": f"{DAY}T03:00:00+09:00"} for hour in range(count)]


def review(rows, *, plan_hash=HASH, battery=None, selected_run="selected"):
    daily = _build_energy_daily(start_date=DAY, end_date_iso=DAY,
                               pv_daily=[{"date": DAY, "forecast_pv_total_kwh": 999.0}],
                               monitoring_daily=[{"date": DAY, "actual_pv_kwh": 8.0, "actual_load_kwh": 9.0}],
                               forecast_hourly=rows)
    daily[0]["forecast_run_id"] = selected_run
    client = Client({"night_charge_plans": {DAY: {
        "detail_sha256": plan_hash, "forecast": {"date": DAY},
        "result": {"soc_expected_day_buy_kwh": 1.5, "soc_expected_sell_kwh": 2.5,
                   "target_soc_7_percent": 80.0, "required_night_charge_kwh": 3.5}}}})
    metrics = {"date": DAY, "sample_count": 48, "first_sample_at": f"{DAY}T00:00:00",
               "latest_sample_at": f"{DAY}T23:30:00", "morning_soc_percent": 70.0,
               "day_buy_kwh": 4.0, "sell_kwh": 5.0, "actual_load_kwh": 9.0}
    result = repository._build_firestore_daily_reviews(
        client, end_date_iso=DAY, energy_daily=daily,
        battery_daily=[{"date": DAY, **battery}] if battery else [],
        forecast_hourly=rows, daily_metrics=[metrics],
    )[0]
    assert len(client.reads) == 1  # Existing selected plan read; no extra identity query.
    return result, daily


@pytest.mark.parametrize("case,expected", [
    ("matched", "matched"), ("wrong_plan", "mismatched"),
    ("mixed_hash", "mismatched"), ("hash_missing", "legacy_unknown"),
    ("known_and_missing", "legacy_unknown"), ("historical_run", "matched"),
    ("legacy", "legacy_unknown"), ("unknown_selected_mixed_run", "mismatched"),
    ("unknown_selected_missing_run", "legacy_unknown"),
    ("invalid_forecast_hash", "legacy_unknown"), ("invalid_plan_hash", "legacy_unknown"),
])
def test_review_binding_keeps_the_selected_energy_pair(case, expected):
    rows = hourly()
    plan_hash, selected_run = HASH, "selected"
    if case == "wrong_plan":
        plan_hash = OTHER_HASH
    elif case == "mixed_hash":
        rows[0]["_raw_plan_sha256"] = OTHER_HASH
    elif case == "hash_missing":
        plan_hash = None
    elif case == "known_and_missing":
        rows[0]["_raw_plan_sha256"] = None
    elif case == "historical_run":
        rows += hourly(raw_hash=OTHER_HASH, run="old")
    elif case == "legacy":
        for row in rows:
            row["_raw_plan_sha256"] = None
            row["forecast_run_id"] = None
        selected_run = None
    elif case == "unknown_selected_mixed_run":
        rows[0]["forecast_run_id"] = "old"
        selected_run = None
    elif case == "unknown_selected_missing_run":
        rows[0]["forecast_run_id"] = None
        selected_run = None
    elif case == "invalid_forecast_hash":
        for row in rows:
            row["_raw_plan_sha256"] = "not-a-sha"
    elif case == "invalid_plan_hash":
        plan_hash = "not-a-sha"
    original = deepcopy(rows)
    result, daily = review(rows, plan_hash=plan_hash, selected_run=selected_run)
    assert result["plan_forecast_binding"] == expected
    assert rows == original
    assert result["forecast_pv_kwh"] == daily[0]["forecast_pv_kwh"]
    assert result["forecast_load_kwh"] == daily[0]["forecast_load_kwh"]
    assert result["actual_pv_kwh"] == 8.0 and result["actual_load_kwh"] == 9.0
    assert result["actual_day_buy_kwh"] == 4.0 and result["actual_sell_kwh"] == 5.0
    assert result["actual_morning_soc_percent"] == 70.0
    for field in ("forecast_day_buy_kwh", "forecast_sell_kwh", "target_soc_percent", "forecast_night_charge_kwh"):
        assert (result[field] is None) == (expected == "mismatched")


def test_mismatch_preserves_applied_target_and_charge():
    result, _ = review(hourly(), plan_hash=OTHER_HASH,
                       battery={"setting_soc_target_percent": 45.0, "night_charge_kwh": 0.0})
    assert result["plan_forecast_binding"] == "mismatched"
    assert result["target_soc_percent"] == 45.0
    assert result["forecast_night_charge_kwh"] == 0.0


@pytest.mark.parametrize("count", [0, 23, 24])
def test_forecast_coverage_counts_valid_intervals_without_zero_fill(count):
    rows = hourly()
    for row in rows[count:]:
        row["forecast_pv_kwh"] = None
    result, daily = review(rows)
    assert result["forecast_available_hour_count"] == count
    assert daily[0]["forecast_available_hour_count"] == count


def test_legacy_daily_has_no_invented_interval_count():
    daily = _build_energy_daily(start_date=DAY, end_date_iso=DAY,
                               pv_daily=[{"date": DAY, "forecast_pv_total_kwh": 2.0}],
                               monitoring_daily=[])
    assert daily[0]["forecast_available_hour_count"] is None


@pytest.mark.parametrize("invalid", [True, "0", float("nan"), float("inf")])
def test_coverage_rejects_invalid_energy_but_keeps_valid_zero(invalid):
    rows = hourly()
    rows[0]["forecast_pv_kwh"] = 0.0
    rows[1]["forecast_load_kwh"] = invalid
    daily = _build_energy_daily(start_date=DAY, end_date_iso=DAY, pv_daily=[],
                               monitoring_daily=[], forecast_hourly=rows)
    assert daily[0]["forecast_available_hour_count"] == 23


def test_api_strips_only_private_identity_without_mutating_raw():
    rows = hourly()
    _, daily = review(rows)
    raw = DashboardRawData(pv_daily=[], cost_daily=[], cost_monthly=[], battery_daily=[],
                           model_parameters=[], battery_flow_daily=[], energy_daily=daily,
                           forecast_hourly=rows, latest_schedule={}, global_oldest=DAY, global_newest=DAY)
    before = deepcopy(raw)
    payload = assemble_dashboard_slice(raw, meta={}, warnings=[]).data
    assert raw == before
    for row in payload.energy_daily + payload.forecast_hourly:
        assert not MARKERS.intersection(row)
    assert payload.forecast_hourly[0]["forecast_run_id"] == "selected"
    assert payload.energy_daily[0]["forecast_available_hour_count"] == 24


def test_firestore_reader_retains_identity_without_an_extra_query():
    rows = [{**row, "raw_plan_sha256": row["_raw_plan_sha256"],
             "control_version_id": row["_control_version_id"],
             "generated_at": row["_plan_generated_at"]} for row in hourly()]
    client = Client({"forecast_hourly": {str(row["hour"]): row for row in rows}})
    selected = repository._firestore_forecast_hourly_between(client, start_date=DAY, end_date_iso=DAY)
    assert client.reads == ["forecast_hourly", "forecast_hourly_snapshots", "monitoring_samples"]
    assert len(selected) == 24
    assert all(row["_raw_plan_sha256"] == HASH for row in selected)
    assert all("raw_plan_sha256" not in row for row in selected)


def test_immutable_snapshot_reader_preserves_existing_raw_identity():
    rows = [{**row, "raw_plan_sha256": HASH, "control_version_id": "v1",
             "generated_at": f"{DAY}T03:00:00+09:00", "issued_at": f"{DAY}T03:00:00+09:00"}
            for row in hourly()]
    client = Client({"forecast_hourly_snapshots": {str(row["hour"]): row for row in rows}})
    selected = repository._firestore_forecast_hourly_between(client, start_date=DAY, end_date_iso=DAY)
    assert len(selected) == 24
    assert all(row["_raw_plan_sha256"] == HASH for row in selected)
    result, _ = review(selected)
    assert result["plan_forecast_binding"] == "matched"
