"""KP-NET configuration shared by the workflow and HTTP client."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from app.configuration.environment import env, env_bool
from app.kpnet.client_support import parse_har_credentials, validate_base_url
from app.kpnet.csv_visualization import _default_csv_target_months
from app.kpnet.rules import _parse_hhmm


LOGGER = logging.getLogger("app.kpnet")


@dataclass(frozen=True)
class KpNetConfig:
    base_url: str
    username: str
    password: str
    dry_run: bool
    timeout_sec: float
    csv_output_format: str
    csv_aggr_type: str
    csv_target_months: list[str]
    download_latest_month: bool
    workflow_mode: str
    settings_sequence: str
    force_settings_profile: str
    dynamic_forced_profile: bool
    dynamic_mode_switch_by_time: bool
    night_plan_path: Path
    default_charge_power_kw: float
    green_mode_max_charge_percent: float
    night_charge_window_start: str
    night_charge_window_end: str
    day_discharge_window_start: str
    day_discharge_window_end: str
    operation_conditions_path: Path
    timezone_name: str
    use_har_credentials: bool
    har_path: Path
    artifacts_dir: Path
    enforce_https: bool
    allowed_hosts: list[str]

    @staticmethod
    def from_env() -> "KpNetConfig":
        username = env("KP_MONITOR_USERNAME", default=env("MONITOR_USERNAME", default=""))
        password = env("KP_MONITOR_PASSWORD", default=env("MONITOR_PASSWORD", default=""))
        use_har_credentials = env_bool("KP_USE_HAR_CREDENTIALS", default=False)
        har_path = Path(env("KP_HAR_PATH", default=""))
        if use_har_credentials and (not username or not password):
            username, password = parse_har_credentials(har_path)
        if not username or not password:
            raise RuntimeError("KP_MONITOR_USERNAME / KP_MONITOR_PASSWORD が未設定です (または HAR から取得できません)")

        raw_months = env("KP_CSV_TARGET_MONTHS", default="")
        months = [month.strip() for month in raw_months.split(",") if month.strip()] or _default_csv_target_months()
        workflow_mode = env("KP_WORKFLOW_MODE", default="all").strip().lower()
        if workflow_mode not in {"all", "csv", "settings"}:
            raise RuntimeError("KP_WORKFLOW_MODE は all / csv / settings のいずれかを指定してください")
        settings_sequence = env("KP_SETTINGS_SEQUENCE", default="forced-only").strip().lower()
        if settings_sequence not in {"forced-only", "forced-then-green"}:
            raise RuntimeError("KP_SETTINGS_SEQUENCE は forced-only / forced-then-green のいずれかを指定してください")
        profile = env("KP_FORCE_SETTINGS_PROFILE", default="auto").strip().lower()
        if profile not in {"auto", "forced", "green", "standby"}:
            raise RuntimeError("KP_FORCE_SETTINGS_PROFILE は auto / forced / green / standby のいずれかを指定してください")
        night_start = env("KP_NIGHT_CHARGE_WINDOW_START", default="23:00").strip()
        night_end = env("KP_NIGHT_CHARGE_WINDOW_END", default="07:00").strip()
        day_start = env("KP_DAY_DISCHARGE_WINDOW_START", default="07:00").strip()
        day_end = env("KP_DAY_DISCHARGE_WINDOW_END", default="23:00").strip()
        for value, name in ((night_start, "KP_NIGHT_CHARGE_WINDOW_START"), (night_end, "KP_NIGHT_CHARGE_WINDOW_END"), (day_start, "KP_DAY_DISCHARGE_WINDOW_START"), (day_end, "KP_DAY_DISCHARGE_WINDOW_END")):
            _parse_hhmm(value, name=name)
        default_charge_power_kw = float(env("KP_DEFAULT_CHARGE_POWER_KW", default="1.8"))
        if default_charge_power_kw <= 0:
            raise RuntimeError("KP_DEFAULT_CHARGE_POWER_KW は 0 より大きい値を指定してください")
        green_limit = float(env("KP_GREEN_MODE_MAX_CHARGE_PERCENT", default="50"))
        if green_limit < 0:
            raise RuntimeError("KP_GREEN_MODE_MAX_CHARGE_PERCENT は 0 以上を指定してください")
        base_url = env("KP_BASE_URL", default="https://ctrl.kp-net.com/settingcontrol").strip()
        enforce_https = env_bool("KP_ENFORCE_HTTPS", default=True)
        allowed_hosts = [host.strip() for host in env("KP_ALLOWED_HOSTS", default="ctrl.kp-net.com").split(",") if host.strip()]
        validate_base_url(base_url=base_url, enforce_https=enforce_https, allowed_hosts=allowed_hosts)
        artifacts_dir = Path(env("ARTIFACTS_DIR", default="artifacts"))
        return KpNetConfig(
            base_url=base_url, username=username, password=password, dry_run=env_bool("DRY_RUN", default=True),
            timeout_sec=float(env("KP_TIMEOUT_SEC", default="60")), csv_output_format=env("KP_CSV_OUTPUT_FORMAT", default="太陽光発電＋蓄電池"),
            csv_aggr_type=env("KP_CSV_AGGR_TYPE", default="30分データ"), csv_target_months=months,
            download_latest_month=env_bool("KP_DOWNLOAD_LATEST_MONTH", default=True), workflow_mode=workflow_mode,
            settings_sequence=settings_sequence, force_settings_profile=profile,
            dynamic_forced_profile=env_bool("KP_DYNAMIC_FORCED_PROFILE", default=True),
            dynamic_mode_switch_by_time=env_bool("KP_DYNAMIC_MODE_SWITCH_BY_TIME", default=True),
            night_plan_path=Path(env("KP_NIGHT_PLAN_PATH", default=str(artifacts_dir / "night_charge_plan.json"))),
            default_charge_power_kw=default_charge_power_kw, green_mode_max_charge_percent=green_limit,
            night_charge_window_start=night_start, night_charge_window_end=night_end,
            day_discharge_window_start=day_start, day_discharge_window_end=day_end,
            operation_conditions_path=Path(env("KP_OPERATION_CONDITIONS_PATH", default="config/operation_conditions.json")),
            timezone_name=env("TIMEZONE", default="Asia/Tokyo").strip() or "Asia/Tokyo",
            use_har_credentials=use_har_credentials, har_path=har_path, artifacts_dir=artifacts_dir,
            enforce_https=enforce_https, allowed_hosts=allowed_hosts,
        )
