from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.forecasting import clear_ghi_calibration_io as io
from app.forecasting.pv_clear_ghi import BASIS_VERSION, MODEL_ID, calibration_from_bytes, canonical_pack_bytes

AS_OF = datetime(2026, 10, 14, tzinfo=timezone.utc)


def calibration(*, stamp=AS_OF, **updates):
    pack = {"schema_version": 1, "model_id": MODEL_ID, "basis_version": BASIS_VERSION,
            "site_fingerprint": "synthetic-設備", "terms": ["1", "x", "cos_phi", "sin_phi"],
            "theta4": [2.0, 0.1, 0.2, 0.1], "training_dates": ["2026-01-01", "2026-04-01", "2026-07-01"],
            "training_samples": 39, "rank": 4, "condition_number": 3.0,
            "training_as_of": stamp.isoformat(), "created_at": stamp.isoformat(), "usable_from": stamp.isoformat(),
            "actual_source_sha256": "a" * 64, "certification_source_sha256": "b" * 64,
            "certification_rule_version": "synthetic-certified-v1"}
    pack.update(updates)
    raw = canonical_pack_bytes(pack)
    return calibration_from_bytes(raw, sha256=hashlib.sha256(raw).hexdigest())


def load(path, **updates):
    args = {"expected_site": "synthetic-設備", "expected_basis": BASIS_VERSION,
            "target_date": date(2026, 10, 15), "as_of": AS_OF}
    args.update(updates)
    return io.load_clear_ghi_calibration(path, **args)


def test_canonical_pack_roundtrip_and_hash(tmp_path):
    original = calibration()
    path = io.write_clear_ghi_calibration(original, directory=tmp_path)
    assert load(path) == original
    reference_bytes = (tmp_path / "current.json").read_bytes()
    assert io.write_clear_ghi_calibration(original, directory=tmp_path) == path
    assert (tmp_path / "current.json").read_bytes() == reference_bytes
    assert len(list(tmp_path.glob("*.json"))) == 2
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("overrides", [{"expected_site": "other"}, {"expected_basis": "other"},
                                       {"as_of": AS_OF - timedelta(seconds=1)},
                                       {"target_date": date(2026, 4, 1)}])
def test_reject_invalid_calibration_pack(tmp_path, overrides):
    path = io.write_clear_ghi_calibration(calibration(), directory=tmp_path)
    with pytest.raises(ValueError):
        load(path, **overrides)


def test_hash_corruption_and_noncanonical_originals_fail(tmp_path):
    original = calibration()
    path = tmp_path / f"{original.sha256}.json"
    path.write_bytes(original.raw_bytes + b" ")
    with pytest.raises(ValueError, match="hash mismatch"):
        load(path)
    raw = json.dumps(json.loads(original.raw_bytes), indent=2, ensure_ascii=False).encode()
    noncanonical = tmp_path / f"{hashlib.sha256(raw).hexdigest()}.json"
    noncanonical.write_bytes(raw)
    with pytest.raises(ValueError, match="noncanonical"):
        load(noncanonical)


@pytest.mark.parametrize("updates", [{"schema_version": True}, {"rank": 3},
                                      {"theta4": [True, 0, 0, 0]}, {"terms": ["x", "1", "cos_phi", "sin_phi"]},
                                      {"actual_source_sha256": "not-a-hash"},
                                      {"training_dates": ["2026-04-01", "2026-01-01"]}])
def test_original_schema_rejects_corruption(updates):
    with pytest.raises(ValueError):
        calibration(**updates)


def test_bad_readback_keeps_previous_current(tmp_path, monkeypatch):
    old = io.write_clear_ghi_calibration(calibration(), directory=tmp_path)
    previous_reference = (tmp_path / "current.json").read_bytes()
    new = calibration(stamp=AS_OF + timedelta(hours=1))
    from pathlib import Path
    read_bytes = Path.read_bytes
    def corrupt_readback(path):
        raw = read_bytes(path)
        return raw + b" " if path.stem == new.sha256 else raw
    monkeypatch.setattr(Path, "read_bytes", corrupt_readback)
    with pytest.raises(ValueError, match="hash mismatch"):
        io.write_clear_ghi_calibration(new, directory=tmp_path)
    assert (tmp_path / "current.json").read_bytes() == previous_reference
    assert load(old).sha256 == old.stem


def test_failed_update_keeps_valid_reference(tmp_path, monkeypatch, caplog):
    first = io.write_clear_ghi_calibration(calibration(), directory=tmp_path)
    previous_reference = (tmp_path / "current.json").read_bytes()
    newer = calibration(stamp=AS_OF + timedelta(hours=1))
    def fail_replace(*args):
        raise OSError("simulated replacement interruption")
    monkeypatch.setattr(io.os, "replace", fail_replace)
    with pytest.raises(OSError):
        io.write_clear_ghi_calibration(newer, directory=tmp_path)
    assert (tmp_path / "current.json").read_bytes() == previous_reference
    assert load(first).sha256 == first.stem
    assert "update_failed" in caplog.text
    assert not list(tmp_path.glob("*.tmp"))


def test_cold_start_without_pack_fails(tmp_path):
    with pytest.raises(FileNotFoundError):
        load(tmp_path / ("a" * 64 + ".json"))
    with pytest.raises(ValueError, match="hash-named"):
        load(tmp_path / "current.json")


def test_interrupted_tmp_is_not_current(tmp_path, monkeypatch):
    io.write_clear_ghi_calibration(calibration(), directory=tmp_path)
    reference = (tmp_path / "current.json").read_bytes()
    def fail_link(*args):
        raise OSError("simulated original publication interruption")
    monkeypatch.setattr(io.os, "link", fail_link)
    with pytest.raises(OSError):
        io.write_clear_ghi_calibration(calibration(stamp=AS_OF + timedelta(hours=1)), directory=tmp_path)
    assert (tmp_path / "current.json").read_bytes() == reference
    assert not list(tmp_path.glob("*.tmp"))


def test_late_updater_and_correction_order(tmp_path):
    old = calibration()
    new = calibration(stamp=AS_OF + timedelta(hours=1), theta4=[2.1, 0.1, 0.2, 0.1])
    io.write_clear_ghi_calibration(new, directory=tmp_path)
    reference = (tmp_path / "current.json").read_bytes()
    with pytest.raises(ValueError, match="older/conflicting"):
        io.write_clear_ghi_calibration(old, directory=tmp_path)
    conflict = calibration(stamp=new.training_as_of, theta4=[2.2, 0.1, 0.2, 0.1])
    with pytest.raises(ValueError, match="older/conflicting"):
        io.write_clear_ghi_calibration(conflict, directory=tmp_path)
    assert (tmp_path / "current.json").read_bytes() == reference


def test_plan_restore_does_not_promote_current_pack(tmp_path):
    original = calibration()
    path = io.write_clear_ghi_calibration(original, directory=tmp_path)
    reference = (tmp_path / "current.json").read_bytes()
    restored = calibration_from_bytes(original.raw_bytes, sha256=original.sha256)
    assert restored.raw_bytes == path.read_bytes()
    assert (tmp_path / "current.json").read_bytes() == reference
