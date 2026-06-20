from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.utils import env, env_bool, env_float, env_int, to_float


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
    mode_force_charge: str
    green_mode_max_charge_percent: float

    @staticmethod
    def from_env() -> "AppConfig":
        local_dev_mode = env_bool("LOCAL_DEV_MODE", default=False)

        if local_dev_mode:
            monitor_login_url = env("MONITOR_LOGIN_URL", default="https://example.com/login")
            monitor_csv_page_url = env("MONITOR_CSV_PAGE_URL", default="https://example.com/operation/csv")
            monitor_settings_url = env("MONITOR_SETTINGS_URL", default="https://example.com/battery/settings")
            monitor_username = env("MONITOR_USERNAME", default="")
            monitor_password = env("MONITOR_PASSWORD", default="")
            csv_download_selector = env("CSV_DOWNLOAD_SELECTOR", default="")
            settings_charge_limit_selector = env("SETTINGS_CHARGE_LIMIT_SELECTOR", default="")
            settings_submit_selector = env("SETTINGS_SUBMIT_SELECTOR", default="")
        else:
            monitor_login_url = env("MONITOR_LOGIN_URL", required=True)
            monitor_csv_page_url = env("MONITOR_CSV_PAGE_URL", required=True)
            monitor_settings_url = env("MONITOR_SETTINGS_URL", required=True)
            monitor_username = env("MONITOR_USERNAME", required=True)
            monitor_password = env("MONITOR_PASSWORD", required=True)
            csv_download_selector = env("CSV_DOWNLOAD_SELECTOR", required=True)
            settings_charge_limit_selector = env("SETTINGS_CHARGE_LIMIT_SELECTOR", required=True)
            settings_submit_selector = env("SETTINGS_SUBMIT_SELECTOR", required=True)

        return AppConfig(
            timezone=env("TIMEZONE", default="Asia/Tokyo"),
            dry_run=env_bool("DRY_RUN", default=True),
            headless=env_bool("HEADLESS", default=True),
            timeout_ms=env_int("BROWSER_TIMEOUT_MS", default=45_000),
            artifacts_dir=Path(env("ARTIFACTS_DIR", default="artifacts")),
            history_csv_path=Path(
                env("HISTORY_CSV_PATH", default="artifacts/history.csv")
            ),
            history_sqlite_enabled=env_bool("HISTORY_SQLITE_ENABLED", default=True),
            history_sqlite_path=Path(
                env("HISTORY_SQLITE_PATH", default="artifacts/history.db")
            ),
            local_dev_mode=local_dev_mode,
            local_monitor_csv_path=Path(
                env("LOCAL_MONITOR_CSV_PATH", default="sample_data/monitoring_sample.csv")
            ),
            local_forecast_hours_override=to_float(env("LOCAL_FORECAST_HOURS_OVERRIDE", default="")),
            local_current_charge_limit_text=env("LOCAL_CURRENT_CHARGE_LIMIT_TEXT", default=""),
            forecast_url=env("FORECAST_URL", required=True),
            forecast_ready_selector=env("FORECAST_READY_SELECTOR", default=""),
            forecast_hours_selector=env("FORECAST_HOURS_SELECTOR", required=True),
            forecast_value_regex=env("FORECAST_VALUE_REGEX", default=r"([0-9]+(?:\.[0-9]+)?)"),
            forecast_value_divisor=env_float("FORECAST_VALUE_DIVISOR", default=1.0),
            monitor_login_url=monitor_login_url,
            monitor_csv_page_url=monitor_csv_page_url,
            monitor_settings_url=monitor_settings_url,
            monitor_username=monitor_username,
            monitor_password=monitor_password,
            login_username_selector=env("LOGIN_USERNAME_SELECTOR", default="input[name='username']"),
            login_password_selector=env("LOGIN_PASSWORD_SELECTOR", default="input[name='password']"),
            login_submit_selector=env("LOGIN_SUBMIT_SELECTOR", default="button[type='submit']"),
            login_success_selector=env("LOGIN_SUCCESS_SELECTOR", default=""),
            csv_download_selector=csv_download_selector,
            csv_pre_click_selector=env("CSV_PRE_CLICK_SELECTOR", default=""),
            settings_charge_limit_selector=settings_charge_limit_selector,
            settings_mode_selector=env("SETTINGS_MODE_SELECTOR", default=""),
            settings_submit_selector=settings_submit_selector,
            settings_success_selector=env("SETTINGS_SUCCESS_SELECTOR", default=""),
            settings_current_charge_limit_selector=env("SETTINGS_CURRENT_CHARGE_LIMIT_SELECTOR", default=""),
            csv_timestamp_column=env("CSV_TIMESTAMP_COLUMN", default=""),
            csv_soc_column=env("CSV_SOC_COLUMN", required=True),
            csv_charge_power_column=env("CSV_CHARGE_POWER_COLUMN", default=""),
            csv_discharge_power_column=env("CSV_DISCHARGE_POWER_COLUMN", default=""),
            forecast_high_hours=env_float("FORECAST_HIGH_HOURS", default=6.0),
            forecast_low_hours=env_float("FORECAST_LOW_HOURS", default=2.0),
            low_soc_threshold=env_float("LOW_SOC_THRESHOLD", default=30.0),
            high_soc_threshold=env_float("HIGH_SOC_THRESHOLD", default=80.0),
            charge_limit_high=env_int("CHARGE_LIMIT_HIGH", default=95),
            charge_limit_mid=env_int("CHARGE_LIMIT_MID", default=70),
            charge_limit_low=env_int("CHARGE_LIMIT_LOW", default=40),
            mode_high_sun=env("MODE_HIGH_SUN", default="charge-priority"),
            mode_mid_sun=env("MODE_MID_SUN", default="balanced"),
            mode_low_sun=env("MODE_LOW_SUN", default="economy"),
            mode_force_charge=env("MODE_FORCE_CHARGE", default="forced-charge"),
            green_mode_max_charge_percent=env_float("GREEN_MODE_MAX_CHARGE_PERCENT", default=50.0),
        )
