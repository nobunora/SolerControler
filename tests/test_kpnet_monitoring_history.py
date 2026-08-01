from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.energy_plan.workflow import _latest_kpnet_csv_paths as energy_latest_csv_paths
from app.kpnet.monitoring_history import find_latest_kpnet_csv_paths, iter_charge_soc_points
from app.kpnet.workflow import _iter_charge_soc_points as kpnet_charge_soc_points
from app.runtime.cloud_job import _iter_charge_soc_points as cloud_charge_soc_points
from app.runtime.cloud_job import _latest_kpnet_csv_paths as cloud_latest_csv_paths


def _write_monitoring_csv(path: Path, rows: list[str]) -> None:
    path.write_text(
        "\n".join(
            ["年月日,時刻,蓄電残量(SOC)[%],充電電力量[kWh]", *rows],
        ),
        encoding="utf-8-sig",
    )


def test_charge_soc_points_skip_incomplete_or_invalid_rows_and_sort(tmp_path: Path) -> None:
    later = tmp_path / "later.csv"
    earlier = tmp_path / "earlier.csv"
    _write_monitoring_csv(
        later,
        [
            "2026/07/02,00:30,45,",
            ",01:00,46,1.2",
            "2026/07/02,,47,1.2",
            "2026/07/02,01:30,bad,1.2",
            "2026/07/02,02:00,48,bad",
        ],
    )
    _write_monitoring_csv(earlier, ["2026/07/01,23:30,40,1.1"])

    expected = [
        (datetime(2026, 7, 1, 23, 30), 40.0, 1.1),
        (datetime(2026, 7, 2, 0, 30), 45.0, 0.0),
    ]
    assert kpnet_charge_soc_points([later, earlier]) == expected
    assert cloud_charge_soc_points([later, earlier]) == expected
    assert iter_charge_soc_points([later, earlier]) == expected


def test_latest_csv_discovery_returns_name_sorted_csvs_from_latest_run(tmp_path: Path) -> None:
    old_csv = tmp_path / "20260701-old" / "csv"
    latest_csv = tmp_path / "20260702-new" / "csv"
    old_csv.mkdir(parents=True)
    latest_csv.mkdir(parents=True)
    (old_csv / "old.csv").write_text("", encoding="utf-8")
    second = latest_csv / "b.csv"
    first = latest_csv / "a.csv"
    second.write_text("", encoding="utf-8")
    first.write_text("", encoding="utf-8")

    assert cloud_latest_csv_paths(tmp_path) == [first, second]
    assert energy_latest_csv_paths(tmp_path) == [first, second]
    assert find_latest_kpnet_csv_paths(tmp_path) == [first, second]


def test_missing_csv_remains_runtime_specific(tmp_path: Path) -> None:
    assert cloud_latest_csv_paths(tmp_path) == []
    assert find_latest_kpnet_csv_paths(tmp_path) == []
    with pytest.raises(RuntimeError, match="artifacts配下にCSV"):
        energy_latest_csv_paths(tmp_path)
