from __future__ import annotations

import ast
import builtins
import copy
import inspect
import json
from datetime import date, timedelta

import pytest

from app.dashboard import slice_assembler
from app.dashboard.slice_assembler import _build_pv_model_summary, extract_pv_forecast_diagnostics


MODEL = "physical_clear_ghi_annual1"
BASIS = "noaa_geometric_1min_midpoint_annual1_v1"
COMMON = {"schema_version", "status", "reason_code", "model_id", "plan_date", "generated_at",
          "forecast_run_id", "raw_plan_sha256", "control_version_id"}


def model_document():
    return {
        "generated_at": "2026-10-11T03:00:00Z", "forecast": {"date": "2026-10-11"},
        "result": {"final_pv_forecast_source": MODEL},
        "decision_rationale": {
            "pv_physical_forecast": {"selected_method": MODEL},
            "forecast_correction": {"consumption": "preserved"},
            "hourly_weather_pv_shape": {"enabled": False}, "overnight_discharge_guard": {"legacy": 1},
            "pv_clear_ghi": {
                "schema_version": 1, "model_id": MODEL, "basis_version": BASIS, "target_date": "2026-10-11",
                "forecast_sha256": "a" * 64, "calibration_sha256": "b" * 64,
                "retrieved_at": "2026-10-11T02:50:00Z", "as_of": "2026-10-11T02:55:00Z", "provider_issued_at": None,
                "site_fingerprint": "private-site", "hourly": [{"q_kwh_m2": 999}], "repaired_hours": [5, 6],
                "totals": {"full_day_pv_kwh": 10.5, "decision_day_pv_kwh": 9.0, "morning_pv_kwh": 2.0,
                           "midday_pv_kwh": 6.0, "evening_pv_kwh": 1.0},
                "calibration_pack": {
                    "schema_version": 1, "model_id": MODEL, "basis_version": BASIS,
                    "terms": ["1", "x", "cos_phi", "sin_phi"], "theta4": [-0.5, 0, 1, 2],
                    "training_dates": [(date(2026, 9, 24) + timedelta(days=offset)).isoformat() for offset in range(17)],
                    "training_samples": 221, "rank": 4, "condition_number": 2.5,
                    "training_as_of": "2026-10-10T02:00:00Z", "created_at": "2026-10-10T02:10:00Z",
                    "usable_from": "2026-10-10T02:20:00Z", "private_path": "PRIVATE-DO-NOT-TRANSFER",
                },
            },
        },
    }


def producer_identity(data):
    return {"target_date": data["forecast"]["date"], "generated_at": data["generated_at"],
            "raw_plan_sha256": "c" * 64, "control_version_id": "d" * 64}


def summary(data, **kwargs):
    return _build_pv_model_summary(data, producer_identity=kwargs.get("identity", producer_identity(data)),
                                   forecast_run_id=kwargs.get("run", "e" * 24))


def test_valid_summary_is_small_same_original_and_preserves_legacy_diagnostics():
    data = model_document()
    before = copy.deepcopy(data)
    value = extract_pv_forecast_diagnostics(data, producer_identity=producer_identity(data), forecast_run_id="e" * 24)
    result = value["pv_model"]
    assert result["status"] == "valid" and result["reason_code"] is None
    assert result["training_day_count"] == 17 and result["training_sample_count"] == 221
    assert result["training_start_date"] == "2026-09-24" and result["training_end_date"] == "2026-10-10"
    assert result["theta4"] == [-0.5, 0, 1, 2] and result["provider_issued_at"] is None
    assert result["totals"] == data["decision_rationale"]["pv_clear_ghi"]["totals"]
    assert result["raw_plan_sha256"] == "c" * 64 and result["control_version_id"] == "d" * 64
    assert value["physical"] == data["decision_rationale"]["pv_physical_forecast"]
    assert value["forecast_correction"] == {"consumption": "preserved"}
    assert value["hourly_weather_pv_shape"] == {"enabled": False} and value["overnight_load_forecast"] == {"legacy": 1}
    assert result == summary(data) == summary(data) and data == before
    serialized = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    assert len(serialized.encode()) <= 4096
    assert all(secret not in serialized for secret in ("training_dates", "hourly", "site_fingerprint", "PRIVATE", "q_kwh_m2"))


@pytest.mark.parametrize("source", ["physical_pv_forecast", "corrected_pv_forecast"])
def test_all_known_legacy_sources_have_only_common_shape(source):
    data = {"forecast": {"date": "2026-10-11"}, "result": {"final_pv_forecast_source": source}}
    value = _build_pv_model_summary(data)
    assert value["status"] == "legacy" and value["reason_code"] is None and set(value) == COMMON


@pytest.mark.parametrize("method", ["existing", "physical_unscaled", "physical_global", "physical_daypart", "physical_altitude_shortwave"])
def test_all_known_legacy_methods_without_recorded_source_are_supported(method):
    data = {"daytime_soc_optimization": {"pv_physical_forecast": {"selected_method": method}}}
    value = extract_pv_forecast_diagnostics(data)
    assert value["pv_model"]["status"] == "legacy" and value["physical"]["selected_method"] == method


@pytest.mark.parametrize("key", ["scales", "data_quality"])
def test_old_diagnostics_without_a_recorded_method_remain_legacy(key):
    value = _build_pv_model_summary({"decision_rationale": {"pv_physical_forecast": {key: {"old": 1}}}})
    assert value["status"] == "legacy" and value["model_id"] is None


@pytest.mark.parametrize("source", ["future_model", "z" * 10000, {"unexpected": "type"}, False])
def test_unknown_model_is_never_legacy_and_common_shape_is_bounded(source):
    data = model_document()
    data["result"]["final_pv_forecast_source"] = source
    result = summary(data)
    assert result["status"] == "unsupported" and result["reason_code"] == "unsupported_model"
    assert set(result) == COMMON and len(json.dumps(result).encode()) <= 4096
    if not isinstance(source, str) or len(source) > 128:
        assert result["model_id"] is None


def test_new_model_without_producer_or_run_is_unsupported_not_old_physics():
    data = model_document()
    assert extract_pv_forecast_diagnostics(data)["pv_model"]["reason_code"] == "producer_identity_unavailable"
    assert summary(data, run=None)["reason_code"] == "forecast_run_unavailable"
    assert summary(data, run="f" * 10000)["forecast_run_id"] is None
    data["decision_rationale"].pop("pv_clear_ghi")
    result = summary(data)
    assert result["reason_code"] == "clear_ghi_trace_missing" and set(result) == COMMON


@pytest.mark.parametrize("path,value", [
    (("schema_version",), True), (("model_id",), "physical_pv_forecast"), (("basis_version",), "wrong"),
    (("target_date",), "2026-10-12"), (("forecast_sha256",), "not-a-hash"), (("calibration_sha256",), "A" * 64),
    (("calibration_pack", "theta4"), [True, 0, 1, 2]), (("calibration_pack", "theta4"), [float("nan"), 0, 1, 2]),
    (("calibration_pack", "theta4"), [float("inf"), 0, 1, 2]), (("calibration_pack", "theta4"), [1, 2, 3]),
    (("calibration_pack", "training_samples"), True), (("calibration_pack", "training_samples"), 220),
    (("calibration_pack", "rank"), True), (("calibration_pack", "rank"), 3),
    (("calibration_pack", "condition_number"), float("inf")), (("calibration_pack", "terms"), ["wrong"] * 4),
    (("repaired_hours",), [True]), (("repaired_hours",), [5, 5]), (("repaired_hours",), [24]),
    (("totals", "full_day_pv_kwh"), float("nan")), (("totals", "morning_pv_kwh"), True),
    (("retrieved_at",), "2026-10-11T02:50:00"), (("provider_issued_at",), False),
])
def test_invalid_metadata_keeps_only_common_keys(path, value):
    data = model_document()
    target = data["decision_rationale"]["pv_clear_ghi"]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    result = summary(data)
    assert result["status"] == "invalid" and result["reason_code"] == "display_metadata_invalid"
    assert set(result) == COMMON


@pytest.mark.parametrize("dates", [["2026-10-11"], ["2026-10-10", "2026-10-10"], ["2026-10-10", "2026-10-09"], [True], []])
def test_training_dates_are_unique_sorted_and_before_target(dates):
    data = model_document()
    data["decision_rationale"]["pv_clear_ghi"]["calibration_pack"]["training_dates"] = dates
    assert summary(data)["reason_code"] == "display_metadata_invalid"


@pytest.mark.parametrize("first,second", [("training_as_of", "created_at"), ("created_at", "usable_from"),
                                          ("usable_from", "as_of"), ("retrieved_at", "as_of"), ("provider_issued_at", "retrieved_at")])
def test_every_time_order_rejects_reversal_and_accepts_equal_instants(first, second):
    data = model_document()
    trace = data["decision_rationale"]["pv_clear_ghi"]
    pack = trace["calibration_pack"]
    for key in ("training_as_of", "created_at", "usable_from"):
        pack[key] = "2026-10-11T02:00:00Z"
    for key in ("as_of", "retrieved_at", "provider_issued_at"):
        trace[key] = "2026-10-11T02:00:00Z"
    target = pack if first in pack else trace
    target[first] = "2026-10-11T11:00:00+09:00"
    assert summary(data)["status"] == "valid"
    target[first] = "2026-10-11T02:00:01Z"
    assert summary(data)["reason_code"] == "display_metadata_invalid"


@pytest.mark.parametrize("created", ["2026-10-11T02:49:00Z", "2026-10-11T02:51:00Z"])
def test_retrieval_and_calibration_creation_have_no_required_relative_order(created):
    data = model_document()
    pack = data["decision_rationale"]["pv_clear_ghi"]["calibration_pack"]
    pack.update(training_as_of="2026-10-11T02:48:00Z", created_at=created, usable_from="2026-10-11T02:52:00Z")
    assert summary(data)["status"] == "valid"


@pytest.mark.parametrize("key,value", [("target_date", "2026-10-12"), ("generated_at", "2026-10-11T04:00:00Z"),
                                       ("raw_plan_sha256", "c" * 10000), ("control_version_id", "d" * 10000)])
def test_identity_mismatch_or_oversize_never_fabricates_a_truncated_identity(key, value):
    data = model_document()
    identity = producer_identity(data)
    identity[key] = value
    result = summary(data, identity=identity)
    assert result["reason_code"] == "plan_identity_mismatch" and set(result) == COMMON
    if len(value) > 256:
        assert result[key] is None


def test_generated_at_uses_same_instant_not_coefficient_creation_clock():
    data = model_document()
    identity = producer_identity(data)
    identity["generated_at"] = "2026-10-11T12:00:00+09:00"
    result = summary(data, identity=identity)
    assert result["status"] == "valid" and result["generated_at"] == identity["generated_at"]


def test_valid_zero_and_signed_coefficients_are_preserved():
    data = model_document()
    trace = data["decision_rationale"]["pv_clear_ghi"]
    trace["calibration_pack"]["theta4"] = [0, 0.0, -1, -2.5]
    trace["totals"] = dict.fromkeys(trace["totals"], 0.0)
    result = summary(data)
    assert result["status"] == "valid" and result["theta4"] == [0, 0.0, -1, -2.5]
    assert result["totals"]["full_day_pv_kwh"] == 0.0


def test_serialized_budget_failure_has_only_bounded_common_shape(monkeypatch):
    original_dumps = json.dumps
    def oversized(value, **kwargs):
        result = original_dumps(value, **kwargs)
        return result + " " * 4096 if value.get("status") == "valid" else result
    monkeypatch.setattr(slice_assembler.json, "dumps", oversized)
    result = summary(model_document())
    assert result["reason_code"] == "display_summary_too_large" and set(result) == COMMON
    assert len(original_dumps(result).encode()) <= 4096


def test_summary_invocation_imports_no_model_numpy_observer_or_store(monkeypatch):
    original_import = builtins.__import__
    def guarded(name, *args, **kwargs):
        assert not name.startswith(("numpy", "app.forecasting", "app.energy_plan", "app.operations"))
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded)
    assert summary(model_document())["status"] == "valid"
    tree = ast.parse(inspect.getsource(_build_pv_model_summary))
    assert not any(isinstance(node, (ast.Import, ast.ImportFrom)) for node in ast.walk(tree))
