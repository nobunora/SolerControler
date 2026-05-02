from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _env(name: str, default: Optional[str] = None) -> str:
    value = os.getenv(name)
    if value is None:
        if default is None:
            raise ValueError(f"Missing required environment variable: {name}")
        return default
    return value


def _env_optional(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    return float(raw)


def _env_optional_float(name: str) -> Optional[float]:
    raw = os.getenv(name)
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    return float(raw)


@dataclass(frozen=True)
class AppConfig:
    timezone: str
    dry_run: bool
    headless: bool
    timeout_ms: int
    artifacts_dir: Path
    history_csv_path: Path
    history_sqlite_enabled: bool
    history_sqlite_path: Path
    local_dev_mode: bool
    local_monitor_csv_path: Path
    local_forecast_hours_override: Optional[float]
    local_current_charge_limit_text: str

    forecast_url: str
    forecast_ready_selector: str
    forecast_hours_selector: str
    forecast_value_regex: str
    forecast_value_divisor: float

    monitor_login_url: str
    monitor_csv_page_url: str
    monitor_settings_url: str
    monitor_username: str
    monitor_password: str

    login_username_selector: str
    login_password_selector: str
    login_submit_selector: str
    login_success_selector: str

    csv_download_selector: str
    csv_pre_click_selector: str

    settings_charge_limit_selector: str
    settings_mode_selector: str
    settings_submit_selector: str
    settings_success_selector: str
    settings_current_charge_limit_selector: str

    csv_timestamp_column: str
    csv_soc_column: str
    csv_charge_power_column: str
    csv_discharge_power_column: str

    forecast_high_hours: float
    forecast_low_hours: float
    low_soc_threshold: float
    high_soc_threshold: float
    charge_limit_high: int
    charge_limit_mid: int
    charge_limit_low: int

    mode_high_sun: str
    mode_mid_sun: str
    mode_low_sun: str

    @staticmethod
    def from_env() -> "AppConfig":
        local_dev_mode = _env_bool("LOCAL_DEV_MODE", False)

        if local_dev_mode:
            monitor_login_url = _env_optional(
                "MONITOR_LOGIN_URL", "https://example.com/login"
            )
            monitor_csv_page_url = _env_optional(
                "MONITOR_CSV_PAGE_URL", "https://example.com/operation/csv"
            )
            monitor_settings_url = _env_optional(
                "MONITOR_SETTINGS_URL", "https://example.com/battery/settings"
            )
            monitor_username = _env_optional("MONITOR_USERNAME", "")
            monitor_password = _env_optional("MONITOR_PASSWORD", "")
            csv_download_selector = _env_optional("CSV_DOWNLOAD_SELECTOR", "")
            settings_charge_limit_selector = _env_optional(
                "SETTINGS_CHARGE_LIMIT_SELECTOR", ""
            )
            settings_submit_selector = _env_optional("SETTINGS_SUBMIT_SELECTOR", "")
        else:
            monitor_login_url = _env("MONITOR_LOGIN_URL")
            monitor_csv_page_url = _env("MONITOR_CSV_PAGE_URL")
            monitor_settings_url = _env("MONITOR_SETTINGS_URL")
            monitor_username = _env("MONITOR_USERNAME")
            monitor_password = _env("MONITOR_PASSWORD")
            csv_download_selector = _env("CSV_DOWNLOAD_SELECTOR")
            settings_charge_limit_selector = _env("SETTINGS_CHARGE_LIMIT_SELECTOR")
            settings_submit_selector = _env("SETTINGS_SUBMIT_SELECTOR")

        return AppConfig(
            timezone=_env_optional("TIMEZONE", "Asia/Tokyo"),
            dry_run=_env_bool("DRY_RUN", True),
            headless=_env_bool("HEADLESS", True),
            timeout_ms=_env_int("BROWSER_TIMEOUT_MS", 45_000),
            artifacts_dir=Path(_env_optional("ARTIFACTS_DIR", "artifacts")),
            history_csv_path=Path(
                _env_optional("HISTORY_CSV_PATH", "artifacts/history.csv")
            ),
            history_sqlite_enabled=_env_bool("HISTORY_SQLITE_ENABLED", True),
            history_sqlite_path=Path(
                _env_optional("HISTORY_SQLITE_PATH", "artifacts/history.db")
            ),
            local_dev_mode=local_dev_mode,
            local_monitor_csv_path=Path(
                _env_optional(
                    "LOCAL_MONITOR_CSV_PATH", "sample_data/monitoring_sample.csv"
                )
            ),
            local_forecast_hours_override=_env_optional_float(
                "LOCAL_FORECAST_HOURS_OVERRIDE"
            ),
            local_current_charge_limit_text=_env_optional(
                "LOCAL_CURRENT_CHARGE_LIMIT_TEXT", ""
            ),
            forecast_url=_env("FORECAST_URL"),
            forecast_ready_selector=_env_optional("FORECAST_READY_SELECTOR", ""),
            forecast_hours_selector=_env("FORECAST_HOURS_SELECTOR"),
            forecast_value_regex=_env_optional(
                "FORECAST_VALUE_REGEX", r"([0-9]+(?:\.[0-9]+)?)"
            ),
            forecast_value_divisor=_env_float("FORECAST_VALUE_DIVISOR", 1.0),
            monitor_login_url=monitor_login_url,
            monitor_csv_page_url=monitor_csv_page_url,
            monitor_settings_url=monitor_settings_url,
            monitor_username=monitor_username,
            monitor_password=monitor_password,
            login_username_selector=_env_optional(
                "LOGIN_USERNAME_SELECTOR", "input[name='username']"
            ),
            login_password_selector=_env_optional(
                "LOGIN_PASSWORD_SELECTOR", "input[name='password']"
            ),
            login_submit_selector=_env_optional(
                "LOGIN_SUBMIT_SELECTOR", "button[type='submit']"
            ),
            login_success_selector=_env_optional("LOGIN_SUCCESS_SELECTOR", ""),
            csv_download_selector=csv_download_selector,
            csv_pre_click_selector=_env_optional("CSV_PRE_CLICK_SELECTOR", ""),
            settings_charge_limit_selector=settings_charge_limit_selector,
            settings_mode_selector=_env_optional("SETTINGS_MODE_SELECTOR", ""),
            settings_submit_selector=settings_submit_selector,
            settings_success_selector=_env_optional("SETTINGS_SUCCESS_SELECTOR", ""),
            settings_current_charge_limit_selector=_env_optional(
                "SETTINGS_CURRENT_CHARGE_LIMIT_SELECTOR", ""
            ),
            csv_timestamp_column=_env_optional("CSV_TIMESTAMP_COLUMN", ""),
            csv_soc_column=_env("CSV_SOC_COLUMN"),
            csv_charge_power_column=_env_optional("CSV_CHARGE_POWER_COLUMN", ""),
            csv_discharge_power_column=_env_optional("CSV_DISCHARGE_POWER_COLUMN", ""),
            forecast_high_hours=_env_float("FORECAST_HIGH_HOURS", 6.0),
            forecast_low_hours=_env_float("FORECAST_LOW_HOURS", 2.0),
            low_soc_threshold=_env_float("LOW_SOC_THRESHOLD", 30.0),
            high_soc_threshold=_env_float("HIGH_SOC_THRESHOLD", 80.0),
            charge_limit_high=_env_int("CHARGE_LIMIT_HIGH", 95),
            charge_limit_mid=_env_int("CHARGE_LIMIT_MID", 70),
            charge_limit_low=_env_int("CHARGE_LIMIT_LOW", 40),
            mode_high_sun=_env_optional("MODE_HIGH_SUN", "charge-priority"),
            mode_mid_sun=_env_optional("MODE_MID_SUN", "balanced"),
            mode_low_sun=_env_optional("MODE_LOW_SUN", "economy"),
        )
