import copy
import json
import os
import sys
from types import SimpleNamespace

import pytest

from scripts import validate_dashboard_backend_parity as validator
from scripts.validate_dashboard_backend_parity import compare_rows


def test_compare_rows_ignores_backend_return_order() -> None:
    sqlite_rows = [
        {"date": "2026-09-15", "value": 1.0},
        {"date": "2026-09-16", "value": 2.0},
    ]
    firestore_rows = list(reversed(sqlite_rows))

    assert compare_rows(sqlite_rows, firestore_rows) == []


def test_compare_rows_matches_hourly_identity() -> None:
    sqlite_rows = [
        {"date": "2026-09-16", "hour": 3, "value": 1.0},
        {"date": "2026-09-16", "hour": 4, "value": 2.0},
    ]
    firestore_rows = [
        {"date": "2026-09-16", "hour": "4", "value": 2.0},
        {"date": "2026-09-16", "hour": "3", "value": 1.0},
    ]

    assert compare_rows(sqlite_rows, firestore_rows) == []


def test_compare_rows_reports_missing_identity_instead_of_index_mismatch() -> None:
    errors = compare_rows(
        [{"date": "2026-09-15", "value": 1.0}],
        [{"date": "2026-09-16", "value": 1.0}],
    )

    assert "missing from firestore: date=2026-09-15" in errors
    assert "missing from sqlite: date=2026-09-16" in errors


def test_compare_rows_rejects_duplicate_identity() -> None:
    errors = compare_rows(
        [
            {"date": "2026-09-16", "value": 1.0},
            {"date": "2026-09-16", "value": 1.0},
        ],
        [{"date": "2026-09-16", "value": 1.0}],
    )

    assert errors == ["sqlite duplicate row identity: date=2026-09-16"]


def test_compare_rows_treats_flattened_null_and_omitted_field_as_equivalent() -> None:
    sqlite_rows = [
        {
            "date": "2026-09-14",
            "setting_soc_target_percent": 100.0,
            "night_charge_kwh": 9.5,
            "pv_charge_end_soc_percent": None,
        }
    ]
    firestore_rows = [
        {
            "date": "2026-09-14",
            "setting_soc_target_percent": 100.0,
            "night_charge_kwh": 9.5,
        }
    ]

    assert compare_rows(sqlite_rows, firestore_rows) == []


def test_compare_rows_does_not_hide_non_null_field_drift() -> None:
    errors = compare_rows(
        [{"date": "2026-09-14", "setting_soc_target_percent": None}],
        [{"date": "2026-09-14", "setting_soc_target_percent": 100.0}],
    )

    assert errors == [
        "date=2026-09-14 fields differ: sqlite_only=[], "
        "firestore_only=['setting_soc_target_percent']"
    ]


def _mock_main(monkeypatch, statuses, *, sql_value=1.0, fs_value=1.0,
               required_status=None, new_coverage=(0, 0)):
    calls = []
    rows = [{"date": "2026-10-11", "hour": 7, "forecast_pv_kwh": fs_value}]
    sql_rows = [{**rows[0], "forecast_pv_kwh": sql_value}]

    def load(_path, **kwargs):
        backend = os.environ["DATA_BACKEND"]
        calls.append((backend, kwargs))
        values = {name: [] for name in validator.DATASETS}
        values["forecast_hourly"] = sql_rows if backend == "sqlite" else rows
        return SimpleNamespace(data=SimpleNamespace(**values))

    def collect(**kwargs):
        calls.append(("collect", kwargs))
        assert kwargs["selected_hourly"] is rows
        return {}

    outcomes = iter(statuses)

    def validate(**_kwargs):
        status, drift = next(outcomes)
        return {"schema_version": 1, "status": status, "start_date": "2026-10-11", "end_date": "2026-10-11",
                "required_evidence_status": required_status or status,
                "days": {"2026-10-11": {"status": status, "failure_reasons": ["snapshot_drift"] if drift else []}},
                "coverage": {"new_required_days": new_coverage[0], "new_verified_days": new_coverage[1]},
                "read_count": 2, "read_bytes": 30, "failure_reasons": []}

    monkeypatch.setattr(validator, "load_dashboard_slice", load)
    monkeypatch.setattr(validator, "clear_dashboard_cache", lambda: None)
    monkeypatch.setattr(validator, "_evidence_clients", lambda: (object(), object()))
    monkeypatch.setattr(validator, "load_forecast_evidence_for_window", collect)
    monkeypatch.setattr(validator, "validate_forecast_window_evidence", validate)
    monkeypatch.setattr(sys, "argv", ["parity", "--end-date", "2026-10-11", "--window-days", "1", "--original-dir", "frozen"])
    return calls, rows


@pytest.mark.parametrize("status,exit_code", [("PASS", 0), ("FAIL", 1), ("INCONCLUSIVE", 2)])
def test_gate_requires_both_checks(monkeypatch, capsys, status, exit_code):
    calls, rows = _mock_main(monkeypatch, [(status, False)])
    before = copy.deepcopy(rows)
    assert validator.main() == exit_code
    output = capsys.readouterr().out
    result = json.loads(output.split("[forecast-evidence] ")[1])
    assert result["status"] == status
    assert result["common_parity_status"] == "PASS"
    assert set(result["common_parity_errors"]) == set(validator.DATASETS)
    assert len([call for call in calls if call[0] == "collect"]) == 1
    assert str(next(call[1]["original_dir"] for call in calls if call[0] == "collect")) == "frozen"
    assert rows == before


def test_common_failure_overrides_original_pass(monkeypatch, capsys):
    _mock_main(monkeypatch, [("PASS", False)], sql_value=2.0)
    assert validator.main() == 1
    result = json.loads(capsys.readouterr().out.split("[forecast-evidence] ")[1])
    assert result["common_parity_errors"]["forecast_hourly"] > 0


def test_cli_preserves_historical_inconclusive_but_passes_required_scope(monkeypatch, capsys):
    _mock_main(monkeypatch, [("INCONCLUSIVE", False)], required_status="PASS")
    assert validator.main() == 0
    result = json.loads(capsys.readouterr().out.split("[forecast-evidence] ")[1])
    assert result["status"] == "INCONCLUSIVE"
    assert result["required_evidence_status"] == result["common_parity_status"] == "PASS"
    assert result["coverage"]["new_verified_days"] == 0


def test_historical_scope_exclusion_never_hides_common_parity_failure(monkeypatch, capsys):
    _mock_main(monkeypatch, [("INCONCLUSIVE", False)], required_status="PASS", sql_value=2.0)
    assert validator.main() == 1
    result = json.loads(capsys.readouterr().out.split("[forecast-evidence] ")[1])
    assert result["common_parity_status"] == "FAIL"


@pytest.mark.parametrize("coverage,required,expected", [((0, 0), "PASS", 0),
                         ((1, 0), "INCONCLUSIVE", 2), ((1, 1), "PASS", 0)])
def test_cli_retains_new_coverage_without_claiming_post_adoption_acceptance(
        monkeypatch, capsys, coverage, required, expected):
    _mock_main(monkeypatch, [(required, False)], new_coverage=coverage)
    assert validator.main() == expected
    result = json.loads(capsys.readouterr().out.split("[forecast-evidence] ")[1])
    assert result["required_evidence_status"] == required
    assert result["coverage"]["new_required_days"] == coverage[0]
    assert result["coverage"]["new_verified_days"] == coverage[1]


def test_sql_numeric_bool_is_not_equal_to_float(monkeypatch, capsys):
    _mock_main(monkeypatch, [("PASS", False)], sql_value=True)
    assert validator.main() == 1
    assert "[forecast-evidence]" in capsys.readouterr().out


def test_drift_reloads_whole_pair_once_with_one_deadline(monkeypatch, capsys):
    calls, _ = _mock_main(monkeypatch, [("INCONCLUSIVE", True), ("PASS", False)])
    assert validator.main() == 0
    assert [call[0] for call in calls] == ["sqlite", "firestore", "collect"] * 2
    deadlines = [call[1]["deadline"] for call in calls if call[0] == "collect"]
    assert deadlines[0] == deadlines[1]
    result = json.loads(capsys.readouterr().out.split("[forecast-evidence] ")[1])
    assert result["read_count"] == 4
    assert result["read_bytes"] == 60


def test_second_drift_is_inconclusive(monkeypatch, capsys):
    calls, _ = _mock_main(monkeypatch, [("INCONCLUSIVE", True)] * 2)
    assert validator.main() == 2
    assert len([call for call in calls if call[0] == "collect"]) == 2
    assert capsys.readouterr().out.count("[forecast-evidence]") == 1


def test_invalid_window_fails_before_io_and_does_not_print_exception(monkeypatch, capsys):
    calls, _ = _mock_main(monkeypatch, [])
    monkeypatch.setattr(sys, "argv", ["parity", "--window-days", "0"])
    assert validator.main() == 1
    assert calls == []
    output = capsys.readouterr().out
    assert output.count("[forecast-evidence]") == 1
    result = json.loads(output.split("[forecast-evidence] ")[1])
    assert result["coverage"]["new_required_days"] == 0
    assert result["coverage"]["sql_soc_supported"] is False
    assert isinstance(result["start_date"], str)
