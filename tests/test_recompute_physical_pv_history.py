from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace


def _load_script_module() -> object:
    path = Path("scripts/recompute_physical_pv_history.py")
    spec = importlib.util.spec_from_file_location("recompute_physical_pv_history_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_records_recomputed_rows_with_fake_firestore(monkeypatch, tmp_path: Path) -> None:
    module = _load_script_module()

    class FakeSnapshot:
        id = "2026-07-06"

        @staticmethod
        def to_dict() -> dict[str, object]:
            return {"result": {"final_pv_forecast_source": "physical_pv_forecast"}}

    class FakeCollection:
        @staticmethod
        def stream() -> list[FakeSnapshot]:
            return [FakeSnapshot()]

    class FakeFirestore:
        @staticmethod
        def collection(_name: str) -> FakeCollection:
            return FakeCollection()

    monkeypatch.setattr(module, "open_firestore", lambda: FakeFirestore())
    monkeypatch.setattr(module, "StorageClient", lambda: object())
    monkeypatch.setattr(
        module,
        "load_night_plan_detail_from_firestore_doc",
        lambda *_args, **_kwargs: {"forecast": {"hourly_weather": [{"hour": 7, "weather_code": 1}]}, "daytime_soc_optimization": {}},
    )
    monkeypatch.setattr(module, "load_forecast_hourly_history_from_firestore", lambda **_kwargs: {})
    monkeypatch.setattr(module, "monitoring_rows", lambda *_args: [])
    monkeypatch.setattr(
        module,
        "build_physical_pv_candidate",
        lambda **_kwargs: SimpleNamespace(hourly_pv_kwh={7: 0.25}),
    )
    db_path = tmp_path / "history.db"
    monkeypatch.setattr(sys, "argv", ["recompute", "--start", "2026-07-06", "--end", "2026-07-06", "--db-path", str(db_path)])

    assert module.main() == 0
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT forecast_date, hour, recomputed_physical_pv_kwh FROM physical_pv_reforecast_hourly").fetchall()
    assert rows == [("2026-07-06", 7, 0.25)]
