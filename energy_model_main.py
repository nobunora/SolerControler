from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable

import requests

from app.energy_model import (
    NightChargeInputs,
    compute_night_charge_target,
    fit_coefficients_from_csv,
    to_dict,
)


def _load_dotenv_if_present(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if (
            len(value) >= 2
            and ((value[0] == '"' and value[-1] == '"') or (value[0] == "'" and value[-1] == "'"))
        ):
            value = value[1:-1]
        os.environ.setdefault(key, value)


def _latest_kpnet_csv_paths(artifacts_dir: Path) -> list[Path]:
    run_dirs = [p for p in artifacts_dir.glob("*") if p.is_dir() and p.name[:8].isdigit()]
    run_dirs.sort(key=lambda p: p.name, reverse=True)
    for run_dir in run_dirs:
        csv_dir = run_dir / "csv"
        csvs = sorted(csv_dir.glob("*.csv"))
        if csvs:
            return csvs
    raise RuntimeError("artifacts配下にCSVが見つかりませんでした")


def _csv_paths_from_env_or_latest(artifacts_dir: Path) -> list[Path]:
    explicit_dir = os.getenv("ENERGY_MODEL_CSV_DIR", "").strip()
    if explicit_dir:
        csv_dir = Path(explicit_dir)
        csvs = sorted(csv_dir.glob("*.csv"))
        if csvs:
            return csvs
        raise RuntimeError(f"ENERGY_MODEL_CSV_DIR にCSVが見つかりません: {csv_dir}")

    explicit_list = os.getenv("ENERGY_MODEL_CSV_PATHS", "").strip()
    if explicit_list:
        csvs = [Path(p.strip()) for p in explicit_list.split(",") if p.strip()]
        existing = [p for p in csvs if p.exists()]
        if existing:
            return existing
        raise RuntimeError("ENERGY_MODEL_CSV_PATHS のCSVが見つかりませんでした")

    return _latest_kpnet_csv_paths(artifacts_dir)


def _read_rows(csv_paths: Iterable[Path]) -> list[dict[str, float | datetime]]:
    rows: list[dict[str, float | datetime]] = []
    for path in csv_paths:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                d = (row.get("年月日") or "").strip()
                t = (row.get("時刻") or "").strip()
                if not d or not t:
                    continue
                dt = datetime.strptime(f"{d} {t}", "%Y/%m/%d %H:%M")

                def fv(key: str) -> float:
                    v = (row.get(key) or "").strip()
                    return float(v) if v else 0.0

                soc_raw = (row.get("蓄電残量(SOC)[%]") or "").strip()
                soc = float(soc_raw) if soc_raw else float("nan")
                rows.append(
                    {
                        "dt": dt,
                        "load": fv("消費電力量[kWh]"),
                        "pv": fv("発電電力量[kWh]"),
                        "soc": soc,
                    }
                )
    rows.sort(key=lambda x: x["dt"])  # type: ignore[index]
    return rows


def _historical_profile(rows: list[dict[str, float | datetime]]) -> dict[str, float]:
    by_day: dict[str, dict[str, float]] = {}
    for r in rows:
        dt = r["dt"]
        assert isinstance(dt, datetime)
        day = dt.date().isoformat()
        d = by_day.setdefault(day, {"day_load": 0.0, "morning_load": 0.0, "day_pv": 0.0, "morning_pv": 0.0})
        h = dt.hour
        load = float(r["load"])
        pv = float(r["pv"])
        if 7 <= h < 23:
            d["day_load"] += load
            d["day_pv"] += pv
        if 7 <= h < 10:
            d["morning_load"] += load
            d["morning_pv"] += pv

    days = list(by_day.values())
    if not days:
        raise RuntimeError("日次集計対象データがありません")

    avg_day_load = sum(d["day_load"] for d in days) / len(days)
    avg_morning_load = sum(d["morning_load"] for d in days) / len(days)
    sum_day_pv = sum(d["day_pv"] for d in days)
    sum_morning_pv = sum(d["morning_pv"] for d in days)
    morning_pv_ratio = (sum_morning_pv / sum_day_pv) if sum_day_pv > 0 else 0.25

    # 日中余剰比率 (max(0, pv-load) はここでは直接ないので実務初期値)
    midday_surplus_ratio = 0.375
    return {
        "avg_day_load_kwh": avg_day_load,
        "avg_morning_load_kwh": avg_morning_load,
        "morning_pv_ratio": morning_pv_ratio,
        "midday_surplus_ratio": midday_surplus_ratio,
    }


def _tomorrow_forecast(lat: float, lon: float, timezone: str) -> tuple[str, float, float]:
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "sunshine_duration,temperature_2m_mean",
        "timezone": timezone,
        "forecast_days": 3,
    }
    resp = requests.get(url, params=params, timeout=20)
    resp.raise_for_status()
    obj = resp.json()
    times = obj["daily"]["time"]
    sunshine = obj["daily"]["sunshine_duration"]
    temp = obj["daily"]["temperature_2m_mean"]
    if len(times) < 2:
        raise RuntimeError("翌日予報を取得できませんでした")
    return times[1], (sunshine[1] or 0) / 3600.0, float(temp[1] or 0.0)


def _forecast_from_env_or_api(*, lat: float, lon: float, timezone: str) -> tuple[str, float, float]:
    sun_override = os.getenv("FORECAST_SUN_HOURS_OVERRIDE", "").strip()
    if sun_override:
        date_override = os.getenv("FORECAST_DATE_OVERRIDE", "").strip() or datetime.now().date().isoformat()
        temp_override = os.getenv("FORECAST_TEMP_C_OVERRIDE", "").strip() or "20"
        return date_override, float(sun_override), float(temp_override)
    return _tomorrow_forecast(lat=lat, lon=lon, timezone=timezone)


def main() -> int:
    _load_dotenv_if_present()
    artifacts_dir = Path(os.getenv("ARTIFACTS_DIR", "artifacts"))
    csv_paths = _csv_paths_from_env_or_latest(artifacts_dir)
    rows = _read_rows(csv_paths)
    coeff = fit_coefficients_from_csv(csv_paths)
    hist = _historical_profile(rows)

    tomorrow_date, sun_h, temp_c = _forecast_from_env_or_api(
        lat=float(os.getenv("FORECAST_LATITUDE", "35.67452")),
        lon=float(os.getenv("FORECAST_LONGITUDE", "139.48216")),
        timezone=os.getenv("TIMEZONE", "Asia/Tokyo"),
    )

    latest_soc = float(rows[-1]["soc"]) if rows and rows[-1]["soc"] == rows[-1]["soc"] else 30.0
    inp = NightChargeInputs(
        soc_now_percent=latest_soc,
        sun_hours_forecast=sun_h,
        temp_forecast_c=temp_c,
        daytime_load_forecast_kwh=hist["avg_day_load_kwh"],
        morning_load_forecast_kwh=hist["avg_morning_load_kwh"],
        morning_pv_ratio=hist["morning_pv_ratio"],
        midday_surplus_ratio=hist["midday_surplus_ratio"],
        reserve_soc_percent=float(os.getenv("NIGHT_RESERVE_SOC_PERCENT", "10")),
        cycle_count=float(os.getenv("BATTERY_CYCLE_COUNT", "0")),
        battery_temp_c=float(os.getenv("BATTERY_TEMP_C", str(temp_c))),
    )
    result = compute_night_charge_target(coeff, inp)

    payload = {
        "csv_paths": [str(p) for p in csv_paths],
        "forecast": {"date": tomorrow_date, "sun_hours": sun_h, "temp_c": temp_c},
        "historical_profile": hist,
        "coefficients": to_dict(coeff),
        "inputs": to_dict(inp),
        "result": to_dict(result),
    }
    out = artifacts_dir / "night_charge_plan.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
