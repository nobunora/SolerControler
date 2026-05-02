from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from playwright.sync_api import Error, Page

from app.config import AppConfig
from app.models import ApplyResult, DesiredBatterySetting, ForecastResult

logger = logging.getLogger(__name__)


def _extract_float(text: str, regex: str) -> float:
    pattern = re.compile(regex)
    match = pattern.search(text)
    if not match:
        raise ValueError(f"予報値を抽出できません: {text!r}")
    return float(match.group(1))


def fetch_forecast_12h(page: Page, cfg: AppConfig, now: datetime) -> ForecastResult:
    logger.info("Forecast page open: %s", cfg.forecast_url)
    page.goto(cfg.forecast_url, wait_until="domcontentloaded")
    if cfg.forecast_ready_selector:
        page.locator(cfg.forecast_ready_selector).first.wait_for(state="visible")

    text = page.locator(cfg.forecast_hours_selector).first.inner_text().strip()
    raw_value = _extract_float(text, cfg.forecast_value_regex)
    hours = raw_value / cfg.forecast_value_divisor
    logger.info(
        "Forecast extracted: raw=%.4f, divisor=%.4f, hours=%.2f (text=%s)",
        raw_value,
        cfg.forecast_value_divisor,
        hours,
        text,
    )

    return ForecastResult(hours_12h=hours, captured_at=now, source_text=text)


def login_monitoring_service(page: Page, cfg: AppConfig) -> None:
    logger.info("Monitoring login page open: %s", cfg.monitor_login_url)
    page.goto(cfg.monitor_login_url, wait_until="domcontentloaded")

    user_locator = page.locator(cfg.login_username_selector).first
    pass_locator = page.locator(cfg.login_password_selector).first
    submit_locator = page.locator(cfg.login_submit_selector).first

    user_locator.fill(cfg.monitor_username)
    pass_locator.fill(cfg.monitor_password)
    submit_locator.click()

    if cfg.login_success_selector:
        page.locator(cfg.login_success_selector).first.wait_for(state="visible")
    logger.info("Monitoring login completed")


def download_monitoring_csv(page: Page, cfg: AppConfig, run_dir: Path) -> Path:
    logger.info("CSV page open: %s", cfg.monitor_csv_page_url)
    page.goto(cfg.monitor_csv_page_url, wait_until="domcontentloaded")
    if cfg.csv_pre_click_selector:
        page.locator(cfg.csv_pre_click_selector).first.click()

    with page.expect_download() as download_info:
        page.locator(cfg.csv_download_selector).first.click()
    download = download_info.value
    filename = download.suggested_filename or "monitor.csv"
    destination = run_dir / filename
    download.save_as(destination)
    logger.info("CSV downloaded: %s", destination)
    return destination


def _current_charge_limit(page: Page, cfg: AppConfig) -> Optional[str]:
    if not cfg.settings_current_charge_limit_selector:
        return None
    locator = page.locator(cfg.settings_current_charge_limit_selector).first
    if locator.count() == 0:
        return None
    try:
        value = locator.input_value(timeout=2_000)
        return value.strip() if value else None
    except Error:
        text = locator.inner_text(timeout=2_000)
        return text.strip() if text else None


def apply_battery_setting(
    page: Page,
    cfg: AppConfig,
    desired: DesiredBatterySetting,
    dry_run: bool,
) -> ApplyResult:
    logger.info("Settings page open: %s", cfg.monitor_settings_url)
    page.goto(cfg.monitor_settings_url, wait_until="domcontentloaded")

    current = _current_charge_limit(page, cfg)
    desired_text = str(desired.charge_limit_percent)
    if current and current == desired_text:
        logger.info("Charge limit already desired value (%s), skip", desired_text)
        return ApplyResult(changed=False, previous_charge_limit_text=current)

    logger.info(
        "Setting decision: limit=%s mode=%s reason=%s",
        desired.charge_limit_percent,
        desired.mode,
        desired.reason,
    )

    if dry_run:
        logger.info("DRY_RUN enabled. No setting update performed.")
        return ApplyResult(changed=False, previous_charge_limit_text=current)

    charge_locator = page.locator(cfg.settings_charge_limit_selector).first
    charge_locator.fill(desired_text)

    if cfg.settings_mode_selector:
        mode_locator = page.locator(cfg.settings_mode_selector).first
        try:
            mode_locator.select_option(value=desired.mode)
        except Error:
            mode_locator.select_option(label=desired.mode)

    page.locator(cfg.settings_submit_selector).first.click()
    if cfg.settings_success_selector:
        page.locator(cfg.settings_success_selector).first.wait_for(state="visible")

    logger.info("Battery setting update submitted")
    return ApplyResult(changed=True, previous_charge_limit_text=current)
