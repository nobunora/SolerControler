import copy
import hashlib
import json
from datetime import date, timedelta

import pytest

from app.dashboard.slice_assembler import _build_pv_model_summary


def document():
    pack = {"model_id": "pv_ideal_max_global_v1", "basis_version": "noaa_haurwitz_erbs_poa_stc25_1min_v1",
            "geometry_fingerprint": "a" * 64, "coefficient": 1.575789910708392, "revision": 11,
            "training_as_of": "2026-10-31T17:31:58+00:00", "snapshot_sha256": "b" * 64}
    sha = hashlib.sha256(json.dumps(pack, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    model = "physical_pv_ideal_max_global_v1"
    return {"generated_at": "2026-11-08T07:23:00Z", "forecast": {"date": "2026-11-08"},
        "result": {"final_pv_forecast_source": model}, "decision_rationale": {
            "pv_physical_forecast": {"selected_method": model}, "pv_ideal_max": {
                "schema_version": 1, "model_id": model, "basis_version": pack["basis_version"],
                "target_date": "2026-11-08", "calibration_pack": pack, "calibration_sha256": sha,
                "coefficient": pack["coefficient"], "revision": 11, "site_fingerprint": "c" * 64,
                "forecast_sha256": "d" * 64, "weather_adjustment_applied": True,
                "retrieved_at": "2026-11-08T07:20:00Z", "as_of": "2026-11-08T07:22:00Z", "provider_issued_at": None,
                "hourly_pv_bias": {"status": "neutral", "enabled": False,
                    "minimum_bin_days": 7, "prior_pseudo_days": 20, "lookback_days": 365,
                    "training_days": [(date(2026, 10, 4) + timedelta(days=n)).isoformat() for n in range(35)],
                    "validation_days": [(date(2026, 10, 11) + timedelta(days=n)).isoformat() for n in range(28)],
                    "economic_validation_days": 4, "coverage": {"accepted_hours": 840},
                    "pv_validation": {"baseline_mae": .140070336, "candidate_mae": .119749956},
                    "validation_errors": {"soc_mae": {"baseline": 1.440494668, "candidate": 1.451442795},
                        "buy_mae": {"baseline": .041576, "candidate": .041576},
                        "fee_mae": {"baseline": 1.60968, "candidate": 1.60968}},
                    "source_sha256": "PRIVATE-DO-NOT-TRANSFER"}}}}


def summary(data):
    return _build_pv_model_summary(data, producer_identity={"target_date": "2026-11-08",
        "generated_at": data["generated_at"], "raw_plan_sha256": "e" * 64, "control_version_id": "f" * 64},
        forecast_run_id="run-1")


def test_native_summary_is_bound_bounded_and_does_not_change_adoption_or_original():
    data = document()
    before = copy.deepcopy(data)
    value = summary(data)
    assert value["status"] == "valid" and value["coefficient"] == 1.575789910708392
    bias = value["pv_bias"]
    assert bias["status"] == "neutral" and bias["regressed_metrics"] == ["soc_mae"]
    assert (bias["training_day_count"], bias["training_hour_count"], bias["validation_day_count"]) == (35, 840, 28)
    assert (bias["minimum_bin_days"], bias["lookback_days"], bias["prior_pseudo_days"]) == (7, 365, 20)
    raw = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    assert len(raw.encode()) <= 4096 and "PRIVATE" not in raw and "training_days" not in raw
    assert data == before


@pytest.mark.parametrize("field,value", [("coefficient", True), ("coefficient", float("nan")),
    ("revision", True), ("weather_adjustment_applied", False), ("calibration_sha256", "a" * 64),
    ("retrieved_at", "2026-11-08T07:24:00Z"), ("basis_version", "unapproved")])
def test_native_invalid_identity_or_pack_never_shows_confirmed_model(field, value):
    data = document()
    data["decision_rationale"]["pv_ideal_max"][field] = value
    assert summary(data)["status"] == "invalid"


def test_missing_bias_metadata_is_not_reported_as_zero_training():
    data = document()
    data["decision_rationale"]["pv_ideal_max"].pop("hourly_pv_bias")
    value = summary(data)
    assert value["status"] == "valid" and value["pv_bias"]["status"] == "unavailable"
    assert value["pv_bias"]["training_day_count"] is None
    assert value["pv_bias"]["training_hour_count"] is None


@pytest.mark.parametrize("field,value", [("coefficient", True), ("revision", False)])
def test_pack_booleans_cannot_pass_numeric_equality(field, value):
    data = document()
    trace = data["decision_rationale"]["pv_ideal_max"]
    trace[field] = 1. if field == "coefficient" else 0
    trace["calibration_pack"][field] = value
    trace["calibration_sha256"] = hashlib.sha256(json.dumps(trace["calibration_pack"], sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()
    assert summary(data)["status"] == "invalid"


def test_display_keeps_zero_metrics_and_epsilon_ties_and_rejects_future_training_dates():
    data = document()
    bias = data["decision_rationale"]["pv_ideal_max"]["hourly_pv_bias"]
    bias["training_days"] = ["2026-11-09"]
    bias["validation_errors"] = {"soc_mae": {"baseline": 0., "candidate": 1e-10},
        "buy_mae": {"baseline": float("nan"), "candidate": 0.},
        "fee_mae": {"baseline": 0., "candidate": 0.}}
    shown = summary(data)["pv_bias"]
    assert shown["training_day_count"] is None and shown["regressed_metrics"] == []
    assert shown["validation_errors"]["fee_mae"] == {"baseline": 0., "candidate": 0.}
    assert shown["validation_errors"]["buy_mae"]["baseline"] is None
