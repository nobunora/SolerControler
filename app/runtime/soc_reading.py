"""SOC acquisition and fallback decisions for the Cloud Job monitor."""

from __future__ import annotations

import csv
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.domain.constants import validate_soc_percent
from app.runtime.night_soc_time_contract import SOC_OPERATION_MAX_SECONDS


@dataclass(frozen=True)
class SocReading:
    value_percent: float | None
    source: str
    error: str | None
    observed_at: datetime | None


def latest_csv_soc_reading(csv_paths: list[Path]) -> tuple[float | None, datetime | None]:
    latest_dt: datetime | None = None
    latest_soc: float | None = None
    for csv_path in csv_paths:
        if not csv_path.exists():
            continue
        with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            for row in csv.DictReader(csv_file):
                date_text = (row.get("年月日") or "").strip()
                time_text = (row.get("時刻") or "").strip()
                soc_text = (row.get("蓄電残量(SOC)[%]") or "").strip()
                if not date_text or not time_text or not soc_text:
                    continue
                try:
                    observed_at = datetime.strptime(f"{date_text} {time_text}", "%Y/%m/%d %H:%M")
                    soc_percent = validate_soc_percent(float(soc_text), raw=soc_text)
                except (TypeError, ValueError):
                    print(
                        f"[cloud_job_runner] invalid CSV SOC skipped: value={soc_text!r} "
                        f"date={date_text} time={time_text}",
                        flush=True,
                    )
                    continue
                if latest_dt is None or observed_at > latest_dt:
                    latest_dt = observed_at
                    latest_soc = soc_percent
    return latest_soc, latest_dt


def latest_realtime_soc_percent(*, deadline_monotonic: float | None = None) -> float | None:
    from app.kpnet.client import KpNetClient
    from app.kpnet.workflow import KpNetConfig

    operation_start = time.monotonic()
    operation_deadline = deadline_monotonic if deadline_monotonic is not None else operation_start + SOC_OPERATION_MAX_SECONDS
    if operation_deadline <= operation_start:
        raise TimeoutError("SOC deadline expired")
    client = KpNetClient(KpNetConfig.from_env(), deadline_monotonic=operation_deadline)
    client.login()
    try:
        return client.read_realtime_soc_percent()
    finally:
        try:
            client.logout()
        except Exception as exc:
            print(f"[cloud_job_runner] KP-NET logout failed: {exc}", flush=True)


def read_soc_with_fallback(
    csv_paths: list[Path],
    *,
    latest_realtime: Callable[[], float | None],
    latest_csv: Callable[[list[Path]], tuple[float | None, datetime | None]],
    env_int: Callable[[str, int], int],
    env_float: Callable[[str, float], float],
    sleep: Callable[[float], None] = time.sleep,
    deadline_monotonic: float | None = None,
    allow_realtime: bool = True,
) -> SocReading:
    attempts = env_int("ADJUST03_REALTIME_SOC_RETRY_ATTEMPTS", 3)
    delay_seconds = env_float("ADJUST03_REALTIME_SOC_RETRY_DELAY_SECONDS", 2.0)
    errors: list[str] = []
    operation_start = time.monotonic()
    operation_deadline = deadline_monotonic if deadline_monotonic is not None else operation_start + SOC_OPERATION_MAX_SECONDS
    if not allow_realtime:
        errors.append("03 SOC safe budget unavailable")
    elif operation_deadline <= operation_start:
        errors.append("SOC deadline expired")
    else:
        for attempt in range(1, attempts + 1):
            if time.monotonic() >= operation_deadline:
                errors.append("SOC deadline expired")
                break
            try:
                value = latest_realtime()
                if value is not None:
                    return SocReading(value, "realtime", None, datetime.now(ZoneInfo("UTC")))
                errors.append("realtime returned no SOC")
            except Exception as exc:
                errors.append(str(exc))
            if attempt < attempts and delay_seconds > 0:
                sleep_seconds = min(delay_seconds, max(0.0, operation_deadline - time.monotonic()))
                if sleep_seconds > 0:
                    sleep(sleep_seconds)

    csv_value, csv_observed_at = latest_csv(csv_paths)
    if csv_value is not None and csv_observed_at is not None:
        timezone_name = os.getenv("TIMEZONE", "Asia/Tokyo").strip() or "Asia/Tokyo"
        observed_local = csv_observed_at.replace(tzinfo=ZoneInfo(timezone_name))
        max_age_minutes = env_int("ADJUST03_CSV_SOC_MAX_AGE_MINUTES", 120)
        age = datetime.now(ZoneInfo(timezone_name)) - observed_local
        if timedelta(0) <= age <= timedelta(minutes=max_age_minutes):
            return SocReading(csv_value, "csv", "; ".join(errors) or None, observed_local)
        errors.append(f"CSV SOC is stale: observed_at={csv_observed_at.isoformat()}")
    else:
        errors.append("CSV SOC unavailable")
    return SocReading(None, "unavailable", "; ".join(errors), csv_observed_at)
