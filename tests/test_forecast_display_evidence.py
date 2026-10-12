import copy
import gzip
import hashlib
import json
import time
from types import SimpleNamespace

import pytest
from google.api_core.datetime_helpers import DatetimeWithNanoseconds
from google.protobuf.timestamp_pb2 import Timestamp

from app.energy_plan.soc_cost import simulate_daytime_soc_percent
from app.operations.forecast_snapshot import build_forecast_snapshot_rows
from app.separated_runtime.plan import PublishedPlan
from scripts.validate_forecast_display_evidence import (
    _original_hourly,
    _native,
    load_forecast_evidence_for_window,
    validate_forecast_day_evidence,
    validate_forecast_window_evidence,
)


DAY = "2026-10-11"


def fixture(*, new=False):
    plan = {"generated_at": "2026-10-10T21:00:00+09:00", "forecast": {"date": DAY,
            "hourly_weather": [{"hour": hour} for hour in range(24)]},
        "result": {"target_soc_7_percent": 50.0, "required_night_charge_kwh": 0.0,
                   "effective_capacity_kwh": 10.0, "final_pv_forecast_source": "fixture-selected"},
        "daytime_soc_optimization": {"hourly_pv_forecast_kwh": {str(hour): 0.4 if 8 <= hour <= 15 else 0.0 for hour in range(24)},
                                      "hourly_load_forecast_kwh": {str(hour): 0.1 for hour in range(24)}}}
    raw = json.dumps(plan, separators=(",", ":"), allow_nan=False).encode()
    stored = build_forecast_snapshot_rows(plan, ingested_at=plan["generated_at"], timezone="UTC")
    soc = simulate_daytime_soc_percent(target_soc_percent=50.0, capacity_kwh=10.0,
        hourly_pv_kwh={hour: 0.4 if 8 <= hour <= 15 else 0.0 for hour in range(24)},
        hourly_load_kwh={hour: 0.1 for hour in range(24)})
    metadata = {"date": DAY, "forecast_run_id": stored[0]["forecast_run_id"],
                "forecast_issued_at": plan["generated_at"], "planned_target_soc_percent": 50.0,
                "planned_night_charge_kwh": 0.0, "planned_capacity_kwh": 10.0,
                "planned_hourly_soc_percent": {str(hour): value for hour, value in soc.items()}}
    published = PublishedPlan.from_bytes(raw, target_date=DAY, charge_rate_info={"percent_per_hour": 10.0},
                                         producer_source_revision="fixture", inputs_verified=True)
    if new:
        metadata.update(raw_plan_sha256=hashlib.sha256(raw).hexdigest(), control_version_id=published.version_id)
    rows = [{**row, "source": "forecast_hourly_snapshot", "forecast_issued_at": plan["generated_at"],
             "forecast_target_soc_percent": 50.0, "forecast_night_charge_kwh": 0.0,
             "forecast_capacity_kwh": 10.0, **({"forecast_soc_percent": soc[row["hour"]]} if row["hour"] in soc else {})}
            for row in stored]
    daily = {"date": DAY, "setting_soc_target_percent": 60.0, "night_charge_kwh": 0.0,
             "plan_display_source": "forecast_plans"}
    evidence = {"target_date": DAY, "metadata_exists": True, "metadata": metadata, "stored_hourly": stored,
                "stored_battery_daily_exists": True, "stored_battery_daily": {"date": DAY, "setting_soc_target_percent": 60.0},
                "original": raw, "original_sha256": hashlib.sha256(raw).hexdigest(), "original_generation": None,
                "published_plan_document": published.to_document() if new else None,
                "forecast_identity": {"target_date": DAY, "forecast_run_id": metadata["forecast_run_id"],
                    "forecast_issued_at": metadata["forecast_issued_at"], "raw_plan_sha256": metadata.get("raw_plan_sha256"),
                    "control_version_id": metadata.get("control_version_id")},
                "evidence_kind": "NEW_ADOPTED" if new else "LEGACY_ORIGINAL", "revisions_before": {"native": "v1"},
                "revisions_after": {"native": "v1"}, "native_reads_complete": True,
                "read_count": 1, "read_bytes": len(raw), "failure_reasons": []}
    return plan, evidence, rows, daily


def check(evidence, rows, daily):
    return validate_forecast_day_evidence(target_date=DAY, evidence=evidence,
                                         displayed_hourly=rows, displayed_battery_daily=daily)


def old_shape_fixture():
    plan, evidence, rows, daily = fixture()
    for key in ("planned_capacity_kwh", "planned_hourly_soc_percent"):
        evidence["metadata"].pop(key)
    for row in rows + evidence["stored_hourly"]:
        row.pop("forecast_capacity_kwh", None)
        row.pop("forecast_soc_percent", None)
    return plan, evidence, rows, daily


def test_legacy_absent_capacity_soc_has_no_fabricated_coverage_and_checks_daily_nulls():
    _, evidence, rows, daily = old_shape_fixture()
    result = check(evidence, rows, daily)
    assert result["status"] == "PASS"
    assert result["original_verified"] and result["metadata_verified"]
    assert result["capacity_verified"] is False
    assert result["required_soc_points"] == result["soc_points_verified"] == 0
    assert result["daily_metadata_fields_verified"] == 1
    assert all(row.get("forecast_capacity_kwh") is None and row.get("forecast_soc_percent") is None
               for row in rows)
    daily["night_charge_kwh"] = 0.0003
    assert "night_charge_kwh:daily_mismatch" in check(evidence, rows, daily)["failure_reasons"]


@pytest.mark.parametrize("partial", ["capacity_null", "soc_null", "capacity_only", "soc_only",
                                    "display_capacity", "display_soc", "stored_capacity", "stored_soc", "new"])
def test_legacy_old_shape_exception_does_not_accept_new_or_partial_evidence(partial):
    _, evidence, rows, daily = old_shape_fixture()
    if partial == "new":
        evidence["evidence_kind"] = "NEW_ADOPTED"
    elif partial in {"capacity_null", "capacity_only"}:
        evidence["metadata"]["planned_capacity_kwh"] = None if partial.endswith("null") else 10.0
    elif partial in {"soc_null", "soc_only"}:
        evidence["metadata"]["planned_hourly_soc_percent"] = None if partial.endswith("null") else {"7":50.0}
    else:
        destination = rows if partial.startswith("display") else evidence["stored_hourly"]
        field = "forecast_capacity_kwh" if partial.endswith("capacity") else "forecast_soc_percent"
        destination[7][field] = 10.0 if field.endswith("kwh") else 0.0
    result = check(evidence, rows, daily)
    assert result["status"] == "FAIL"
    assert result["required_soc_points"] == 17


@pytest.mark.parametrize("field", ["planned_target_soc_percent", "planned_night_charge_kwh"])
def test_legacy_old_shape_original_metadata_mismatch_is_still_fail(field):
    _, evidence, rows, daily = old_shape_fixture()
    evidence["metadata"][field] += 0.000219208414091
    assert f"{field}:original_mismatch" in check(evidence, rows, daily)["failure_reasons"]


@pytest.mark.parametrize("invalid", [None, 0.0, True, "10", -1.0])
def test_legacy_old_shape_still_requires_valid_original_capacity(invalid):
    plan, evidence, rows, daily = old_shape_fixture()
    plan["result"]["effective_capacity_kwh"] = invalid
    evidence["original"] = json.dumps(plan).encode()
    evidence["original_sha256"] = hashlib.sha256(evidence["original"]).hexdigest()
    identity = build_forecast_snapshot_rows(plan, ingested_at=plan["generated_at"], timezone="UTC")[0]["forecast_run_id"]
    evidence["metadata"]["forecast_run_id"] = identity
    for row in rows + evidence["stored_hourly"]:
        row["forecast_run_id"] = identity
    result = check(evidence, rows, daily)
    assert result["status"] == "FAIL"
    assert "effective_capacity_kwh:invalid_numeric" in result["failure_reasons"]


def unavailable_old_shape_fixture():
    _, evidence, rows, daily = old_shape_fixture()
    evidence.update(original=None, original_sha256=None, evidence_kind="LEGACY_UNAVAILABLE",
                    failure_reasons=["original:unavailable_legacy"])
    return evidence, rows, daily


def window(evidence, rows, daily):
    return validate_forecast_window_evidence(start_date=DAY, end_date=DAY,
        evidence_by_date={DAY: evidence}, displayed_hourly=rows, displayed_battery_daily=[daily])


def test_historical_original_gap_remains_inconclusive_outside_required_scope():
    evidence, rows, daily = unavailable_old_shape_fixture()
    result = window(evidence, rows, daily)
    assert result["status"] == "INCONCLUSIVE"
    assert result["required_evidence_status"] == "PASS"
    day = result["days"][DAY]
    assert day["evidence_required"] is False and day["status"] == "INCONCLUSIVE"
    assert day["original_verified"] is False
    assert day["failure_reasons"] == ["original:unavailable_legacy"]
    assert result["coverage"]["legacy_unavailable_days"] == 1
    assert result["coverage"]["new_required_days"] == result["coverage"]["new_verified_days"] == 0


@pytest.mark.parametrize("block", ["new", "capacity_key_null", "soc_key_null", "capacity_value",
                                  "soc_value", "native_capacity", "native_soc", "metadata_type",
                                  "identity", "incomplete_hourly", "read_unknown", "drift", "incomplete_reads"])
def test_old_schema_scope_exclusion_requires_full_valid_native_evidence(block):
    evidence, rows, daily = unavailable_old_shape_fixture()
    if block == "new":
        evidence["evidence_kind"] = "NEW_ADOPTED"
    elif block in {"capacity_key_null", "soc_key_null"}:
        field = "planned_capacity_kwh" if block.startswith("capacity") else "planned_hourly_soc_percent"
        evidence["metadata"][field] = None
    elif block in {"capacity_value", "soc_value", "native_capacity", "native_soc"}:
        target = evidence["stored_hourly"] if block.startswith("native") else rows
        field = "forecast_capacity_kwh" if "capacity" in block else "forecast_soc_percent"
        target[7][field] = 10.0 if "capacity" in block else 0.0
    elif block == "metadata_type":
        evidence["metadata"]["planned_night_charge_kwh"] = "0"
    elif block == "identity":
        rows[0]["forecast_run_id"] = "another-run"
    elif block == "incomplete_hourly":
        evidence["stored_hourly"].pop()
    elif block == "read_unknown":
        evidence["failure_reasons"].append("collector:read_unknown")
    elif block == "drift":
        evidence["revisions_after"] = {"native": "v2"}
    else:
        evidence["native_reads_complete"] = False
    result = window(evidence, rows, daily)
    assert result["days"][DAY]["evidence_required"] is True
    assert result["required_evidence_status"] != "PASS"


def test_same_generation_legacy_numeric_mismatch_is_required_failure():
    _, evidence, rows, daily = old_shape_fixture()
    evidence["metadata"]["planned_night_charge_kwh"] = 0.0003
    result = window(evidence, rows, daily)
    assert result["status"] == result["required_evidence_status"] == "FAIL"
    assert result["days"][DAY]["evidence_required"] is True


def test_new_adopted_is_required_and_all_capacity_soc_points_are_verified():
    _, evidence, rows, daily = fixture(new=True)
    result = window(evidence, rows, daily)
    assert result["status"] == result["required_evidence_status"] == "PASS"
    assert result["days"][DAY]["evidence_required"] is True
    assert result["coverage"]["new_required_days"] == result["coverage"]["new_verified_days"] == 1
    assert result["coverage"]["capacity_points_verified"] == 24
    assert result["coverage"]["soc_points_verified"] == 17


@pytest.mark.parametrize("new", [False, True])
def test_original_rounding_matches_saved_projection(new):
    _, evidence, rows, daily = fixture(new=new)
    before = copy.deepcopy((evidence, rows, daily))
    result = check(evidence, rows, daily)
    assert result["status"] == "PASS"
    assert result["soc_points_verified"] == 17
    assert result["daily_metadata_fields_verified"] == 1
    assert rows[23]["forecast_soc_percent"] == pytest.approx(66.0)
    assert (evidence, rows, daily) == before


@pytest.mark.parametrize("field,value", [("planned_capacity_kwh", 0), ("planned_capacity_kwh", True),
    ("planned_capacity_kwh", "10"), ("planned_capacity_kwh", float("nan")),
    ("planned_capacity_kwh", float("inf")), ("planned_target_soc_percent", -1),
    ("planned_target_soc_percent", 101), ("planned_night_charge_kwh", -1)])
def test_invalid_numeric_evidence_fails(field, value):
    _, evidence, rows, daily = fixture()
    evidence["metadata"][field] = value
    assert check(evidence, rows, daily)["status"] == "FAIL"


@pytest.mark.parametrize("mutation", ["capacity", "soc", "run", "sha", "daily_existing", "daily_null", "partial_soc", "version"])
def test_capacity_soc_and_generation_drift_not_ignored(mutation):
    _, evidence, rows, daily = fixture(new=True)
    if mutation == "capacity":
        rows[8]["forecast_capacity_kwh"] *= 1.01
    elif mutation == "soc":
        rows[23]["forecast_soc_percent"] += 1
    elif mutation == "run":
        rows[4]["forecast_run_id"] = "different"
    elif mutation == "sha":
        evidence["original_sha256"] = "0" * 64
    elif mutation == "daily_existing":
        daily["setting_soc_target_percent"] = 50.0
    elif mutation == "daily_null":
        daily["night_charge_kwh"] = 2.0
    elif mutation == "partial_soc":
        del evidence["metadata"]["planned_hourly_soc_percent"]["23"]
    else:
        evidence["metadata"]["control_version_id"] = "0" * 64
    assert check(evidence, rows, daily)["status"] == "FAIL"


def test_raw_charge_rounding_uses_raw_difference_and_legacy_missing_is_not_zero():
    plan, _, _, _ = fixture()
    plan["daytime_soc_optimization"]["hourly_pv_forecast_kwh"]["8"] = 0.30006
    plan["daytime_soc_optimization"]["hourly_load_forecast_kwh"]["8"] = 0.20004
    result = _original_hourly(plan, DAY)
    assert result[8] == {"forecast_pv_kwh": 0.3001, "forecast_load_kwh": 0.2, "forecast_charge_kwh": 0.1}
    assert round(result[8]["forecast_pv_kwh"] - result[8]["forecast_load_kwh"], 4) == 0.1001
    pv = plan["daytime_soc_optimization"]["hourly_pv_forecast_kwh"]
    for hour in (*range(7), 23):
        del pv[str(hour)]
    assert _original_hourly(plan, DAY)[0]["forecast_pv_kwh"] == 0
    del pv["10"]
    with pytest.raises(ValueError, match="missing_or_invalid_energy"):
        _original_hourly(plan, DAY)


def test_soc_pulse_energy_balance_and_closing_23_are_independent():
    # Start 5kWh. Hour 8 +2kWh; hour 22 -1kWh. SOC[23] closes hour 22.
    soc = simulate_daytime_soc_percent(target_soc_percent=50, capacity_kwh=10,
        hourly_pv_kwh={8: 2.0}, hourly_load_kwh={22: 1.0})
    assert soc[7] == 50
    assert soc[8] == 50
    assert soc[9] == 70
    assert soc[22] == 70
    assert soc[23] == 60


@pytest.mark.parametrize("new,expected", [(False, "INCONCLUSIVE"), (True, "FAIL")])
def test_missing_original_is_not_pass(new, expected):
    _, evidence, rows, daily = fixture(new=new)
    evidence["original"] = None
    assert check(evidence, rows, daily)["status"] == expected


def test_metadata_absence_is_distinct_from_invalid_doc_and_rpc_unknown():
    _, evidence, rows, daily = fixture()
    rows = [{"date": DAY, "hour": 7, "forecast_pv_kwh": 0.0, "forecast_load_kwh": 0.1}]
    evidence.update(metadata_exists=False, metadata={}, original=None)
    result = check(evidence, rows, daily)
    assert result["status"] == "PASS"
    assert result["evidence_kind"] == "LEGACY_NO_PLAN_METADATA"
    evidence["metadata_exists"] = True
    assert check(evidence, rows, daily)["status"] == "FAIL"


def test_native_new_marker_cannot_hide_behind_public_rows_without_model_label():
    _, evidence, _, daily = fixture(new=True)
    rows = [{"date": DAY, "hour": 7, "forecast_pv_kwh": 0.0, "forecast_load_kwh": 0.1}]
    evidence.update(metadata_exists=False, metadata={})
    assert check(evidence, rows, daily)["status"] == "FAIL"
    evidence["metadata_exists"] = None
    assert check(evidence, rows, daily)["status"] == "INCONCLUSIVE"
    evidence["metadata_exists"] = False
    rows[0]["pv_input_source"] = "physical_clear_ghi_annual1"
    assert check(evidence, rows, daily)["status"] == "FAIL"


@pytest.mark.parametrize("value", [True, "1", float("nan"), float("inf"), -1])
def test_pure_legacy_business_types_are_strict(value):
    _, evidence, _, daily = fixture()
    evidence.update(metadata_exists=False, metadata={}, original=None)
    assert check(evidence, [{"date": DAY, "hour": 7, "forecast_pv_kwh": value}], daily)["status"] == "FAIL"


def test_actual_only_rows_remain_in_numeric_validation_and_coverage():
    _, evidence, _, _ = fixture()
    evidence.update(metadata_exists=False, metadata={}, original=None)
    rows = [{"date": DAY, "hour": 0, "actual_pv_kwh": 0.0, "actual_load_kwh": None,
             "forecast_run_id": None, "forecast_pv_kwh": None}]
    result = validate_forecast_window_evidence(start_date=DAY, end_date=DAY, evidence_by_date={DAY: evidence},
                                              displayed_hourly=rows, displayed_battery_daily=[])
    assert result["status"] == "PASS"
    assert result["coverage"]["actual_only_rows"] == 1
    assert result["coverage"]["selected_forecast_days"] == 0
    assert result["coverage"]["new_required_days"] == 0


def test_selected_snapshot_drift_is_inconclusive_without_mixed_value_checks():
    _, evidence, rows, daily = fixture(new=True)
    evidence["revisions_after"] = {"native": "v2"}
    result = check(evidence, rows, daily)
    assert result["status"] == "INCONCLUSIVE"
    assert result["failure_reasons"] == ["snapshot_drift"]


class Snapshot:
    def __init__(self, key, value=None, version="2026-10-11T00:00:00Z"):
        self.id, self.value = key, copy.deepcopy(value)
        self.exists = value is not None
        self.update_time = version if self.exists else None

    def to_dict(self):
        return copy.deepcopy(self.value)


class Query:
    def __init__(self, client, name, filters=(), maximum=None):
        self.client, self.name, self.filters, self.maximum = client, name, filters, maximum

    def where(self, key, op, value):
        return Query(self.client, self.name, self.filters + ((key, op, value),), self.maximum)

    def limit(self, maximum):
        return Query(self.client, self.name, self.filters, maximum)

    def document(self, key):
        def get(**kwargs):
            self.client.calls.append(("get", self.name, kwargs))
            assert 0 < kwargs["timeout"] <= 3 and kwargs["retry"] is None
            return Snapshot(key, self.client.values.get(self.name, {}).get(key))
        return SimpleNamespace(get=get, path=f"{self.name}/{key}")

    def stream(self, **kwargs):
        self.client.calls.append(("stream", self.name, kwargs))
        assert 0 < kwargs["timeout"] <= 3 and kwargs["retry"] is None
        rows = []
        for key, row in self.client.values.get(self.name, {}).items():
            good = True
            for field, op, value in self.filters:
                current = row.get(field)
                good &= current is not None and ({"==": lambda: current == value,
                    ">=": lambda: current >= value, "<=": lambda: current <= value}[op]())
            if good:
                rows.append(Snapshot(key, row))
        return rows[:self.maximum]


class Client:
    def __init__(self, evidence):
        sha = evidence["original_sha256"]
        self.values = {"forecast_plans": {DAY: evidence["metadata"]},
            "forecast_hourly_snapshots": {row["snapshot_id"]: row for row in evidence["stored_hourly"]},
            "battery_daily_metrics": {DAY: evidence["stored_battery_daily"]},
            "night_charge_plans": {DAY: {"date": DAY, "detail_sha256": sha, "detail_uri": "gs://fixture/original.json.gz"}},
            "night_plan_decisions": {f"{DAY}--{sha}": {"date": DAY, "detail_sha256": sha,
                                                      "detail_uri": "gs://fixture/original.json.gz"}},
            "control_plan_versions": {evidence["metadata"].get("control_version_id"): {
                "payload": json.dumps(evidence["published_plan_document"])}}}
        self.calls = []

    def collection(self, name):
        return Query(self, name)

    def get_all(self, references, **kwargs):
        self.calls.append(("get_all", tuple(ref.path for ref in references), kwargs))
        assert 0 < kwargs["timeout"] <= 3 and kwargs["retry"] is None
        result = []
        for ref in references:
            collection, key = ref.path.split("/", 1)
            snapshot = Snapshot(key, self.values.get(collection, {}).get(key))
            snapshot.reference = ref
            result.append(snapshot)
        # BatchGet does not promise caller ordering.
        return reversed(result)


class Blob:
    def __init__(self, raw):
        self.raw, self.generation, self.calls = gzip.compress(raw), 7, []

    def reload(self, **kwargs):
        self.calls.append(("reload", kwargs))

    def download_as_bytes(self, **kwargs):
        self.calls.append(("download", kwargs))
        assert kwargs["if_generation_match"] == 7
        assert kwargs["raw_download"] is True
        assert kwargs["retry"] is None and 0 < kwargs["timeout"] <= 4
        return self.raw


@pytest.mark.parametrize("new", [False, True])
def test_collector_is_read_only_pins_original_and_full_native_rows(new):
    _, evidence, rows, daily = fixture(new=new)
    client, blob = Client(evidence), Blob(evidence["original"])
    storage = SimpleNamespace(bucket=lambda _: SimpleNamespace(blob=lambda _: blob))
    collected = load_forecast_evidence_for_window(client=client, storage=storage, start_date=DAY, end_date=DAY,
                                                  selected_hourly=rows, deadline=time.monotonic() + 60)
    assert check(collected[DAY], rows, daily)["status"] == "PASS"
    assert len([call for call in blob.calls if call[0] == "download"]) == 1
    assert collected[DAY]["revisions_before"] == collected[DAY]["revisions_after"]
    assert len([key for key in collected[DAY]["revisions_before"] if key.startswith("forecast_hourly_snapshots/")]) == 24
    assert all(call[0] in {"get_all", "stream"} for call in client.calls)
    assert len([call for call in client.calls if call[0] == "get_all"]) == 4
    assert not any(call[0] == "get" for call in client.calls)


def test_collector_frozen_original_has_no_gcs_read(tmp_path):
    _, evidence, rows, daily = fixture()
    (tmp_path / "original.json").write_bytes(evidence["original"])
    collected = load_forecast_evidence_for_window(client=Client(evidence), storage=None,
        start_date=DAY, end_date=DAY, selected_hourly=rows, original_dir=tmp_path, deadline=time.monotonic() + 60)
    assert check(collected[DAY], rows, daily)["status"] == "PASS"


def vintage_client(*, matching=1):
    plan, evidence, rows, daily = fixture()
    client = Client(evidence)
    latest_plan = copy.deepcopy(plan)
    latest_plan["generated_at"] = "2026-10-10T21:30:00+09:00"
    latest_raw = json.dumps(latest_plan).encode()
    client.values["night_charge_plans"][DAY].update(
        generated_at=latest_plan["generated_at"], detail_sha256=hashlib.sha256(latest_raw).hexdigest(),
        detail_uri="gs://fixture/latest.json.gz")
    prior = {"date": DAY, "generated_at": plan["generated_at"],
             "detail_sha256": evidence["original_sha256"], "detail_uri": "gs://fixture/prior.json.gz"}
    client.values["night_plan_decisions"] = {f"prior-{n}": copy.deepcopy(prior) for n in range(matching)}
    client.values["night_plan_decisions"]["latest"] = copy.deepcopy(client.values["night_charge_plans"][DAY])
    blobs = {"prior.json.gz": Blob(evidence["original"]), "latest.json.gz": Blob(latest_raw)}
    storage = SimpleNamespace(bucket=lambda _: SimpleNamespace(blob=lambda name: blobs[name]))
    return evidence, rows, daily, client, blobs, storage


def test_legacy_archive_selects_same_issued_immutable_original_instead_of_latest():
    evidence, rows, daily, client, blobs, storage = vintage_client()
    collected = load_forecast_evidence_for_window(client=client, storage=storage,
        start_date=DAY, end_date=DAY, selected_hourly=rows, deadline=time.monotonic() + 60)
    assert check(collected[DAY], rows, daily)["status"] == "PASS"
    assert collected[DAY]["original_sha256"] == evidence["original_sha256"]
    assert not blobs["latest.json.gz"].calls
    revisions = collected[DAY]["revisions_before"]
    assert "night_plan_decisions/prior-0" in revisions and "night_plan_decisions/latest" in revisions
    assert revisions == collected[DAY]["revisions_after"]


def test_legacy_wrong_vintage_without_matching_archive_is_unavailable_not_value_failure():
    _, rows, daily, client, blobs, storage = vintage_client(matching=0)
    collected = load_forecast_evidence_for_window(client=client, storage=storage,
        start_date=DAY, end_date=DAY, selected_hourly=rows, deadline=time.monotonic() + 60)
    result = check(collected[DAY], rows, daily)
    assert result["status"] == "INCONCLUSIVE"
    assert result["evidence_kind"] == "LEGACY_UNAVAILABLE"
    assert result["original_verified"] is False and result["original_sha256"] is None
    assert result["failure_reasons"] == ["original:unavailable_legacy"]
    assert not any(blob.calls for blob in blobs.values())


@pytest.mark.parametrize("matching", [2, 33])
def test_legacy_archive_ambiguity_or_sentinel_cannot_pass(matching):
    _, rows, _, client, blobs, storage = vintage_client(matching=matching)
    with pytest.raises(ValueError, match="legacy_archive_"):
        load_forecast_evidence_for_window(client=client, storage=storage,
            start_date=DAY, end_date=DAY, selected_hourly=rows, deadline=time.monotonic() + 60)
    assert not any(blob.calls for blob in blobs.values())


def test_legacy_same_vintage_numeric_corruption_is_not_classified_as_unavailable():
    _, rows, daily, client, _, storage = vintage_client()
    client.values["forecast_plans"][DAY]["planned_night_charge_kwh"] += 0.0003
    collected = load_forecast_evidence_for_window(client=client, storage=storage,
        start_date=DAY, end_date=DAY, selected_hourly=rows, deadline=time.monotonic() + 60)
    result = check(collected[DAY], rows, daily)
    assert result["status"] == "FAIL"
    assert result["evidence_kind"] == "LEGACY_ORIGINAL"
    assert "planned_night_charge_kwh:original_mismatch" in result["failure_reasons"]


def test_legacy_archive_candidate_changes_are_part_of_native_drift(monkeypatch):
    _, rows, daily, client, _, storage = vintage_client()
    original_stream = Query.stream
    seen = 0

    def changing(query, **kwargs):
        nonlocal seen
        if query.name == "night_plan_decisions":
            seen += 1
            if seen == 2:
                query.client.values[query.name]["prior-0"]["source"] = "changed"
        return original_stream(query, **kwargs)

    monkeypatch.setattr(Query, "stream", changing)
    collected = load_forecast_evidence_for_window(client=client, storage=storage,
        start_date=DAY, end_date=DAY, selected_hourly=rows, deadline=time.monotonic() + 60)
    assert check(collected[DAY], rows, daily)["failure_reasons"] == ["snapshot_drift"]


def test_legacy_pointer_without_generated_stamp_still_rejects_wrong_decoded_run():
    _, rows, daily, client, blobs, storage = vintage_client()
    client.values["night_charge_plans"][DAY].pop("generated_at")
    collected = load_forecast_evidence_for_window(client=client, storage=storage,
        start_date=DAY, end_date=DAY, selected_hourly=rows, deadline=time.monotonic() + 60)
    assert blobs["latest.json.gz"].calls
    assert check(collected[DAY], rows, daily)["status"] == "INCONCLUSIVE"
    assert collected[DAY]["original"] is None


@pytest.mark.parametrize("wrong_batch", ["missing", "duplicate", "foreign"])
def test_exact_batch_reference_set_is_required(monkeypatch, wrong_batch):
    _, evidence, rows, _ = fixture()
    client = Client(evidence)
    original = client.get_all

    def broken(references, **kwargs):
        snapshots = list(original(references, **kwargs))
        if wrong_batch == "missing":
            return []
        if wrong_batch == "duplicate":
            return snapshots + snapshots
        snapshots[0].reference = SimpleNamespace(path="foreign/document")
        return snapshots

    monkeypatch.setattr(client, "get_all", broken)
    with pytest.raises(ValueError, match="native_reference_"):
        load_forecast_evidence_for_window(client=client, storage=None, start_date=DAY,
            end_date=DAY, selected_hourly=rows, deadline=time.monotonic() + 60)


def test_collector_timeout_records_safe_internal_stage_without_message(monkeypatch):
    _, evidence, rows, daily = fixture()
    client = Client(evidence)

    def unavailable(*args, **kwargs):
        raise TimeoutError("private endpoint or account detail")

    monkeypatch.setattr(client, "get_all", unavailable)
    collected = load_forecast_evidence_for_window(client=client, storage=None, start_date=DAY,
        end_date=DAY, selected_hourly=rows, deadline=time.monotonic() + 60)
    assert collected[DAY]["collector_error"] == {"exception_type": "TimeoutError", "stage": "native_before"}
    result = check(collected[DAY], rows, daily)
    assert result["status"] == "INCONCLUSIVE"
    assert "collector_error" not in result
    assert "private endpoint" not in json.dumps(result)


def test_collector_deadline_is_non_success_and_has_no_write():
    _, evidence, rows, daily = fixture()
    client = Client(evidence)
    collected = load_forecast_evidence_for_window(client=client, storage=None, start_date=DAY, end_date=DAY,
                                                  selected_hourly=rows, deadline=time.monotonic() - 1)
    assert check(collected[DAY], rows, daily)["status"] == "INCONCLUSIVE"
    assert client.calls == []


def test_collector_rejects_oversize_gzip_without_expansion():
    _, evidence, rows, _ = fixture()
    blob = Blob(b"x" * 750_001)
    with pytest.raises(ValueError, match="original_size_or_gzip"):
        load_forecast_evidence_for_window(client=Client(evidence), storage=SimpleNamespace(
            bucket=lambda _: SimpleNamespace(blob=lambda _: blob)), start_date=DAY, end_date=DAY,
            selected_hourly=rows, deadline=time.monotonic() + 60)


def test_collector_rejects_metadata_sentinel_and_wrong_document_identity():
    _, evidence, rows, _ = fixture()
    client = Client(evidence)
    client.values["forecast_plans"] = {str(index): {**evidence["metadata"], "date": DAY} for index in range(32)}
    with pytest.raises(ValueError, match="metadata_limit"):
        load_forecast_evidence_for_window(client=client, storage=None, start_date=DAY, end_date=DAY,
                                          selected_hourly=rows, deadline=time.monotonic() + 60)
    client.values["forecast_plans"] = {"wrong": evidence["metadata"]}
    with pytest.raises(ValueError, match="metadata_document_identity"):
        load_forecast_evidence_for_window(client=client, storage=None, start_date=DAY, end_date=DAY,
                                          selected_hourly=rows, deadline=time.monotonic() + 60)


def test_known_metadata_with_both_backends_missing_rows_is_not_empty_pass():
    _, evidence, _, daily = fixture(new=True)
    result = validate_forecast_window_evidence(start_date=DAY, end_date=DAY, evidence_by_date={DAY: evidence},
                                              displayed_hourly=[], displayed_battery_daily=[daily])
    assert result["status"] == "FAIL"
    assert result["coverage"]["new_required_days"] == 1


def test_corrupt_control_payload_is_not_accepted_as_new_original():
    _, evidence, rows, daily = fixture(new=True)
    evidence["published_plan_document"]["raw_json"] += " "
    assert check(evidence, rows, daily)["status"] == "FAIL"


def test_native_hourly_hash_change_is_detected_without_metadata_change(monkeypatch):
    _, evidence, rows, daily = fixture()
    client, blob = Client(evidence), Blob(evidence["original"])
    stream = Query.stream
    calls = 0

    def mutate_second_hourly(query, **kwargs):
        nonlocal calls
        if query.name == "forecast_hourly_snapshots":
            calls += 1
            if calls == 2:
                first = next(iter(client.values[query.name].values()))
                first["forecast_pv_kwh"] = 0.9
        return stream(query, **kwargs)

    monkeypatch.setattr(Query, "stream", mutate_second_hourly)
    collected = load_forecast_evidence_for_window(client=client,
        storage=SimpleNamespace(bucket=lambda _: SimpleNamespace(blob=lambda _: blob)),
        start_date=DAY, end_date=DAY, selected_hourly=rows, deadline=time.monotonic() + 60)
    assert check(collected[DAY], rows, daily)["status"] == "INCONCLUSIVE"


def test_native_nanosecond_revision_change_cannot_be_rounded_away():
    first = Snapshot("same", {"value": 1}, DatetimeWithNanoseconds.from_timestamp_pb(Timestamp(seconds=1, nanos=1)))
    second = Snapshot("same", {"value": 1}, DatetimeWithNanoseconds.from_timestamp_pb(Timestamp(seconds=1, nanos=2)))
    assert _native(first)[1] != _native(second)[1]


def test_collector_pins_selected_reconstruction_without_reading_other_runs():
    _, evidence, rows, _ = fixture()
    client = Client(evidence)
    client.values["forecast_plans"] = {}
    selected = [{"date": DAY, "hour": hour, "forecast_pv_kwh": 0.0,
                 "forecast_load_kwh": 0.1, "source": "historical_reconstructed_estimate",
                 "forecast_reconstruction_id": "selected"} for hour in range(24)]
    client.values["forecast_hourly_reconstructed"] = {
        f"{run}-{hour}": {**row, "forecast_reconstruction_id": run}
        for run in ("selected", "older") for hour, row in enumerate(selected)}
    collected = load_forecast_evidence_for_window(client=client, storage=None, start_date=DAY, end_date=DAY,
                                                  selected_hourly=selected, deadline=time.monotonic() + 60)
    result = validate_forecast_window_evidence(start_date=DAY, end_date=DAY, evidence_by_date=collected,
                                              displayed_hourly=selected, displayed_battery_daily=[])
    assert result["status"] == "PASS"
    assert len(collected[DAY]["stored_hourly"]) == 24
    assert result["coverage"]["legacy_no_metadata_days"] == 1
