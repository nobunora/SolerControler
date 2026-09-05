from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.dashboard import history_reconstruction
from app.dashboard.history_reconstruction import firestore_forecast_hourly_with_reconstruction
from app.dashboard.slice_assembler import (
    merge_display_plan_into_battery_daily,
    merge_forecast_metadata_into_schedule,
)


# HISTORICAL_FAILURE_LOCK (2026-09-04): predicted SOC must not silently disappear
# when control-plan persistence is intentionally absent. The dedicated forecast-only
# path owns display metadata only and must never write or require control-plan state.


def test_latest_forecast_plan_restores_battery_chart_without_control_write() -> None:
    rows = merge_display_plan_into_battery_daily(
        [{"date": "2026-09-05", "pv_charge_end_soc_percent": 44.0}],
        {
            "plan_date": "2026-09-05",
            "planned_target_soc_percent": 100.0,
            "planned_night_charge_kwh": 6.39,
        },
    )

    assert rows == [
        {
            "date": "2026-09-05",
            "pv_charge_end_soc_percent": 44.0,
            "setting_soc_target_percent": 100.0,
            "night_charge_kwh": 6.39,
            "plan_display_source": "forecast_plans",
        }
    ]


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


def _hourly(day: str, *, updated_at: str | None = None) -> list[dict[str, Any]]:
    rows = [
        {
            "date": day,
            "hour": hour,
            "forecast_pv_kwh": 0.2,
            "forecast_load_kwh": 0.3,
            "source": "forecast-only-hourly",
        }
        for hour in range(24)
    ]
    if updated_at is not None:
        for row in rows:
            row["updated_at"] = updated_at
    return rows


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


def test_firestore_history_read_joins_same_mutable_vintage_forecast_soc_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    day = "2026-09-05"
    updated_at = "2026-09-04T17:30:00Z"
    monkeypatch.setattr(
        history_reconstruction,
        "_firestore_forecast_hourly_between",
        lambda client, start_date, end_date_iso: _hourly(day, updated_at=updated_at),
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
                    "forecast_run_id": "run-a",
                    "updated_at": updated_at,
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


def test_later_mutable_forecast_metadata_does_not_attach_to_older_mutable_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    day = "2026-09-05"
    monkeypatch.setattr(
        history_reconstruction,
        "_firestore_forecast_hourly_between",
        lambda client, start_date, end_date_iso: _hourly(day, updated_at="2026-09-04T17:30:00Z"),
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
                    "forecast_run_id": "run-late",
                    "updated_at": "2026-09-04T22:30:00Z",
                    "planned_target_soc_percent": 77.0,
                }
            ]
        }
    )

    rows = firestore_forecast_hourly_with_reconstruction(client, start_date=day, end_date_iso=day)

    assert all("forecast_target_soc_percent" not in row for row in rows)


def test_snapshot_soc_metadata_requires_same_forecast_run_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    day = "2026-09-05"
    snapshot_rows = [
        {
            "date": day,
            "hour": hour,
            "forecast_pv_kwh": 0.2,
            "forecast_load_kwh": 0.3,
            "source": "forecast_hourly_snapshot",
            "forecast_run_id": "run-early",
        }
        for hour in range(24)
    ]
    monkeypatch.setattr(
        history_reconstruction,
        "_firestore_forecast_hourly_between",
        lambda client, start_date, end_date_iso: snapshot_rows,
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
                    "forecast_run_id": "run-late",
                    "updated_at": "2026-09-04T22:30:00Z",
                    "planned_target_soc_percent": 77.0,
                }
            ]
        }
    )

    rows = firestore_forecast_hourly_with_reconstruction(client, start_date=day, end_date_iso=day)

    assert all("forecast_target_soc_percent" not in row for row in rows)


def test_matching_snapshot_run_can_restore_soc_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    day = "2026-09-05"
    snapshot_rows = [
        {
            "date": day,
            "hour": hour,
            "forecast_pv_kwh": 0.2,
            "forecast_load_kwh": 0.3,
            "source": "forecast_hourly_snapshot",
            "forecast_run_id": "run-a",
        }
        for hour in range(24)
    ]
    monkeypatch.setattr(
        history_reconstruction,
        "_firestore_forecast_hourly_between",
        lambda client, start_date, end_date_iso: snapshot_rows,
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
                    "forecast_run_id": "run-a",
                    "planned_target_soc_percent": 68.5,
                }
            ]
        }
    )

    rows = firestore_forecast_hourly_with_reconstruction(client, start_date=day, end_date_iso=day)

    assert {row["forecast_target_soc_percent"] for row in rows} == {68.5}


def test_reconstructed_history_never_inherits_original_forecast_soc_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    day = "2026-08-30"
    reconstructed = [
        {
            "date": day,
            "hour": hour,
            "forecast_pv_kwh": 0.2,
            "forecast_load_kwh": 0.3,
            "source": "historical_reconstructed_estimate",
            "is_reconstructed": True,
            "forecast_reconstruction_id": "recon-a",
            "forecast_reconstructed_at": "2026-09-05T00:00:00Z",
            "forecast_reconstruction_model_version": "v1",
        }
        for hour in range(24)
    ]
    monkeypatch.setattr(
        history_reconstruction,
        "_firestore_forecast_hourly_between",
        lambda client, start_date, end_date_iso: [],
    )
    monkeypatch.setattr(
        history_reconstruction,
        "_selected_reconstructed_rows_between",
        lambda client, start_date, end_date_iso, original_dates: reconstructed,
    )
    monkeypatch.setattr(
        history_reconstruction,
        "_monitoring_rows_for_reconstructed_dates",
        lambda client, dates: [],
    )
    client = _Client(
        {
            "forecast_plans": [
                {
                    "date": day,
                    "forecast_run_id": "original-run",
                    "planned_target_soc_percent": 80.0,
                }
            ]
        }
    )

    rows = firestore_forecast_hourly_with_reconstruction(client, start_date=day, end_date_iso=day)

    assert len(rows) == 24
    assert all("forecast_target_soc_percent" not in row for row in rows)


def test_frontend_predicted_soc_path_uses_restored_schedule_target() -> None:
    source = (Path(__file__).parents[1] / "static" / "dashboard.js").read_text(encoding="utf-8")

    assert "plannedBatteryValues(batteryRow, sch).targetSocPercent" in source
    assert "HISTORICAL_FAILURE_LOCK (2026-09-05)" in source
    assert "forecastSocFromLatestActual(rows, capacityKwh, chargeEff, dischargeEff)" in source
    assert "if (!Number.isFinite(targetSocRaw)) return rows.map(() => null);" not in source
    assert 'label: "予想SOC(%)"' in source
    assert "const soc = estimateHourlyForecastSoc(rows, date);" in source
