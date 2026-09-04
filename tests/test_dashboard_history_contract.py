from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from app.dashboard.aggregation import _build_energy_daily
from app.dashboard.firestore_repository import _EXPECTED_HALF_HOUR_SLOTS, _snapshot_cutoff
from app.dashboard import history_reconstruction
from app.dashboard.history_reconstruction import (
    RECONSTRUCTED_FORECAST_SOURCE,
    firestore_forecast_hourly_with_reconstruction,
)
from app.dashboard.warnings import build_dashboard_warnings
from app.operations.historical_forecast_reconstruction import build_reconstructed_forecast_rows


# HISTORICAL_FAILURE_LOCK (2026-09-04): do not weaken without an explicit migration decision.
# These tests protect the product contract that historical PV/load forecast-vs-actual
# remains recoverable, provenance-safe, and isolated from battery-control ownership.


class _Doc:
    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row
        self.id = str(row.get("date") or row.get("ts") or "doc")

    def to_dict(self) -> dict[str, Any]:
        return dict(self._row)


class _Query:
    def __init__(self, client: "_Client", collection: str) -> None:
        self._client = client
        self._collection = collection

    def where(self, *args: object) -> "_Query":
        self._client.where_calls.append((self._collection, args))
        return self

    def order_by(self, *args: object) -> "_Query":
        del args
        return self

    def stream(self) -> list[_Doc]:
        self._client.stream_calls.append(self._collection)
        return [_Doc(row) for row in self._client.rows.get(self._collection, [])]


class _Client:
    def __init__(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        self.rows = rows
        self.where_calls: list[tuple[str, tuple[object, ...]]] = []
        self.stream_calls: list[str] = []

    def collection(self, name: str) -> _Query:
        return _Query(self, name)


def _hourly_rows(
    *,
    day: str,
    source: str,
    reconstruction_id: str | None = None,
    reconstructed_at: str = "2026-09-04T12:00:00Z",
    count: int = 24,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for hour in range(count):
        row: dict[str, Any] = {
            "date": day,
            "hour": hour,
            "forecast_pv_kwh": 1.0,
            "forecast_load_kwh": 2.0,
            "source": source,
        }
        if reconstruction_id is not None:
            row.update(
                {
                    "is_reconstructed": True,
                    "forecast_reconstruction_id": reconstruction_id,
                    "forecast_reconstructed_at": reconstructed_at,
                    "forecast_reconstruction_model_version": "test-model",
                    "forecast_reconstruction_basis": "historical_model_replay",
                }
            )
        rows.append(row)
    return rows


def _plan(day: str) -> dict[str, Any]:
    return {
        "forecast": {"date": day},
        "daytime_soc_optimization": {
            "hourly_pv_forecast_kwh": {str(hour): 0.25 for hour in range(24)},
            "hourly_load_forecast_kwh": {str(hour): 0.75 for hour in range(24)},
        },
    }


def test_reconstructed_persistence_rows_can_never_masquerade_as_original(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan("2026-09-02")), encoding="utf-8")

    rows, summary = build_reconstructed_forecast_rows(
        plan_path=plan_path,
        target_date="2026-09-02",
        reconstruction_model_version="master@abc123",
        reconstruction_basis="historical_model_replay",
        input_provenance="historical weather archive + historical load inputs",
        reconstructed_at="2026-09-04T12:00:00Z",
    )

    assert len(rows) == 24
    assert {int(row["hour"]) for row in rows} == set(range(24))
    assert {row["source"] for row in rows} == {RECONSTRUCTED_FORECAST_SOURCE}
    assert all(row["is_reconstructed"] is True for row in rows)
    assert all("forecast_run_id" not in row for row in rows)
    assert all("issued_at" not in row for row in rows)
    assert all("forecast_issued_at" not in row for row in rows)
    assert summary["source"] == RECONSTRUCTED_FORECAST_SOURCE
    assert summary["hourly_row_count"] == 24


def test_reconstruction_identity_is_idempotent_across_retry_timestamp(tmp_path: Path) -> None:
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(_plan("2026-09-02")), encoding="utf-8")

    _, first = build_reconstructed_forecast_rows(
        plan_path=plan_path,
        target_date="2026-09-02",
        reconstruction_model_version="master@abc123",
        reconstruction_basis="historical_archive",
        input_provenance="archive-set-1",
        reconstructed_at="2026-09-04T12:00:00Z",
    )
    _, retry = build_reconstructed_forecast_rows(
        plan_path=plan_path,
        target_date="2026-09-02",
        reconstruction_model_version="master@abc123",
        reconstruction_basis="historical_archive",
        input_provenance="archive-set-1",
        reconstructed_at="2026-09-04T13:00:00Z",
    )

    assert first["forecast_reconstruction_id"] == retry["forecast_reconstruction_id"]
    assert first["forecast_reconstructed_at"] != retry["forecast_reconstructed_at"]


def test_original_hourly_forecast_always_wins_over_reconstruction(monkeypatch: pytest.MonkeyPatch) -> None:
    day = "2026-09-02"
    original = _hourly_rows(day=day, source="forecast-only-hourly")
    reconstructed = _hourly_rows(
        day=day,
        source=RECONSTRUCTED_FORECAST_SOURCE,
        reconstruction_id="recon-a",
    )
    monkeypatch.setattr(
        history_reconstruction,
        "_firestore_forecast_hourly_between",
        lambda client, start_date, end_date_iso: list(original),
    )
    client = _Client({"forecast_hourly_reconstructed": reconstructed, "monitoring_samples": []})

    rows = firestore_forecast_hourly_with_reconstruction(
        client,
        start_date=day,
        end_date_iso=day,
    )

    assert len(rows) == 24
    assert {row["source"] for row in rows} == {"forecast-only-hourly"}
    assert "monitoring_samples" not in client.stream_calls


def test_complete_reconstruction_is_used_only_when_original_is_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    day = "2026-09-02"
    reconstructed = _hourly_rows(
        day=day,
        source=RECONSTRUCTED_FORECAST_SOURCE,
        reconstruction_id="recon-a",
    )
    monkeypatch.setattr(
        history_reconstruction,
        "_firestore_forecast_hourly_between",
        lambda client, start_date, end_date_iso: [],
    )
    client = _Client({"forecast_hourly_reconstructed": reconstructed, "monitoring_samples": []})

    rows = firestore_forecast_hourly_with_reconstruction(
        client,
        start_date=day,
        end_date_iso=day,
    )

    assert len(rows) == 24
    assert {row["source"] for row in rows} == {RECONSTRUCTED_FORECAST_SOURCE}
    assert {row["forecast_reconstruction_id"] for row in rows} == {"recon-a"}
    assert all(row["is_reconstructed"] is True for row in rows)


def test_partial_reconstruction_is_never_presented_as_daily_forecast(monkeypatch: pytest.MonkeyPatch) -> None:
    day = "2026-09-02"
    reconstructed = _hourly_rows(
        day=day,
        source=RECONSTRUCTED_FORECAST_SOURCE,
        reconstruction_id="recon-a",
        count=23,
    )
    monkeypatch.setattr(
        history_reconstruction,
        "_firestore_forecast_hourly_between",
        lambda client, start_date, end_date_iso: [],
    )
    client = _Client({"forecast_hourly_reconstructed": reconstructed, "monitoring_samples": []})

    rows = firestore_forecast_hourly_with_reconstruction(
        client,
        start_date=day,
        end_date_iso=day,
    )

    assert rows == []


def test_energy_daily_keeps_reconstructed_pv_and_load_as_one_pair() -> None:
    day = "2026-09-02"
    rows = _build_energy_daily(
        start_date=day,
        end_date_iso=day,
        pv_daily=[{"date": day, "forecast_pv_total_kwh": 99.0}],
        monitoring_daily=[{"date": day, "actual_pv_kwh": 4.0, "actual_load_kwh": 18.0}],
        forecast_hourly=_hourly_rows(
            day=day,
            source=RECONSTRUCTED_FORECAST_SOURCE,
            reconstruction_id="recon-a",
        ),
    )

    assert rows[0]["forecast_pv_kwh"] == pytest.approx(24.0)
    assert rows[0]["forecast_load_kwh"] == pytest.approx(48.0)
    assert rows[0]["forecast_pv_source"] == RECONSTRUCTED_FORECAST_SOURCE
    assert rows[0]["forecast_load_source"] == RECONSTRUCTED_FORECAST_SOURCE
    assert rows[0]["forecast_is_reconstructed"] is True
    assert rows[0]["forecast_provenance_kind"] == "reconstructed"
    assert rows[0]["forecast_reconstruction_id"] == "recon-a"


def test_dashboard_warning_makes_reconstructed_history_visible_to_user() -> None:
    warnings = build_dashboard_warnings(
        latest_schedule={},
        battery_daily=[],
        energy_daily=[
            {
                "date": "2026-09-03",
                "forecast_pv_source": RECONSTRUCTED_FORECAST_SOURCE,
                "forecast_load_source": RECONSTRUCTED_FORECAST_SOURCE,
                "forecast_is_reconstructed": True,
                "actual_pv_kwh": 3.0,
                "actual_load_kwh": 20.0,
            }
        ],
        forecast_hourly=[],
        end_date_iso="2026-09-03",
        today_jst_iso="2026-09-04",
    )

    codes = {warning["code"] for warning in warnings}
    assert "history_forecast_reconstructed" in codes
    assert "history_original_forecast_missing" in codes
    assert "history_actual_missing" not in codes


def test_daily_actual_reconstruction_contract_remains_48_unique_half_hours() -> None:
    assert len(_EXPECTED_HALF_HOUR_SLOTS) == 48
    assert _EXPECTED_HALF_HOUR_SLOTS == {
        f"{hour:02d}:{minute:02d}" for hour in range(24) for minute in (0, 30)
    }


def test_legacy_snapshot_hindsight_cutoff_remains_0700_jst() -> None:
    cutoff = _snapshot_cutoff("2026-09-02").astimezone(ZoneInfo("Asia/Tokyo"))
    assert cutoff.isoformat() == "2026-09-02T07:00:00+09:00"


def test_forecast_only_owner_remains_structurally_non_control() -> None:
    source = (Path(__file__).parents[1] / "app" / "runtime" / "forecast_job.py").read_text(
        encoding="utf-8"
    )
    forbidden = {
        "slot_orchestration",
        "run_settings_roundtrip",
        "apply_profile",
        "settings_events",
        "CLOUD_JOB_SLOT",
    }
    assert not {token for token in forbidden if token in source}
