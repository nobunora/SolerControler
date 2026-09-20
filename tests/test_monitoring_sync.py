from __future__ import annotations

from datetime import datetime, timezone

from app.operations.monitoring_sync import (
    classify_monitoring_rows,
    monitoring_sync_window,
    prepare_monitoring_rows,
    window_from_ingested_at,
)


def _row(ts: str, *, pv: float = 1.0, soc: float | None = 50.0, **extra):
    return {
        "ts": ts,
        "pv_kwh": pv,
        "load_kwh": 2.0,
        "sell_kwh": 0.0,
        "buy_kwh": 1.0,
        "charge_kwh": 0.2,
        "discharge_kwh": 0.0,
        "soc_percent": soc,
        **extra,
    }


def test_monitoring_sync_window_uses_four_jst_calendar_days() -> None:
    window = monitoring_sync_window(datetime(2026, 9, 20, 7, 0, tzinfo=timezone.utc))

    assert window.start_date.isoformat() == "2026-09-17"
    assert window.end_date.isoformat() == "2026-09-20"
    assert window.start_ts == "2026-09-17T00:00:00"
    assert window.end_ts == "2026-09-21T00:00:00"
    assert window.months == ("2026-09",)


def test_monitoring_sync_window_crosses_month_and_year_boundaries() -> None:
    october = monitoring_sync_window(datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc))
    january = monitoring_sync_window(datetime(2027, 1, 1, 15, 0, tzinfo=timezone.utc))

    assert october.months == ("2026-09", "2026-10")
    assert january.months == ("2026-12", "2027-01")


def test_window_from_ingested_at_converts_utc_to_jst() -> None:
    window = window_from_ingested_at("2026-09-20T15:30:00Z")

    assert window.end_date.isoformat() == "2026-09-21"
    assert window.start_date.isoformat() == "2026-09-18"


def test_prepare_monitoring_rows_filters_window_and_collapses_exact_duplicates() -> None:
    window = monitoring_sync_window(datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc))
    duplicate = _row("2026-09-20T14:30:00")
    prepared = prepare_monitoring_rows(
        [
            _row("2026-09-16T23:30:00"),
            duplicate,
            dict(duplicate),
            _row("2026-09-20T15:00:00"),
        ],
        window=window,
    )

    assert [row["ts"] for row in prepared.rows] == [
        "2026-09-20T15:00:00",
        "2026-09-20T14:30:00",
    ]
    assert prepared.rows_seen == 4
    assert prepared.rows_in_window == 3
    assert prepared.duplicate_same == 1
    assert prepared.duplicate_conflict == 0


def test_conflicting_duplicate_is_removed_without_blocking_other_rows() -> None:
    prepared = prepare_monitoring_rows(
        [
            _row("2026-09-20T14:30:00", pv=0.5),
            _row("2026-09-20T14:30:00", pv=0.6),
            _row("2026-09-20T15:00:00", pv=0.7),
        ],
        window=None,
    )

    assert [row["ts"] for row in prepared.rows] == ["2026-09-20T15:00:00"]
    assert prepared.duplicate_conflict == 1
    assert prepared.conflict_timestamps == ("2026-09-20T14:30:00",)


def test_classification_ignores_ingest_metadata_and_only_writes_real_changes() -> None:
    same = _row("2026-09-20T14:00:00", _source_csv="new.csv")
    changed = _row("2026-09-20T14:30:00", soc=65.0, _source_csv="new.csv")
    inserted = _row("2026-09-20T15:00:00", _source_csv="new.csv")
    prepared = prepare_monitoring_rows([same, changed, inserted], window=None)
    existing = {
        "2026-09-20T14:00:00": {
            **_row("2026-09-20T14:00:00"),
            "source_csv": "old.csv",
            "ingested_at": "old",
        },
        "2026-09-20T14:30:00": {
            **_row("2026-09-20T14:30:00", soc=60.0),
            "source_csv": "old.csv",
            "ingested_at": "old",
        },
    }

    changes = classify_monitoring_rows(prepared, existing_by_ts=existing)

    assert len(changes.inserts) == 1
    assert len(changes.updates) == 1
    assert changes.unchanged == 1
    assert changes.changed_count == 2
    assert changes.calendar_dates == ("2026-09-20",)


def test_23_hour_change_affects_next_dashboard_day() -> None:
    prepared = prepare_monitoring_rows(
        [
            _row("2026-09-20T22:30:00"),
            _row("2026-09-20T23:00:00"),
        ],
        window=None,
    )
    changes = classify_monitoring_rows(prepared, existing_by_ts={})

    assert changes.calendar_dates == ("2026-09-20",)
    assert changes.dashboard_affected_dates == ("2026-09-20", "2026-09-21")
