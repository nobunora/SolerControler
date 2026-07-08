from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "kpnet_soc_gap_report.py"
    spec = importlib.util.spec_from_file_location("kpnet_soc_gap_report", path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_read_monitoring_rows_and_summarize(tmp_path: Path) -> None:
    mod = _load_module()
    csv_path = tmp_path / "kpnet.csv"
    csv_path.write_text(
        "\n".join(
            [
                "年月日,時刻,発電電力量[kWh],消費電力量[kWh],売電電力量[kWh],買電電力量[kWh],充電電力量[kWh],放電電力量[kWh],蓄電残量(SOC)[%]",
                "2026/07/08,07:00,0.5,0.7,0.0,0.2,0.1,0.0,3",
                "2026/07/08,07:30,0.6,0.4,0.1,0.0,0.2,0.0,5",
            ]
        ),
        encoding="utf-8-sig",
    )

    rows = mod.read_monitoring_rows([csv_path])
    summary = mod.summarize(rows)

    assert len(rows) == 2
    assert summary.rows == 2
    assert summary.first_hhmm == "07:00"
    assert summary.last_hhmm == "07:30"
    assert summary.pv_kwh == 1.1
    assert summary.load_kwh == 1.1
    assert summary.buy_kwh == 0.2
    assert summary.sell_kwh == 0.1
    assert summary.charge_kwh == 0.30000000000000004
    assert summary.soc_first == 3.0
    assert summary.soc_last == 5.0
    assert summary.soc_min == (3.0, "07:00")
    assert summary.soc_max == (5.0, "07:30")


def test_collect_csv_paths_prefers_summary_entries(tmp_path: Path) -> None:
    mod = _load_module()
    run_dir = tmp_path / "20260708-120000"
    csv_dir = run_dir / "csv"
    csv_dir.mkdir(parents=True)
    selected = csv_dir / "selected.csv"
    fallback = csv_dir / "fallback.csv"
    selected.write_text("年月日,時刻\n", encoding="utf-8")
    fallback.write_text("年月日,時刻\n", encoding="utf-8")
    (run_dir / "kpnet_summary.json").write_text(
        '{"csv_downloads":[{"path":"' + selected.as_posix() + '"}]}',
        encoding="utf-8",
    )

    assert mod._collect_csv_paths(run_dir) == [selected]


def test_hourly_forecast_reads_result_hourly_maps() -> None:
    mod = _load_module()
    plan_doc = {
        "_plan": {
            "result": {
                "hourly_pv_forecast_kwh": {"7": 0.48},
                "hourly_load_forecast_kwh": {"7": 1.187},
            }
        }
    }

    assert mod._hourly_forecast(plan_doc, 7, "pv") == 0.48
    assert mod._hourly_forecast(plan_doc, 7, "load") == 1.187


def test_hourly_forecast_reads_daytime_optimization_hourly_maps() -> None:
    mod = _load_module()
    plan_doc = {
        "_plan": {
            "daytime_soc_optimization": {
                "hourly_pv_forecast_kwh": {"7": 0.52},
                "hourly_load_forecast_kwh": {"7": 1.25},
            }
        }
    }

    assert mod._hourly_forecast(plan_doc, 7, "pv") == 0.52
    assert mod._hourly_forecast(plan_doc, 7, "load") == 1.25
