from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from db_pipeline_main import _env_bool, _ingest_firestore, _settings_summary_successful


def _write_summary(tmp_path: Path, payload: dict[str, object]) -> Path:
    summary_path = tmp_path / "kpnet_summary.json"
    summary_path.write_text(json.dumps(payload), encoding="utf-8")
    return summary_path


def test_settings_summary_successful_accepts_applied_and_skipped(tmp_path: Path) -> None:
    summary_path = _write_summary(
        tmp_path,
        {
            "setting_results": [
                {"profile": "night-green", "status": "applied"},
                {"profile": "green-mode", "status": "skipped-no-change"},
            ]
        },
    )

    assert _settings_summary_successful(summary_path) is True


def test_settings_summary_successful_rejects_failed_and_dry_run(tmp_path: Path) -> None:
    for status in ("confirm-failed", "dry-run-confirmed", "unknown"):
        summary_path = _write_summary(
            tmp_path,
            {"setting_results": [{"profile": "night-green", "status": status}]},
        )

        assert _settings_summary_successful(summary_path) is False


def test_settings_summary_successful_rejects_summary_error(tmp_path: Path) -> None:
    summary_path = _write_summary(
        tmp_path,
        {
            "error": "KP-NET setting confirmation failed",
            "setting_results": [{"profile": "night-green", "status": "applied"}],
        },
    )

    assert _settings_summary_successful(summary_path) is False


def test_night_plan_ingestion_can_be_disabled_for_actual_only_import(
    monkeypatch,
) -> None:
    monkeypatch.setenv("DATA_PIPELINE_INCLUDE_NIGHT_PLAN", "false")

    assert _env_bool("DATA_PIPELINE_INCLUDE_NIGHT_PLAN", True) is False


def test_firestore_actual_only_import_does_not_write_night_plan(monkeypatch) -> None:
    from app import firestore_ops

    client = object()
    monkeypatch.setattr(firestore_ops, "open_firestore", lambda: client)
    monkeypatch.setattr(firestore_ops, "ensure_schema", lambda _client: None)
    monkeypatch.setattr(
        firestore_ops, "pipeline_run_exists", lambda _client, *, run_key: False
    )
    monkeypatch.setattr(
        firestore_ops,
        "ingest_sunshine_from_night_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("night plan must not be ingested")
        ),
    )
    monkeypatch.setattr(
        firestore_ops,
        "upsert_model_parameters_from_plan",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("plan parameters must not be ingested")
        ),
    )
    monkeypatch.setattr(
        firestore_ops, "recalc_battery_pv_charge_end_soc", lambda *args, **kwargs: 0
    )
    monkeypatch.setattr(
        firestore_ops, "recalc_dashboard_daily_metrics", lambda *args, **kwargs: 0
    )
    monkeypatch.setattr(
        firestore_ops, "recalc_model_hit_rates", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(firestore_ops, "recalc_cost_daily", lambda *args, **kwargs: None)
    monkeypatch.setattr(firestore_ops, "upsert_pipeline_run", lambda *args, **kwargs: None)
    cfg = SimpleNamespace(
        site_id="test-site",
        slot="manual-csv",
        artifacts_dir=Path("artifacts"),
        timezone="Asia/Tokyo",
        day_rate_yen_per_kwh=31.0,
        cost_tariff_mode="flat",
        night8_day_start_hhmm="07:00",
        night8_day_end_hhmm="23:00",
        night8_day_tier1_upper_kwh=90.0,
        night8_day_tier2_upper_kwh=230.0,
        night8_day_rate_tier1_yen=31.8,
        night8_day_rate_tier2_yen=39.1,
        night8_day_rate_tier3_yen=43.62,
        night8_night_rate_yen=28.85,
    )

    _ingest_firestore(
        cfg,
        csv_run_dir=None,
        settings_run_dir=None,
        now_iso="2026-07-17T13:40:00Z",
        include_night_plan=False,
    )
