from __future__ import annotations

import gzip
import json
from pathlib import Path

from app.dashboard.models import DashboardData, DashboardSlice
from app.dashboard.snapshots import (
    BOOTSTRAP_KIND,
    HISTORY_KIND,
    artifact_from_payload,
    build_bootstrap_payload,
    clear_snapshot_cache,
    _full_history_rebuild_requested,
    load_precomputed_snapshot,
)


def _slice() -> DashboardSlice:
    return DashboardSlice(
        data=DashboardData(
            pv_daily=[{"date": "2026-09-19"}, {"date": "2026-09-20"}],
            cost_daily=[],
            cost_monthly=[],
            battery_daily=[],
            model_parameters=[],
            energy_daily=[],
            battery_flow_daily=[],
            forecast_hourly=[
                {"date": "2026-09-19", "hour": 23, "forecast_load_kwh": 0.2},
                {"date": "2026-09-20", "hour": 0, "forecast_load_kwh": 0.3},
                {"date": "2026-09-20", "hour": 1, "forecast_load_kwh": 0.4},
            ],
            latest_schedule={"plan_date": "2026-09-20"},
        ),
        meta={
            "window_days": 31,
            "oldest_loaded_date": "2026-08-21",
            "newest_loaded_date": "2026-09-20",
            "global_oldest_date": "2026-04-01",
            "global_newest_date": "2026-09-20",
            "has_more_before": True,
        },
    )


def test_bootstrap_snapshot_keeps_only_latest_plan_hourly_rows() -> None:
    payload = build_bootstrap_payload(_slice())

    assert [row["date"] for row in payload["forecast_hourly"]] == [
        "2026-09-20",
        "2026-09-20",
    ]
    assert payload["meta"]["snapshot_kind"] == BOOTSTRAP_KIND
    assert payload["meta"]["snapshot_schema_version"] == 1


def test_snapshot_artifact_is_deterministic_and_gzipped() -> None:
    payload = {
        "meta": {"snapshot_kind": BOOTSTRAP_KIND},
        "pv_daily": [{"date": f"2026-09-{(index % 20) + 1:02d}", "forecast_pv_kwh": 12.345} for index in range(100)],
    }

    first = artifact_from_payload(BOOTSTRAP_KIND, payload)
    second = artifact_from_payload(BOOTSTRAP_KIND, payload)

    assert first.gzip_bytes == second.gzip_bytes
    assert first.etag == second.etag
    assert first.etag.startswith('W/"')
    assert json.loads(gzip.decompress(first.gzip_bytes).decode("utf-8")) == payload
    assert len(first.gzip_bytes) < len(first.raw)


def test_local_precomputed_snapshot_roundtrip(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("DASHBOARD_SNAPSHOT_GCS_PREFIX", raising=False)
    monkeypatch.delenv("NIGHT_PLAN_ARCHIVE_GCS_PREFIX", raising=False)
    monkeypatch.setenv("DASHBOARD_SNAPSHOT_LOCAL_DIR", str(tmp_path))
    clear_snapshot_cache()

    payload = {"meta": {"snapshot_kind": HISTORY_KIND}, "energy_daily": [{"date": "2026-09-20"}]}
    artifact = artifact_from_payload(HISTORY_KIND, payload)
    (tmp_path / "history.json.gz").write_bytes(artifact.gzip_bytes)

    loaded = load_precomputed_snapshot(HISTORY_KIND)

    assert loaded is not None
    assert loaded.etag == artifact.etag
    assert loaded.raw == artifact.raw
    assert loaded.gzip_bytes == artifact.gzip_bytes


def test_full_history_rebuild_flag_is_explicit(monkeypatch) -> None:
    monkeypatch.delenv("DASHBOARD_SNAPSHOT_FULL_HISTORY_REBUILD", raising=False)
    assert _full_history_rebuild_requested() is False

    monkeypatch.setenv("DASHBOARD_SNAPSHOT_FULL_HISTORY_REBUILD", "true")
    assert _full_history_rebuild_requested() is True

    monkeypatch.setenv("DASHBOARD_SNAPSHOT_FULL_HISTORY_REBUILD", "0")
    assert _full_history_rebuild_requested() is False


def test_slot_23_is_the_only_automatic_full_history_rebuild_path() -> None:
    source = (Path(__file__).parents[1] / "app" / "operations" / "workflow.py").read_text(encoding="utf-8")

    assert 'full_history_rebuild=cfg.slot == "23"' in source

