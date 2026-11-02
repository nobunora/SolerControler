from datetime import datetime

import pytest

from app.energy_plan.monitoring_history import _monitoring_row, merge_monitoring_rows


def test_storage_reader_preserves_available_metadata_without_inventing_receipt():
    row = _monitoring_row({"ts": "2026-11-01T07:00:00", "pv_kwh": 0.1,
                           "available_at": "2026-11-01T08:00:00+09:00",
                           "temperature_c": 12.3, "temperature_source": "observed_outdoor"})
    assert row["available_at"] == "2026-11-01T08:00:00+09:00"
    assert row["temperature_c"] == 12.3
    assert row["temperature_source"] == "observed_outdoor"
    assert "ingested_at" not in row
    assert "updated_at" not in row
    assert "available_at" not in _monitoring_row({"ts": "2026-11-01T07:00:00"})


@pytest.mark.parametrize("changed_field", [None, "pv", "load", "charge", "discharge", "buy", "sell", "soc"])
def test_merge_preserves_receipt_only_for_identical_energy_and_soc(changed_field):
    fresh = {"dt": datetime(2026, 11, 1, 7), "pv": 0.0, "load": 0.2,
             "charge": 0.0, "discharge": 0.2, "buy": 0.0, "sell": 0.0, "soc": 50.0}
    stored = dict(fresh, available_at="2026-11-01T08:00:00+09:00",
                  ingested_at="2026-11-01T08:01:00+09:00", temperature_c=12.0,
                  temperature_source="measured_battery")
    if changed_field is not None:
        fresh[changed_field] += 0.1
    result = merge_monitoring_rows([stored], [fresh])[0]
    if changed_field is None:
        assert result["available_at"] == stored["available_at"]
        assert result["ingested_at"] == stored["ingested_at"]
        assert result["temperature_source"] == "measured_battery"
    else:
        assert all(key not in result for key in ("available_at", "ingested_at", "temperature_c", "temperature_source"))
    assert result["soc"] == fresh["soc"]


def test_merge_keeps_new_receipt_and_does_not_attach_old_source_to_new_temperature():
    fresh = {"dt": datetime(2026, 11, 1, 7), "soc": 50.0, "temperature_c": 20.0}
    stored = dict(fresh, available_at="2026-11-01T08:00:00+09:00",
                  temperature_c=10.0, temperature_source="measured_battery")
    result = merge_monitoring_rows([stored], [fresh])[0]
    assert result["available_at"] == stored["available_at"]
    assert result["temperature_c"] == 20.0
    assert "temperature_source" not in result
