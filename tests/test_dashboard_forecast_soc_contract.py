from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.dashboard import history_reconstruction
from app.dashboard.history_reconstruction import firestore_forecast_hourly_with_reconstruction
from app.dashboard.slice_assembler import merge_forecast_metadata_into_schedule


# HISTORICAL_FAILURE_LOCK (2026-09-04): predicted SOC must not silently disappear
# when control-plan persistence is intentionally absent. The dedicated forecast-only
# path owns display metadata only and must never write or require control-plan state.


class _Doc:
    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row
        self.id = str(row.get("date") or "doc")

    def to_dict(self) -> dict[str, Any]:
        return dict(self._row)


class _Query:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def where(self, *args: object) -> "_Query":
        del args
        return self

    def stream(self) -> list[_Doc]:
        return [_Doc(row) for row in self._rows]


class _Collection:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def where(self, *args: object) -> _Query:
        del args
        return _Query(self._rows)


class _Client:
    def __init__(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        self.rows = rows

    def collection(self, name: str) -> _Collection:
        return _Collection(self.rows.get(name, []))


def _hourly(day: str) -> list[dict[str, Any]]:
    return [
        {
            "date": day,
            "hour": hour,
            "forecast_pv_kwh": 0.2,
            "forecast_load_kwh": 0.3,
            "source": "forecast-only-hourly",
        }
        for hour in range(24)
    ]


def test_forecast_plan_metadata_restores_predicted_soc_anchor_without_control_plan() -> None:
    day = "2026-09-05"
    schedule = {"plan_date": day, "planned_target_soc_percent": None}
    hourly = [
        {
            "date": day,
            "hour": hour,
            "forecast_target_soc_percent": 68.5,
            "forecast_night_charge_kwh": 2.75,
        }
        for hour in range(24)
    ]

    merged = merge_forecast_metadata_into_schedule(schedule, hourly, plan_date=day)

    assert merged["planned_target_soc_percent"] == pytest.approx(68.5)
    assert merged["planned_night_charge_kwh"] == pytest.approx(2.75)
    assert merged["planned_target_soc_source"] == "forecast_plans"


def test_forecast_metadata_never_overrides_existing_control_plan_target() -> None:
    day = "2026-09-05"
    schedule = {
        "plan_date": day,
        "planned_target_soc_percent": 72.0,
        "planned_night_charge_kwh": 4.0,
    }
    hourly = [
        {
            "date": day,
            "hour": hour,
            "forecast_target_soc_percent": 68.5,
            "forecast_night_charge_kwh": 2.75,
        }
        for hour in range(24)
    ]

    merged = merge_forecast_metadata_into_schedule(schedule, hourly, plan_date=day)

    assert merged["planned_target_soc_percent"] == pytest.approx(72.0)
    assert merged["planned_night_charge_kwh"] == pytest.approx(4.0)
    assert "planned_target_soc_source" not in merged


def test_inconsistent_forecast_soc_metadata_fails_closed() -> None:
    day = "2026-09-05"
    hourly = [
        {
            "date": day,
            "hour": hour,
            "forecast_target_soc_percent": 68.5 if hour < 23 else 69.0,
        }
        for hour in range(24)
    ]

    merged = merge_forecast_metadata_into_schedule({"plan_date": day}, hourly, plan_date=day)

    assert "planned_target_soc_percent" not in merged


def test_firestore_history_read_joins_forecast_only_soc_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    day = "2026-09-05"
    monkeypatch.setattr(
        history_reconstruction,
        "_firestore_forecast_hourly_between",
        lambda client, start_date, end_date_iso: _hourly(day),
    )
    monkeypatch.setattr(
        history_reconstruction,
        "_selected_reconstructed_rows_between",
        lambda client, start_date, end_date_iso, original_dates: [],
    )
    client = _Client(
        {
            "forecast_plans": [
                {
                    "date": day,
                    "planned_target_soc_percent": 68.5,
                    "planned_night_charge_kwh": 2.75,
                }
            ]
        }
    )

    rows = firestore_forecast_hourly_with_reconstruction(
        client,
        start_date=day,
        end_date_iso=day,
    )

    assert len(rows) == 24
    assert {row["forecast_target_soc_percent"] for row in rows} == {68.5}
    assert {row["forecast_night_charge_kwh"] for row in rows} == {2.75}


def test_frontend_predicted_soc_path_uses_restored_schedule_target() -> None:
    source = (Path(__file__).parents[1] / "static" / "dashboard.js").read_text(encoding="utf-8")

    assert "plannedBatteryValues(batteryRow, sch).targetSocPercent" in source
    assert "if (!Number.isFinite(targetSocRaw)) return rows.map(() => null);" in source
    assert 'label: "予想SOC(%)"' in source
    assert "const soc = estimateHourlyForecastSoc(rows, date);" in source
