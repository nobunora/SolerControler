from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from app.artifact_cleanup import collect_cleanup_candidates


def _touch(path: Path, *, age_days: int, now: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    ts = (now - timedelta(days=age_days)).timestamp()
    os.utime(path, (ts, ts))


def test_cleanup_keeps_replay_inputs_but_flags_generated_outputs(tmp_path: Path) -> None:
    now = datetime(2026, 7, 11, 12, 0, 0)
    artifacts = tmp_path / "artifacts"
    replay = artifacts / "replay" / "20260621-000000-000-20260701-010101"

    _touch(replay / "replay.db", age_days=40, now=now)
    _touch(replay / "night_charge_plan.json", age_days=40, now=now)
    _touch(replay / "20260701-010101" / "csv" / "infoMeasureMulti30Min_EU_00HX25X02077_202607.csv", age_days=40, now=now)
    _touch(replay / "20260701-010101" / "kpnet_summary.json", age_days=40, now=now)

    candidates = collect_cleanup_candidates(artifacts, now=now)
    rels = {item.path.relative_to(artifacts).as_posix(): item.reason for item in candidates}

    assert rels["replay/20260621-000000-000-20260701-010101/replay.db"] == "regenerable_replay_output"
    assert rels["replay/20260621-000000-000-20260701-010101/night_charge_plan.json"] == "regenerable_replay_output"
    assert "replay/20260621-000000-000-20260701-010101/20260701-010101/csv/infoMeasureMulti30Min_EU_00HX25X02077_202607.csv" not in rels
    assert "replay/20260621-000000-000-20260701-010101/20260701-010101/kpnet_summary.json" not in rels


def test_cleanup_flags_old_png_and_cloud_pull(tmp_path: Path) -> None:
    now = datetime(2026, 7, 11, 12, 0, 0)
    artifacts = tmp_path / "artifacts"
    _touch(artifacts / "dashboard_old.png", age_days=20, now=now)
    _touch(artifacts / "cloud_pull.db", age_days=20, now=now)
    _touch(artifacts / "night_charge_plan.json", age_days=20, now=now)

    candidates = collect_cleanup_candidates(artifacts, now=now)
    rels = {item.path.relative_to(artifacts).as_posix(): item.reason for item in candidates}

    assert rels["dashboard_old.png"] == "regenerable_png"
    assert rels["cloud_pull.db"] == "temporary_cloud_pull"
    assert "night_charge_plan.json" not in rels
