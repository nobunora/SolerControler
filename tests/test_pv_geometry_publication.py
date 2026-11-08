import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone

import pytest

from app.forecasting.clear_ghi_observations import site_fingerprint
from app.forecasting.pv_array import load_pv_array_configs
from app.forecasting.pv_ideal_max import BASIS_VERSION, MODEL_ID, IdealMaxArray, IdealMaxGeometry
from app.operations import pv_geometry_transition as transition
from app.operations import pv_ideal_max as ledger
from app.separated_runtime.plan import PublishedPlan
from app.separated_runtime.plan_store import _check_provenance

NOW = datetime(2026, 11, 8, 6, tzinfo=timezone.utc)


def _plan(geometry, arrays):
    pack = {"model_id": MODEL_ID, "basis_version": BASIS_VERSION,
            "geometry_fingerprint": geometry.fingerprint, "coefficient": 1.6,
            "revision": 11, "training_as_of": (NOW - timedelta(days=7)).isoformat()}
    trace = {"model_id": "physical_pv_ideal_max_global_v1", "basis_version": BASIS_VERSION,
             "site_fingerprint": site_fingerprint(latitude=geometry.latitude, longitude=geometry.longitude,
                 timezone_name=geometry.timezone_name, arrays=[asdict(a) for a in arrays]),
             "calibration_pack": pack, "retrieved_at": NOW.isoformat(), "provider_issued_at": None}
    _rehash(trace)
    return trace


def _rehash(trace):
    raw = json.dumps(trace["calibration_pack"], sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    trace["calibration_sha256"] = hashlib.sha256(raw).hexdigest()


def _published(trace):
    raw = json.dumps({"generated_at": NOW.isoformat(), "forecast": {"date": "2026-11-08"},
        "result": {"target_soc_7_percent": 50., "required_night_charge_kwh": 2., "effective_capacity_kwh": 8.8},
        "decision_rationale": {"pv_ideal_max": trace}}).encode()
    return PublishedPlan.from_bytes(raw, target_date="2026-11-08", charge_rate_info={"percent_per_hour": 20.})


@pytest.fixture
def correction(monkeypatch):
    arrays = load_pv_array_configs()
    target = IdealMaxGeometry(35., 139., "Asia/Tokyo", tuple(IdealMaxArray(a.azimuth_deg, a.tilt_deg,
              a.capacity_kw, a.performance_ratio, a.shading_factor) for a in arrays))
    source_arrays = [replace(a, capacity_kw=1., tilt_deg=20.) for a in arrays]
    source = replace(target, arrays=tuple(replace(a, capacity_kw=1., tilt_deg=20.) for a in target.arrays))
    monkeypatch.setattr(ledger, "configured_ideal_max_geometry", lambda: target)
    monkeypatch.setattr(transition, "load_pv_array_configs", lambda: arrays)
    return _plan(target, arrays), _plan(source, source_arrays)


def test_approved_panel_correction_passes_real_publication_guard(correction, capsys):
    new, old = correction
    assert transition.approved_panel_geometry_transition(new, old)
    _check_provenance(_published(new), _published(old))
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[0]["event"] == "plan_publication_geometry_transition"


@pytest.mark.parametrize("defect", ["site", "geometry", "pack_hash", "basis", "model", "reverse", "location"])
def test_panel_correction_does_not_authorize_other_changes(correction, defect):
    new, old = correction
    if defect == "reverse":
        new, old = old, new
    elif defect == "site":
        new["site_fingerprint"] = "a" * 64
    elif defect == "geometry":
        new["calibration_pack"]["geometry_fingerprint"] = "b" * 64
        _rehash(new)
    elif defect == "pack_hash":
        new["calibration_sha256"] = "c" * 64
    elif defect in {"basis", "model"}:
        new[defect + ("_version" if defect == "basis" else "_id")] = "unapproved"
    else:
        old["site_fingerprint"] = "different-physical-location"
    with pytest.raises(ValueError):
        _check_provenance(_published(new), _published(old))


@pytest.mark.parametrize("clock", ["retrieval", "training", "provider"])
def test_panel_correction_keeps_every_vintage_guard(correction, clock):
    new, old = correction
    if clock == "retrieval":
        new["retrieved_at"] = (NOW - timedelta(seconds=1)).isoformat()
    elif clock == "provider":
        old["provider_issued_at"] = NOW.isoformat()
        new["provider_issued_at"] = (NOW - timedelta(seconds=1)).isoformat()
    else:
        new["calibration_pack"]["training_as_of"] = (NOW - timedelta(days=8)).isoformat()
        _rehash(new)
    with pytest.raises(ValueError, match="regress"):
        _check_provenance(_published(new), _published(old))


def test_other_target_capacity_is_not_an_approved_transition(correction, monkeypatch):
    new, old = correction
    arrays = [replace(a, capacity_kw=1.09) for a in load_pv_array_configs()]
    monkeypatch.setattr(transition, "load_pv_array_configs", lambda: arrays)
    assert not transition.approved_panel_geometry_transition(new, old)
