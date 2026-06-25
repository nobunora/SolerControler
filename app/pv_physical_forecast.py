from __future__ import annotations

"""Physical PV forecast candidate and diagnostics.

The model is intentionally small: build a clear-sky panel shape from solar
geometry, attenuate it with hourly shortwave radiation, then apply historical
ratio scales only when enough samples exist.
"""

import math
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from app.utils import env_bool, env_float, env_float_clamped, to_float


HOURS = range(7, 23)
DAYPARTS = {
    "morning": range(7, 11),
    "midday": range(11, 15),
    "evening": range(15, 23),
}
ALTITUDE_BINS = (0.0, 15.0, 30.0, 45.0, 90.0)
SHORTWAVE_RATIO_BINS = (0.0, 0.25, 0.5, 0.75, 1.25)


@dataclass(frozen=True)
class PhysicalPvCandidate:
    hourly_pv_kwh: dict[int, float]
    diagnostics: dict[str, object]


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default)).strip()))
    except ValueError:
        return default


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _solar_position(*, day: date, hour: int, lat: float, lon: float, timezone: str) -> tuple[float, float]:
    tz = ZoneInfo(timezone)
    dt = datetime.combine(day, time(hour=hour, minute=30), tzinfo=tz)
    n = day.timetuple().tm_yday
    gamma = 2.0 * math.pi / 365.0 * (n - 1 + (hour + 0.5 - 12.0) / 24.0)
    eqtime = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2.0 * gamma)
        - 0.040849 * math.sin(2.0 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2.0 * gamma)
        + 0.000907 * math.sin(2.0 * gamma)
        - 0.002697 * math.cos(3.0 * gamma)
        + 0.00148 * math.sin(3.0 * gamma)
    )
    offset_min = dt.utcoffset().total_seconds() / 60.0 if dt.utcoffset() else 0.0
    time_offset = eqtime + 4.0 * lon - offset_min
    true_solar_minutes = (hour * 60.0 + 30.0 + time_offset) % 1440.0
    hour_angle = math.radians(true_solar_minutes / 4.0 - 180.0)
    lat_rad = math.radians(lat)
    cos_zenith = (
        math.sin(lat_rad) * math.sin(decl)
        + math.cos(lat_rad) * math.cos(decl) * math.cos(hour_angle)
    )
    cos_zenith = _clip(cos_zenith, -1.0, 1.0)
    zenith = math.acos(cos_zenith)
    altitude = 90.0 - math.degrees(zenith)
    if altitude <= 0:
        return altitude, 180.0
    azimuth = math.degrees(
        math.atan2(
            math.sin(hour_angle),
            math.cos(hour_angle) * math.sin(lat_rad) - math.tan(decl) * math.cos(lat_rad),
        )
    ) + 180.0
    return altitude, azimuth % 360.0


def _panel_incidence_weight(*, altitude_deg: float, azimuth_deg: float, panel_azimuth_deg: float, tilt_deg: float) -> float:
    if altitude_deg <= 0:
        return 0.0
    altitude = math.radians(altitude_deg)
    az_delta = math.radians(azimuth_deg - panel_azimuth_deg)
    tilt = math.radians(tilt_deg)
    cos_incidence = math.sin(altitude) * math.cos(tilt) + math.cos(altitude) * math.sin(tilt) * math.cos(az_delta)
    return max(0.0, cos_incidence)


def _geometry_weight(*, day: date, hour: int, lat: float, lon: float, timezone: str, roof_pitch_deg: float) -> dict[str, float]:
    altitude, azimuth = _solar_position(day=day, hour=hour, lat=lat, lon=lon, timezone=timezone)
    east = env_float("PHYSICAL_PV_PANEL_WEIGHT_EAST", default=1.0)
    south = env_float("PHYSICAL_PV_PANEL_WEIGHT_SOUTH", default=1.0)
    west = env_float("PHYSICAL_PV_PANEL_WEIGHT_WEST", default=1.0)
    weighted = (
        east * _panel_incidence_weight(altitude_deg=altitude, azimuth_deg=azimuth, panel_azimuth_deg=90.0, tilt_deg=roof_pitch_deg)
        + south * _panel_incidence_weight(altitude_deg=altitude, azimuth_deg=azimuth, panel_azimuth_deg=180.0, tilt_deg=roof_pitch_deg)
        + west * _panel_incidence_weight(altitude_deg=altitude, azimuth_deg=azimuth, panel_azimuth_deg=270.0, tilt_deg=roof_pitch_deg)
    )
    return {"altitude_deg": altitude, "azimuth_deg": azimuth, "weight": weighted}


def _hourly_weather_map(forecast: dict[str, object]) -> dict[int, dict[str, float]]:
    out: dict[int, dict[str, float]] = {}
    hourly = forecast.get("hourly_weather")
    if not isinstance(hourly, list):
        return out
    for item in hourly:
        if not isinstance(item, dict):
            continue
        hour = to_float(item.get("hour"))
        shortwave = to_float(item.get("shortwave_radiation_w_m2"))
        if hour is None or shortwave is None:
            continue
        h = int(hour)
        if h in HOURS:
            out[h] = {"shortwave": max(0.0, shortwave)}
    return out


def _actual_hourly(rows: list[dict[str, object]], *, target_date: str) -> dict[str, dict[int, float]]:
    out: dict[str, dict[int, float]] = defaultdict(dict)
    for row in rows:
        dt = row.get("dt")
        if not isinstance(dt, datetime):
            continue
        day = dt.date().isoformat()
        if day >= target_date:
            continue
        if dt.hour not in HOURS:
            continue
        pv = to_float(row.get("pv"))
        if pv is None:
            continue
        out[day][dt.hour] = out[day].get(dt.hour, 0.0) + max(0.0, pv)
    return dict(out)


def _actual_hourly_from_sqlite(*, target_date: str) -> dict[str, dict[int, float]]:
    db_path = os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db").strip()
    if not db_path:
        return {}
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT substr(ts,1,10) AS date,
                       CAST(substr(ts,12,2) AS INTEGER) AS hour,
                       COALESCE(SUM(COALESCE(pv_kwh,0)), 0) AS pv_kwh
                FROM monitoring_samples
                WHERE substr(ts,1,10) < ?
                GROUP BY substr(ts,1,10), CAST(substr(ts,12,2) AS INTEGER)
                ORDER BY date, hour
                """,
                (target_date,),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return {}
    out: dict[str, dict[int, float]] = defaultdict(dict)
    for row in rows:
        hour = int(row["hour"])
        if hour in HOURS:
            out[str(row["date"])][hour] = max(0.0, float(row["pv_kwh"] or 0.0))
    return dict(out)


def _actual_hourly_history(rows: list[dict[str, object]], *, target_date: str) -> dict[str, dict[int, float]]:
    actual = _actual_hourly(rows, target_date=target_date)
    sqlite_actual = _actual_hourly_from_sqlite(target_date=target_date)
    for day, by_hour in sqlite_actual.items():
        target = actual.setdefault(day, {})
        for hour, value in by_hour.items():
            target.setdefault(hour, value)
    return actual


def _daypart(hour: int) -> str:
    for name, hours in DAYPARTS.items():
        if hour in hours:
            return name
    return "evening"


def _bin(value: float, edges: tuple[float, ...]) -> str:
    for idx in range(len(edges) - 1):
        if edges[idx] <= value < edges[idx + 1]:
            return f"{edges[idx]:g}-{edges[idx + 1]:g}"
    return f"{edges[-2]:g}-{edges[-1]:g}"


def _clear_horizontal_w_m2(altitude_deg: float) -> float:
    return max(0.0, 1000.0 * math.sin(math.radians(max(0.0, altitude_deg))))


def _candidate_shape(
    *,
    day: date,
    lat: float,
    lon: float,
    timezone: str,
    roof_pitch_deg: float,
    shortwave_by_hour: dict[int, dict[str, float]],
) -> tuple[dict[int, float], dict[int, dict[str, float]]]:
    shape: dict[int, float] = {}
    features: dict[int, dict[str, float]] = {}
    max_shortwave_ratio = env_float("PHYSICAL_PV_MAX_SHORTWAVE_RATIO", default=1.2)
    for hour in HOURS:
        geom = _geometry_weight(day=day, hour=hour, lat=lat, lon=lon, timezone=timezone, roof_pitch_deg=roof_pitch_deg)
        clear_horizontal = _clear_horizontal_w_m2(float(geom["altitude_deg"]))
        shortwave = shortwave_by_hour.get(hour, {}).get("shortwave")
        ratio = 0.0 if shortwave is None or clear_horizontal <= 0 else _clip(shortwave / clear_horizontal, 0.0, max_shortwave_ratio)
        value = max(0.0, float(geom["weight"])) * ratio
        shape[hour] = value
        features[hour] = {
            "altitude_deg": float(geom["altitude_deg"]),
            "azimuth_deg": float(geom["azimuth_deg"]),
            "shortwave_ratio": ratio,
        }
    return shape, features


def _ratio_stats(values: list[float]) -> dict[str, float]:
    if not values:
        return {"count": 0.0, "scale": 1.0}
    return {"count": float(len(values)), "scale": sum(values) / len(values)}


def _historical_scales(
    *,
    rows: list[dict[str, object]],
    forecast_history: dict[str, dict[int, dict[str, float]]],
    target_date: str,
    lat: float,
    lon: float,
    timezone: str,
    roof_pitch_deg: float,
    base_scale: float,
) -> dict[str, object]:
    actual = _actual_hourly_history(rows, target_date=target_date)
    global_ratios: list[float] = []
    daypart_ratios: dict[str, list[float]] = defaultdict(list)
    bin_ratios: dict[str, list[float]] = defaultdict(list)
    days_used: set[str] = set()
    scale_min = env_float("PHYSICAL_PV_SCALE_MIN", default=0.5)
    scale_max = env_float("PHYSICAL_PV_SCALE_MAX", default=1.8)
    for day_text, by_hour in sorted(forecast_history.items()):
        day = _parse_date(day_text)
        if day is None or day_text not in actual:
            continue
        shortwave_by_hour = {
            hour: {"shortwave": max(0.0, float(values.get("shortwave", 0.0)))}
            for hour, values in by_hour.items()
            if hour in HOURS and values.get("shortwave") is not None
        }
        if not shortwave_by_hour:
            continue
        shape, features = _candidate_shape(
            day=day,
            lat=lat,
            lon=lon,
            timezone=timezone,
            roof_pitch_deg=roof_pitch_deg,
            shortwave_by_hour=shortwave_by_hour,
        )
        pred_total = sum(shape.values()) * base_scale
        actual_total = sum(actual[day_text].get(hour, 0.0) for hour in HOURS)
        if pred_total <= 0 or actual_total <= 0.2:
            continue
        global_ratios.append(_clip(actual_total / pred_total, scale_min, scale_max))
        days_used.add(day_text)
        for part, hours in DAYPARTS.items():
            pred = sum(shape[h] for h in hours) * base_scale
            act = sum(actual[day_text].get(h, 0.0) for h in hours)
            if pred > 0 and act > 0.05:
                daypart_ratios[part].append(_clip(act / pred, scale_min, scale_max))
        for hour, pred_shape in shape.items():
            pred = pred_shape * base_scale
            act = actual[day_text].get(hour, 0.0)
            if pred <= 0 or act <= 0.01:
                continue
            f = features[hour]
            key = f"alt:{_bin(f['altitude_deg'], ALTITUDE_BINS)}|sw:{_bin(f['shortwave_ratio'], SHORTWAVE_RATIO_BINS)}"
            bin_ratios[key].append(_clip(act / pred, scale_min, scale_max))
    return {
        "global": _ratio_stats(global_ratios),
        "dayparts": {k: _ratio_stats(v) for k, v in sorted(daypart_ratios.items())},
        "bins": {k: _ratio_stats(v) for k, v in sorted(bin_ratios.items())},
        "history_days": sorted(days_used),
    }


def _derive_radiation_scale(
    *,
    rows: list[dict[str, object]],
    forecast_history: dict[str, dict[int, dict[str, float]]],
    target_date: str,
    lat: float,
    lon: float,
    timezone: str,
    roof_pitch_deg: float,
) -> dict[str, object]:
    actual = _actual_hourly_history(rows, target_date=target_date)
    ratios: list[float] = []
    days_used: list[str] = []
    for day_text, by_hour in sorted(forecast_history.items()):
        day = _parse_date(day_text)
        if day is None or day_text not in actual:
            continue
        shortwave_by_hour = {
            hour: {"shortwave": max(0.0, float(values.get("shortwave", 0.0)))}
            for hour, values in by_hour.items()
            if hour in HOURS and values.get("shortwave") is not None
        }
        if not shortwave_by_hour:
            continue
        shape, _features = _candidate_shape(
            day=day,
            lat=lat,
            lon=lon,
            timezone=timezone,
            roof_pitch_deg=roof_pitch_deg,
            shortwave_by_hour=shortwave_by_hour,
        )
        shape_total = sum(shape.values())
        actual_total = sum(actual[day_text].get(hour, 0.0) for hour in HOURS)
        if shape_total <= 0 or actual_total <= 0.2:
            continue
        ratios.append(actual_total / shape_total)
        days_used.append(day_text)
    if not ratios:
        return {"scale": 1.0, "sample_count": 0, "days": []}
    ratios.sort()
    mid = len(ratios) // 2
    scale = ratios[mid] if len(ratios) % 2 else (ratios[mid - 1] + ratios[mid]) / 2.0
    return {"scale": scale, "sample_count": len(ratios), "days": days_used[-10:]}


def build_physical_pv_candidate(
    *,
    rows: list[dict[str, object]],
    forecast_history: dict[str, dict[int, dict[str, float]]],
    existing_hourly_pv: dict[int, float],
    forecast: dict[str, object],
    target_date: str,
    lat: float,
    lon: float,
    timezone: str,
) -> PhysicalPvCandidate:
    if not env_bool("PHYSICAL_PV_FORECAST_ENABLED", default=True):
        return PhysicalPvCandidate(existing_hourly_pv, {"enabled": False, "selected_method": "existing", "decision_path": ["disabled"]})

    target_day = _parse_date(target_date)
    if target_day is None:
        return PhysicalPvCandidate(existing_hourly_pv, {"enabled": False, "selected_method": "existing", "decision_path": ["invalid_target_date"]})

    shortwave_by_hour = _hourly_weather_map(forecast)
    if _env_int("PHYSICAL_PV_MIN_SHORTWAVE_HOURS", 4) > len(shortwave_by_hour):
        return PhysicalPvCandidate(existing_hourly_pv, {
            "enabled": False,
            "selected_method": "existing",
            "fallback_reason": "shortwave_missing",
            "decision_path": ["shortwave_missing", "selected_existing"],
            "data_quality": {"shortwave_hours": sorted(shortwave_by_hour), "required": _env_int("PHYSICAL_PV_MIN_SHORTWAVE_HOURS", 4)},
        })

    roof_pitch_deg = env_float("PHYSICAL_PV_ROOF_PITCH_DEG", default=21.8014)
    base_scale = env_float("PHYSICAL_PV_RADIATION_SCALE", default=0.0)
    shape, features = _candidate_shape(
        day=target_day,
        lat=lat,
        lon=lon,
        timezone=timezone,
        roof_pitch_deg=roof_pitch_deg,
        shortwave_by_hour=shortwave_by_hour,
    )
    shape_total = sum(shape.values())
    if shape_total <= 0:
        return PhysicalPvCandidate(existing_hourly_pv, {"enabled": False, "selected_method": "existing", "fallback_reason": "zero_physical_shape", "decision_path": ["zero_physical_shape", "selected_existing"]})

    existing_total = sum(max(0.0, v) for v in existing_hourly_pv.values())
    radiation_scale_source = "env"
    radiation_scale_fit: dict[str, object] = {}
    if base_scale <= 0:
        radiation_scale_fit = _derive_radiation_scale(
            rows=rows,
            forecast_history=forecast_history,
            target_date=target_date,
            lat=lat,
            lon=lon,
            timezone=timezone,
            roof_pitch_deg=roof_pitch_deg,
        )
        base_scale = float(radiation_scale_fit.get("scale") or 1.0)
        radiation_scale_source = "history_median"

    scales = _historical_scales(
        rows=rows,
        forecast_history=forecast_history,
        target_date=target_date,
        lat=lat,
        lon=lon,
        timezone=timezone,
        roof_pitch_deg=roof_pitch_deg,
        base_scale=base_scale,
    )
    min_days = _env_int("PHYSICAL_PV_GLOBAL_MIN_DAYS", 5)
    daypart_min = _env_int("PHYSICAL_PV_DAYPART_MIN_SAMPLES", 20)
    bin_min = _env_int("PHYSICAL_PV_BIN_MIN_SAMPLES", 30)
    global_count = int(scales["global"]["count"])  # type: ignore[index]
    decision_path = ["shortwave_available", "physical_candidate_built"]
    selected_method = "physical_unscaled"
    if global_count >= min_days:
        decision_path.append("global_scale_sample_threshold_met")
        selected_method = "physical_global"
    else:
        decision_path.extend(["global_scale_sample_threshold_not_met", "selected_existing"])
        return PhysicalPvCandidate(existing_hourly_pv, {
            "enabled": False,
            "selected_method": "existing",
            "fallback_reason": "sample_shortage",
            "decision_path": decision_path,
            "data_quality": {"global_days": global_count, "global_days_required": min_days, "shortwave_hours": sorted(shortwave_by_hour)},
            "candidates": {"existing": {"total_kwh": round(existing_total, 4)}, "physical_unscaled": {"total_kwh": round(shape_total * base_scale, 4)}},
        })

    global_scale = float(scales["global"]["scale"])  # type: ignore[index]
    hourly: dict[int, float] = {}
    bin_used = 0
    daypart_used = 0
    for hour in HOURS:
        scale = global_scale
        part = _daypart(hour)
        part_stats = scales["dayparts"].get(part, {}) if isinstance(scales.get("dayparts"), dict) else {}
        if float(part_stats.get("count", 0.0)) >= daypart_min:
            scale = float(part_stats.get("scale", scale))
            daypart_used += 1
            selected_method = "physical_daypart"
        f = features[hour]
        key = f"alt:{_bin(f['altitude_deg'], ALTITUDE_BINS)}|sw:{_bin(f['shortwave_ratio'], SHORTWAVE_RATIO_BINS)}"
        bin_stats = scales["bins"].get(key, {}) if isinstance(scales.get("bins"), dict) else {}
        if float(bin_stats.get("count", 0.0)) >= bin_min:
            scale = float(bin_stats.get("scale", scale))
            bin_used += 1
            selected_method = "physical_altitude_shortwave"
        hourly[hour] = max(0.0, shape[hour] * base_scale * scale)

    decision_path.append("selected_" + selected_method)
    if daypart_used == 0:
        decision_path.append("daypart_threshold_not_met")
    if bin_used == 0:
        decision_path.append("altitude_shortwave_threshold_not_met")
    physical_total = sum(hourly.values())
    diagnostics = {
        "enabled": True,
        "selected_method": selected_method,
        "fallback_reason": None,
        "decision_path": decision_path,
        "input": {
            "lat": lat,
            "lon": lon,
            "timezone": timezone,
            "roof_pitch_deg": round(roof_pitch_deg, 4),
            "panel_azimuths_deg": {"east": 90, "south": 180, "west": 270},
            "panel_weights": {
                "east": env_float("PHYSICAL_PV_PANEL_WEIGHT_EAST", default=1.0),
                "south": env_float("PHYSICAL_PV_PANEL_WEIGHT_SOUTH", default=1.0),
                "west": env_float("PHYSICAL_PV_PANEL_WEIGHT_WEST", default=1.0),
            },
            "shortwave_hours": sorted(shortwave_by_hour),
        },
        "data_quality": {
            "global_days": global_count,
            "global_days_required": min_days,
            "daypart_min_samples": daypart_min,
            "bin_min_samples": bin_min,
            "bin_hours_used": bin_used,
            "daypart_hours_used": daypart_used,
            "history_days": scales["history_days"],
        },
        "scales": {
            "radiation_scale": round(base_scale, 6),
            "radiation_scale_source": radiation_scale_source,
            "radiation_scale_fit": radiation_scale_fit,
            "global_bias_scale": round(global_scale, 6),
            "daypart": scales["dayparts"],
            "altitude_shortwave_bins": scales["bins"],
        },
        "candidates": {
            "existing": {"total_kwh": round(existing_total, 4), "status": "rejected"},
            selected_method: {"total_kwh": round(physical_total, 4), "status": "selected"},
        },
        "developer": {
            "altitude_bins": ALTITUDE_BINS,
            "shortwave_ratio_bins": SHORTWAVE_RATIO_BINS,
            "hour_features": {str(h): {k: round(v, 4) for k, v in features[h].items()} for h in HOURS},
        },
        "retirement_recommendation": _retirement_recommendation(global_count=global_count, selected_method=selected_method),
    }
    return PhysicalPvCandidate(hourly, diagnostics)


def _retirement_recommendation(*, global_count: int, selected_method: str) -> dict[str, object]:
    required_days = _env_int("PHYSICAL_PV_RETIRE_EXISTING_MIN_DAYS", 21)
    if global_count < required_days:
        return {
            "existing_pv_ewma": {
                "status": "watch",
                "reason": "physical_model_valid_days_below_threshold",
                "valid_days": global_count,
                "required_days": required_days,
            }
        }
    return {
        "existing_pv_ewma": {
            "status": "review",
            "reason": f"{selected_method}_has_enough_valid_days; compare rolling MAE before removal",
            "valid_days": global_count,
            "required_days": required_days,
        }
    }
