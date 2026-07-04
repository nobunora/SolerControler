from __future__ import annotations

import json
from pathlib import Path

from db_pipeline_main import _settings_summary_successful


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
