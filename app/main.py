from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from playwright.sync_api import Page, sync_playwright

from app.browser_automation import (
    apply_battery_setting,
    download_monitoring_csv,
    fetch_forecast_12h,
    login_monitoring_service,
)
from app.config import AppConfig
from app.csv_utils import parse_monitoring_csv
from app.decision import decide_battery_setting
from app.history_store import persist_history
from app.models import ApplyResult, DesiredBatterySetting, ForecastResult
from app.utils import load_dotenv_if_present


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def _run_dir(base: Path, now: datetime) -> Path:
    run_id = now.strftime("%Y%m%d-%H%M%S")
    path = base / run_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_run_summary(path: Path, payload: dict) -> Path:
    summary = path / "summary.json"
    summary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def _local_forecast(now: datetime, hours: float) -> ForecastResult:
    return ForecastResult(
        hours_12h=hours,
        captured_at=now,
        source_text=f"LOCAL_FORECAST_HOURS_OVERRIDE={hours}",
    )


def _copy_local_csv(cfg: AppConfig, run_dir: Path) -> Path:
    source = cfg.local_monitor_csv_path.expanduser()
    if not source.is_absolute():
        source = Path.cwd() / source
    if not source.exists():
        raise FileNotFoundError(f"ローカルCSVが見つかりません: {source}")
    destination = run_dir / source.name
    shutil.copy2(source, destination)
    return destination


def _apply_local_battery_setting(
    cfg: AppConfig,
    desired: DesiredBatterySetting,
) -> ApplyResult:
    current = cfg.local_current_charge_limit_text.strip() or None
    desired_text = str(desired.charge_limit_percent)
    if current == desired_text:
        return ApplyResult(changed=False, previous_charge_limit_text=current)
    if cfg.dry_run:
        return ApplyResult(changed=False, previous_charge_limit_text=current)
    return ApplyResult(changed=True, previous_charge_limit_text=current)


def main() -> int:
    load_dotenv_if_present()
    _setup_logging()
    logger = logging.getLogger(__name__)

    cfg = AppConfig.from_env()
    now = datetime.now(ZoneInfo(cfg.timezone))
    run_dir = _run_dir(cfg.artifacts_dir, now)
    logger.info("Run start: %s", now.isoformat())
    logger.info("Run artifacts directory: %s", run_dir)
    logger.info("Local dev mode: %s", cfg.local_dev_mode)

    page: Optional[Page] = None

    try:
        if cfg.local_dev_mode:
            if cfg.local_forecast_hours_override is not None:
                forecast = _local_forecast(now, cfg.local_forecast_hours_override)
                logger.info(
                    "Using LOCAL_FORECAST_HOURS_OVERRIDE: %.2f",
                    cfg.local_forecast_hours_override,
                )
            else:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=cfg.headless)
                    context = browser.new_context(
                        accept_downloads=True,
                        timezone_id=cfg.timezone,
                        locale="ja-JP",
                    )
                    context.set_default_timeout(cfg.timeout_ms)
                    context.set_default_navigation_timeout(cfg.timeout_ms)
                    page = context.new_page()
                    forecast = fetch_forecast_12h(page, cfg, now)

            csv_path = _copy_local_csv(cfg, run_dir)
            logger.info("Using local monitoring CSV: %s", csv_path)
            metrics = parse_monitoring_csv(csv_path, cfg)
            desired = decide_battery_setting(forecast, metrics, cfg)
            apply_result = _apply_local_battery_setting(cfg, desired)
        else:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=cfg.headless)
                context = browser.new_context(
                    accept_downloads=True,
                    timezone_id=cfg.timezone,
                    locale="ja-JP",
                )
                context.set_default_timeout(cfg.timeout_ms)
                context.set_default_navigation_timeout(cfg.timeout_ms)
                page = context.new_page()

                forecast = fetch_forecast_12h(page, cfg, now)
                login_monitoring_service(page, cfg)
                csv_path = download_monitoring_csv(page, cfg, run_dir)
                metrics = parse_monitoring_csv(csv_path, cfg)
                desired = decide_battery_setting(forecast, metrics, cfg)
                apply_result = apply_battery_setting(
                    page=page,
                    cfg=cfg,
                    desired=desired,
                    dry_run=cfg.dry_run,
                )

        payload = {
            "run_at": now.isoformat(),
            "dry_run": cfg.dry_run,
            "local_dev_mode": cfg.local_dev_mode,
            "forecast_hours_12h": forecast.hours_12h,
            "forecast_source_text": forecast.source_text,
            "metrics": {
                "row_count": metrics.row_count,
                "latest_soc": metrics.latest_soc,
                "avg_soc": metrics.avg_soc,
                "total_charge": metrics.total_charge,
                "total_discharge": metrics.total_discharge,
            },
            "decision": {
                "charge_limit_percent": desired.charge_limit_percent,
                "mode": desired.mode,
                "reason": desired.reason,
            },
            "apply_result": {
                "changed": apply_result.changed,
                "previous_charge_limit_text": apply_result.previous_charge_limit_text,
            },
        }
        summary_path = _save_run_summary(run_dir, payload)
        history_record = {
            "run_at": payload["run_at"],
            "dry_run": payload["dry_run"],
            "forecast_hours_12h": payload["forecast_hours_12h"],
            "latest_soc": payload["metrics"]["latest_soc"],
            "avg_soc": payload["metrics"]["avg_soc"],
            "total_charge": payload["metrics"]["total_charge"],
            "total_discharge": payload["metrics"]["total_discharge"],
            "charge_limit_percent": payload["decision"]["charge_limit_percent"],
            "mode": payload["decision"]["mode"],
            "reason": payload["decision"]["reason"],
            "changed": payload["apply_result"]["changed"],
            "previous_charge_limit_text": payload["apply_result"][
                "previous_charge_limit_text"
            ],
            "summary_path": str(summary_path),
            "csv_path": str(csv_path),
        }
        persist_history(cfg, history_record)
        logger.info("Run completed successfully")
        return 0
    except Exception:
        logger.exception("Run failed")
        if page is not None:
            screenshot = run_dir / "error.png"
            try:
                page.screenshot(path=str(screenshot), full_page=True)
                logger.info("Error screenshot saved: %s", screenshot)
            except Exception:
                logger.exception("Failed to capture error screenshot")
        return 1
