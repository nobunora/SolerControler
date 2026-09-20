from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.operations import firestore as firestore_ops
from app.operations import sqlite as sqlite_ops


CSV_HEADER = (
    "年月日,時刻,発電電力量[kWh],消費電力量[kWh],売電電力量[kWh],"
    "買電電力量[kWh],充電電力量[kWh],放電電力量[kWh],蓄電残量(SOC)[%]"
)


def _write_csv(path: Path, rows: list[str]) -> Path:
    path.write_text("\n".join([CSV_HEADER, *rows]), encoding="utf-8-sig")
    return path


class _Snapshot:
    def __init__(self, document_id: str, payload: dict[str, Any] | None, *, exists: bool = True) -> None:
        self.id = document_id
        self._payload = dict(payload or {})
        self.exists = exists

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)


class _DocumentRef:
    def __init__(self, client: "_FirestoreClient", collection: str, document_id: str) -> None:
        self.client = client
        self.collection = collection
        self.id = document_id

    def get(self) -> _Snapshot:
        payload = self.client.data.get(self.collection, {}).get(self.id)
        return _Snapshot(self.id, payload, exists=payload is not None)


class _Query:
    def __init__(self, client: "_FirestoreClient", collection: str, filters=None) -> None:
        self.client = client
        self.collection = collection
        self.filters = list(filters or [])

    def where(self, field: str, op: str, value: str):
        return _Query(self.client, self.collection, [*self.filters, (field, op, value)])

    def order_by(self, _field: str, **_kwargs):
        return self

    def limit(self, _count: int):
        return self

    def stream(self):
        self.client.query_calls.append((self.collection, tuple(self.filters)))
        rows = []
        for document_id, payload in self.client.data.get(self.collection, {}).items():
            include = True
            for field, op, value in self.filters:
                actual = str(payload.get(field, document_id))
                if op == ">=" and not actual >= value:
                    include = False
                elif op == "<" and not actual < value:
                    include = False
            if include:
                rows.append(_Snapshot(document_id, payload))
        return iter(sorted(rows, key=lambda item: item.id))


class _Collection:
    def __init__(self, client: "_FirestoreClient", name: str) -> None:
        self.client = client
        self.name = name

    def where(self, field: str, op: str, value: str):
        return _Query(self.client, self.name).where(field, op, value)

    def document(self, document_id: str) -> _DocumentRef:
        return _DocumentRef(self.client, self.name, document_id)

    def stream(self):
        if self.name == "monitoring_samples":
            raise AssertionError("unbounded monitoring_samples stream is forbidden")
        return _Query(self.client, self.name).stream()


class _Batch:
    def __init__(self, client: "_FirestoreClient") -> None:
        self.client = client

    def set(self, ref: _DocumentRef, payload: dict[str, Any], *, merge: bool) -> None:
        self.client.writes.append((ref.collection, ref.id, dict(payload), merge))
        current = dict(self.client.data.setdefault(ref.collection, {}).get(ref.id, {}))
        current.update(payload)
        self.client.data[ref.collection][ref.id] = current

    def commit(self) -> None:
        self.client.commits += 1


class _FirestoreClient:
    def __init__(self, data: dict[str, dict[str, dict[str, Any]]] | None = None) -> None:
        self.data = data or {}
        self.writes: list[tuple[str, str, dict[str, Any], bool]] = []
        self.query_calls: list[tuple[str, tuple[tuple[str, str, str], ...]]] = []
        self.commits = 0

    def collection(self, name: str) -> _Collection:
        return _Collection(self, name)

    def batch(self) -> _Batch:
        return _Batch(self)


def _monitoring_payload(ts: str, *, soc: float = 50.0, charge: float = 0.2) -> dict[str, Any]:
    return {
        "ts": ts,
        "pv_kwh": 1.0,
        "load_kwh": 2.0,
        "sell_kwh": 0.0,
        "buy_kwh": 1.0,
        "charge_kwh": charge,
        "discharge_kwh": 0.0,
        "soc_percent": soc,
    }


def test_firestore_sync_reads_only_four_day_window_and_writes_only_change(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path / "monitor.csv",
        [
            "2026/09/20,14:00,1,2,0,1,0.2,0,50",
            "2026/09/20,14:30,1,2,0,1,0.2,0,51",
        ],
    )
    existing_ts = "2026-09-20T14:00:00"
    client = _FirestoreClient(
        {
            "monitoring_samples": {
                existing_ts: {
                    **_monitoring_payload(existing_ts),
                    "source_csv": "old.csv",
                    "ingested_at": "old",
                }
            }
        }
    )

    changes = firestore_ops.sync_monitoring_csvs(
        client,
        csv_paths=[csv_path],
        ingested_at="2026-09-20T06:00:00Z",
    )

    assert changes.changed_count == 1
    assert changes.unchanged == 1
    monitoring_writes = [write for write in client.writes if write[0] == "monitoring_samples"]
    assert [write[1] for write in monitoring_writes] == ["2026-09-20T14:30:00"]
    assert client.query_calls == [
        (
            "monitoring_samples",
            (
                ("ts", ">=", "2026-09-17T00:00:00"),
                ("ts", "<", "2026-09-21T00:00:00"),
            ),
        )
    ]


def test_firestore_sync_same_payload_does_not_rewrite_metadata(tmp_path: Path) -> None:
    csv_path = _write_csv(
        tmp_path / "monitor.csv",
        ["2026/09/20,14:00,1,2,0,1,0.2,0,50"],
    )
    ts = "2026-09-20T14:00:00"
    client = _FirestoreClient(
        {
            "monitoring_samples": {
                ts: {
                    **_monitoring_payload(ts),
                    "source_csv": "old.csv",
                    "ingested_at": "old",
                }
            }
        }
    )

    changes = firestore_ops.sync_monitoring_csvs(
        client,
        csv_paths=[csv_path],
        ingested_at="2026-09-20T07:00:00Z",
    )

    assert changes.changed_count == 0
    assert client.writes == []
    assert client.data["monitoring_samples"][ts]["ingested_at"] == "old"


def test_firestore_bounded_daily_materialization_includes_previous_day_23_hour() -> None:
    rows = {
        "2026-07-15T23:00:00": _monitoring_payload("2026-07-15T23:00:00", charge=1.0),
        "2026-07-16T00:00:00": _monitoring_payload("2026-07-16T00:00:00", charge=2.0),
        "2026-07-16T07:00:00": _monitoring_payload("2026-07-16T07:00:00", soc=80.0, charge=0.0),
        "2026-07-16T12:30:00": _monitoring_payload("2026-07-16T12:30:00", soc=63.0, charge=0.2),
    }
    client = _FirestoreClient({"monitoring_samples": rows})

    result = firestore_ops.recalc_monitoring_daily_metrics(
        client,
        calendar_dates={"2026-07-16"},
        dashboard_affected_dates={"2026-07-16"},
        updated_at="2026-07-17T00:00:00Z",
    )

    dashboard = client.data["dashboard_daily_metrics"]["2026-07-16"]
    battery = client.data["battery_daily_metrics"]["2026-07-16"]
    assert result == {"dashboard": 1, "pv_charge_end": 1}
    assert dashboard["review_night_charge_kwh"] == pytest.approx(3.0)
    assert dashboard["morning_soc_percent"] == pytest.approx(80.0)
    assert battery["pv_charge_end_soc_percent"] == pytest.approx(63.0)
    assert client.query_calls[0] == (
        "monitoring_samples",
        (
            ("ts", ">=", "2026-07-15T23:00:00"),
            ("ts", "<", "2026-07-17T00:00:00"),
        ),
    )


def test_sqlite_sync_reuses_unique_timestamp_and_skips_unchanged_rewrite(tmp_path: Path) -> None:
    conn = sqlite_ops.open_db(tmp_path / "monitor.db")
    sqlite_ops.ensure_schema(conn)
    csv_path = _write_csv(
        tmp_path / "monitor.csv",
        [
            "2026/09/18,14:00,1,2,0,1,0.2,0,49",
            "2026/09/20,14:00,1,2,0,1,0.2,0,50",
        ],
    )
    conn.execute(
        """
        INSERT INTO monitoring_samples (
            ts, pv_kwh, load_kwh, sell_kwh, buy_kwh, charge_kwh, discharge_kwh,
            soc_percent, source_csv, ingested_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("2026-09-20T14:00:00", 1, 2, 0, 1, 0.2, 0, 50, "old.csv", "old"),
    )
    conn.commit()

    first = sqlite_ops.sync_monitoring_csvs(
        conn,
        csv_paths=[csv_path],
        ingested_at="2026-09-20T06:00:00Z",
    )
    second = sqlite_ops.sync_monitoring_csvs(
        conn,
        csv_paths=[csv_path],
        ingested_at="2026-09-20T07:00:00Z",
    )

    assert first.changed_count == 1
    assert first.calendar_dates == ("2026-09-18",)
    assert second.changed_count == 0
    unchanged = conn.execute(
        "SELECT ingested_at FROM monitoring_samples WHERE ts=?",
        ("2026-09-20T14:00:00",),
    ).fetchone()
    assert unchanged["ingested_at"] == "old"
    conn.close()


def test_sqlite_bounded_cost_recalculation_applies_prior_cumulative_baseline(tmp_path: Path) -> None:
    conn = sqlite_ops.open_db(tmp_path / "cost.db")
    sqlite_ops.ensure_schema(conn)
    conn.execute(
        """
        INSERT INTO cost_daily
        (date, self_consumption_kwh, savings_yen, cumulative_kwh, cumulative_yen, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        ("2026-08-31", 0.0, 0.0, 100.0, 2000.0, "old"),
    )
    for ts, load, buy in [
        ("2026-09-01T07:00:00", 2.0, 1.0),
        ("2026-09-02T07:00:00", 3.0, 1.0),
    ]:
        conn.execute(
            """
            INSERT INTO monitoring_samples
            (ts, load_kwh, buy_kwh, source_csv, ingested_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ts, load, buy, "x.csv", "now"),
        )
    conn.commit()

    count = sqlite_ops.recalc_cost_daily_from(
        conn,
        start_date="2026-09-01",
        end_ts="2026-09-03T00:00:00",
        day_rate_yen_per_kwh=10.0,
        updated_at="now",
    )

    assert count == 2
    rows = conn.execute(
        "SELECT date, cumulative_kwh, cumulative_yen FROM cost_daily WHERE date >= '2026-09-01' ORDER BY date"
    ).fetchall()
    assert [(row["date"], row["cumulative_kwh"], row["cumulative_yen"]) for row in rows] == [
        ("2026-09-01", 101.0, 2010.0),
        ("2026-09-02", 103.0, 2030.0),
    ]
    conn.close()


def test_firestore_daily_materialization_clears_stale_pv_charge_end_when_no_candidate_remains() -> None:
    day = "2026-07-16"
    client = _FirestoreClient(
        {
            "monitoring_samples": {
                f"{day}T12:30:00": _monitoring_payload(
                    f"{day}T12:30:00",
                    soc=63.0,
                    charge=0.0,
                )
            },
            "battery_daily_metrics": {
                day: {
                    "date": day,
                    "pv_charge_end_soc_percent": 63.0,
                    "pv_charge_end_at": f"{day}T12:30:00",
                }
            },
        }
    )

    result = firestore_ops.recalc_monitoring_daily_metrics(
        client,
        calendar_dates={day},
        dashboard_affected_dates={day},
        updated_at="2026-07-17T00:00:00Z",
    )

    battery = client.data["battery_daily_metrics"][day]
    assert result["pv_charge_end"] == 1
    assert battery["pv_charge_end_soc_percent"] is None
    assert battery["pv_charge_end_at"] is None


def test_sqlite_pv_charge_end_recalc_clears_stale_value_when_no_candidate_remains(
    tmp_path: Path,
) -> None:
    conn = sqlite_ops.open_db(tmp_path / "pv-clear.db")
    sqlite_ops.ensure_schema(conn)
    day = "2026-09-20"
    conn.execute(
        """
        INSERT INTO battery_daily_metrics
        (date, pv_charge_end_soc_percent, pv_charge_end_at, updated_at)
        VALUES (?, ?, ?, ?)
        """,
        (day, 63.0, f"{day}T12:30:00", "old"),
    )
    conn.execute(
        """
        INSERT INTO monitoring_samples
        (ts, pv_kwh, charge_kwh, soc_percent, source_csv, ingested_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (f"{day}T12:30:00", 1.0, 0.0, 63.0, "x.csv", "now"),
    )
    conn.commit()

    updated = sqlite_ops.recalc_battery_pv_charge_end_soc_for_dates(
        conn,
        dates={day},
        updated_at="new",
    )

    row = conn.execute(
        """
        SELECT pv_charge_end_soc_percent, pv_charge_end_at
        FROM battery_daily_metrics
        WHERE date=?
        """,
        (day,),
    ).fetchone()
    assert updated == 1
    assert row["pv_charge_end_soc_percent"] is None
    assert row["pv_charge_end_at"] is None
    conn.close()
