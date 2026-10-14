from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.dashboard.aggregation import _monitoring_history_start
from app.dashboard.firestore_repository import load_firestore_slice
from app.dashboard.repositories import DashboardLoadRequest
from app.dashboard.sqlite_repository import load_sqlite_query_snapshot
from app.operations_db import ensure_schema, open_db


@pytest.mark.parametrize(
    ("start", "close_day", "expected"),
    [
        ("2026-09-14", 14, "2026-08-15"),
        ("2026-09-15", 14, "2026-09-01"),
        ("2026-01-14", 14, "2025-12-15"),
        ("2024-02-29", 31, "2024-02-01"),
        ("2026-02-28", 31, "2026-02-01"),
        ("2026-10-01", 1, "2026-09-02"),
        ("2026-07-30", 14, "2026-07-15"),
    ],
)
def test_review_history_includes_billing_start_without_changing_rolling_window(
    monkeypatch: pytest.MonkeyPatch, start: str, close_day: int, expected: str,
) -> None:
    monkeypatch.setenv("DASHBOARD_AGGREGATION_CLOSE_DAY", str(close_day))
    day = date.fromisoformat(start)
    assert _monitoring_history_start(day, include_static=True) == expected
    assert _monitoring_history_start(day, include_static=False) == (day - timedelta(days=14)).isoformat()
    assert 14 <= (day - date.fromisoformat(expected)).days <= 31


class _EmptyQuery:
    def order_by(self, *_args: Any) -> "_EmptyQuery":
        return self

    def limit_to_last(self, _limit: int) -> "_EmptyQuery":
        return self

    def document(self, _name: str) -> object:
        return object()

    def stream(self) -> list[Any]:
        return []

    def get(self) -> list[Any]:
        return []


class _EmptyClient:
    def collection(self, _name: str) -> _EmptyQuery:
        return _EmptyQuery()

    def get_all(self, _refs: list[Any]) -> list[Any]:
        return []


@pytest.mark.parametrize("include_static", [True, False])
def test_firestore_review_fetches_whole_bill_once_and_preserves_graph_window(
    monkeypatch: pytest.MonkeyPatch, include_static: bool,
) -> None:
    monkeypatch.setenv("DASHBOARD_AGGREGATION_CLOSE_DAY", "14")
    monkeypatch.setattr("app.dashboard.firestore_repository._today_jst_iso", lambda: "2026-10-14")
    requested: list[tuple[str, str]] = []
    metrics = []
    for offset in range(60):
        day = (date(2026, 8, 15) + timedelta(days=offset)).isoformat()
        metrics.append({
            "date": day, "actual_pv_kwh": 1.0, "actual_load_kwh": 25.0,
            "day_buy_kwh": 10.0, "night_buy_kwh": 20.0,
            "sample_count": 48, "first_sample_at": f"{day}T00:00:00",
            "latest_sample_at": f"{day}T23:30:00",
        })

    def monitoring(_client: Any, *, start_date: str, end_date_iso: str) -> list[dict[str, Any]]:
        requested.append((start_date, end_date_iso))
        return [row for row in metrics if start_date <= str(row["date"]) <= end_date_iso]

    result = load_firestore_slice(
        end_date="2026-10-14", window_days=31, include_static=include_static,
        client_factory=_EmptyClient,
        bounds_reader=lambda _: ("2026-08-15", "2026-10-14"),
        rows_reader=lambda *args, **kwargs: [],
        hourly_reader=lambda *args, **kwargs: [],
        monitoring_reader=monitoring,
    )
    assert requested == [("2026-08-15" if include_static else "2026-08-31", "2026-10-14")]
    assert result.meta["oldest_loaded_date"] == "2026-09-14"
    assert len(result.data.energy_daily) == 30
    assert result.data.energy_daily[0]["date"] == "2026-09-14"
    assert result.data.energy_daily[-1]["date"] == "2026-10-13"
    if include_static:
        review = next(row for row in result.data.daily_reviews if row["review_date"] == "2026-09-14")
        assert review["cumulative_day_buy_kwh"] == pytest.approx(310.0)
        assert review["cumulative_night_buy_kwh"] == pytest.approx(620.0)
        assert review["tier3_arrival"] == {"date": "2026-09-06", "status": "reached"}
    else:
        assert result.data.daily_reviews == []


@pytest.mark.parametrize("include_static", [True, False])
def test_sqlite_monitoring_read_has_same_bounded_billing_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, include_static: bool,
) -> None:
    monkeypatch.setenv("DASHBOARD_AGGREGATION_CLOSE_DAY", "14")
    db_path = tmp_path / "solar.db"
    conn = open_db(db_path)
    try:
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO sunshine_daily(date, source, updated_at) VALUES ('2026-10-14', 'test', '2026-10-14')"
        )
        for day in ("2026-08-14", "2026-08-15", "2026-08-31", "2026-09-14"):
            conn.execute(
                "INSERT INTO monitoring_samples(ts,pv_kwh,load_kwh,ingested_at) VALUES (?,1,2,'2026-10-14')",
                (f"{day}T00:00:00",),
            )
        conn.commit()
    finally:
        conn.close()
    snapshot = load_sqlite_query_snapshot(
        db_path, DashboardLoadRequest(end_date="2026-10-14", window_days=31, include_static=include_static),
    )
    expected = ["2026-08-15", "2026-08-31", "2026-09-14"] if include_static else ["2026-08-31", "2026-09-14"]
    assert [row["date"] for row in snapshot.monitoring_daily] == expected
    assert snapshot.resolved_end_date == "2026-10-14"
