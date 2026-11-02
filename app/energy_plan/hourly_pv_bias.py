"""Bounded, frozen-origin hourly PV residuals with forward-only admission.

This changes a forecast, never the independent capability ledger. An incomplete
source or an unverified replay produces a documented neutral forecast.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from app.forecasting.correction_calculations import weather_class
from app.energy_plan.battery_response import BatterySocResponse

LOOKBACK_DAYS = 90
MIN_BIN_DAYS = 20
MIN_VALIDATION_DAYS = 5
MAX_SNAPSHOT_ROWS = LOOKBACK_DAYS * 24 * 8
MODEL_ID = "frozen_hour_weather_bias_v1"


@dataclass(frozen=True)
class PvBiasDay:
    day: date
    issued_at: datetime
    available_at: datetime
    forecast_run_id: str
    hourly_forecast_pv_kwh: dict[int, float]
    hourly_maximum_kwh: dict[int, float]
    hourly_load_kwh: dict[int, float]
    hourly_weather_class: dict[int, str]
    hourly_actual_pv_kwh: dict[int, float]
    hourly_actual_load_kwh: dict[int, float]
    hourly_actual_buy_kwh: dict[int, float]
    hourly_actual_sell_kwh: dict[int, float]
    # Raw h:00 readings, not an inferred interval-start SOC contract.
    hourly_actual_soc_percent: dict[int, float]
    actual_rows: tuple[dict[str, Any], ...]
    source_sha256: str
    battery_response_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class ReplayErrors:
    soc_mae: float
    buy_mae: float
    fee_mae: float

    def valid(self) -> bool:
        return all(math.isfinite(value) and value >= 0 for value in
                   (self.soc_mae, self.buy_mae, self.fee_mae))


@dataclass(frozen=True)
class PvBiasModel:
    deltas: dict[tuple[int, str], float]
    training_days: tuple[str, ...]
    validated_at: datetime
    source_sha256: str


def _stamp(value: object) -> datetime | None:
    try:
        result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result if result.utcoffset() is not None else None
    except (TypeError, ValueError):
        return None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) and result >= 0 else None


def forecast_weather_by_hour(forecast: dict[str, object]) -> dict[int, str]:
    """Use the provider's already-normalized hourly view; never shift timestamps."""
    out = dict.fromkeys(range(24), "unknown")
    rows = forecast.get("hourly_weather")
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        hour = row.get("hour")
        if not isinstance(hour, int) or isinstance(hour, bool) or not 0 <= hour < 24:
            stamp = _stamp(row.get("time"))
            hour = stamp.hour if stamp is not None else None
        code = _number(row.get("weather_code"))
        if hour is not None and code is not None:
            out[hour] = weather_class(code)
    return out


def load_pv_bias_snapshots(*, target_date: str) -> tuple[list[dict[str, Any]], str]:
    """One bounded existing snapshot collection read, no canonical/rebuilt forecasts."""
    start = (date.fromisoformat(target_date) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    try:
        if os.getenv("DATA_BACKEND", "").strip().lower() == "firestore":
            from google.cloud import firestore
            from google.cloud.firestore_v1.base_query import FieldFilter

            client = firestore.Client(project=os.getenv("FIRESTORE_PROJECT_ID") or None,
                                      database=os.getenv("FIRESTORE_DATABASE_ID", "(default)"))
            rows = [doc.to_dict() or {} for doc in client.collection("forecast_hourly_snapshots")
                    .where(filter=FieldFilter("date", ">=", start))
                    .where(filter=FieldFilter("date", "<", target_date))
                    .limit(MAX_SNAPSHOT_ROWS + 1).stream(timeout=20)]
            source = "firestore_forecast_hourly_snapshots"
        else:
            path = Path(os.getenv("DATA_DB_PATH", "artifacts/solar_monitor.db"))
            if not path.is_file():
                return [], "unavailable"
            with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
                connection.row_factory = sqlite3.Row
                rows = [dict(row) for row in connection.execute(
                    "SELECT * FROM forecast_hourly_snapshots WHERE date >= ? AND date < ? LIMIT ?",
                    (start, target_date, MAX_SNAPSHOT_ROWS + 1))]
            source = "sqlite_forecast_hourly_snapshots"
    except Exception as exc:
        return [], f"unavailable:{type(exc).__name__}"
    if len(rows) > MAX_SNAPSHOT_ROWS:
        return [], "rejected_snapshot_read_budget"
    return rows, source


def _detail(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("pv_forecast_detail", row.get("pv_forecast_detail_json"))
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    if not isinstance(value, dict):
        return {}
    result = value.get("hourly_pv_bias")
    return result if isinstance(result, dict) else {}


def _actual_availability(row: dict[str, Any]) -> datetime | None:
    # Measurement time is not acquisition time. Never invent dt + 30 minutes.
    stamps = [_stamp(row.get(key)) for key in ("available_at", "ingested_at", "updated_at")]
    available = [stamp for stamp in stamps if stamp is not None]
    return max(available) if available else None


def _collect_day(day: date, forecasts: list[dict[str, Any]], actuals: list[dict[str, Any]],
                 *, zone: ZoneInfo, as_of: datetime) -> PvBiasDay | None:
    by_slot: dict[tuple[int, int], dict[str, Any]] = {}
    available: list[datetime] = []
    for row in actuals:
        stamp = row.get("dt")
        if not isinstance(stamp, datetime):
            return None
        local = stamp.replace(tzinfo=zone) if stamp.utcoffset() is None else stamp.astimezone(zone)
        slot = (local.hour, local.minute)
        if local.date() != day or local.minute not in (0, 30) or local.second or slot in by_slot:
            return None
        acquired = _actual_availability(row)
        if acquired is None or acquired > as_of or acquired < local:
            return None
        if any(_number(row.get(key)) is None for key in ("pv", "load", "buy", "sell", "soc")):
            return None
        if float(row["soc"]) > 100:
            return None
        by_slot[slot] = row
        available.append(acquired)
    if len(by_slot) != 48 or len(forecasts) != 24:
        return None
    by_hour: dict[int, dict[str, Any]] = {}
    pv: dict[int, float] = {}
    cap: dict[int, float] = {}
    load: dict[int, float] = {}
    weather: dict[int, str] = {}
    issuance: set[datetime] = set()
    run_ids: set[str] = set()
    for row in forecasts:
        hour = row.get("hour")
        issue, recorded = _stamp(row.get("issued_at")), _stamp(row.get("recorded_at"))
        if (not isinstance(hour, int) or isinstance(hour, bool) or not 0 <= hour < 24 or hour in by_hour
                or row.get("date") != day.isoformat() or row.get("issued_at_source") != "plan"
                or issue is None or recorded is None or recorded > as_of or recorded < issue
                or issue > datetime.combine(day, datetime.min.time(), zone) + timedelta(hours=7)):
            return None
        detail = _detail(row)
        baseline, maximum, predicted_load = (_number(detail.get("uncorrected_pv_kwh")),
                                              _number(detail.get("maximum_kwh")),
                                              _number(row.get("forecast_load_kwh")))
        cls = detail.get("weather_class")
        if (baseline is None or maximum is None or predicted_load is None or baseline > maximum
                or not isinstance(cls, str) or cls == "unknown"
                or detail.get("model_id") != "physical_pv_ideal_max_global_v1"):
            return None
        run_id = row.get("forecast_run_id")
        if not isinstance(run_id, str) or not run_id:
            return None
        issuance.add(issue)
        run_ids.add(run_id)
        available.append(recorded)
        by_hour[hour], pv[hour], cap[hour], load[hour], weather[hour] = row, baseline, maximum, predicted_load, cls
    if len(issuance) != 1 or len(run_ids) != 1:
        return None
    response = _detail(by_hour[7]).get("battery_response")
    if not isinstance(response, dict):
        return None
    response_as_of_value = _detail(by_hour[7]).get("battery_response_as_of")
    if response_as_of_value is not None:
        response_as_of = _stamp(response_as_of_value)
        if response_as_of is None or response_as_of > next(iter(issuance)):
            return None
    try:
        BatterySocResponse.from_payload(response)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    totals = {key: {hour: sum(float(by_slot[(hour, minute)][key]) for minute in (0, 30))
                    for hour in range(24)} for key in ("pv", "load", "buy", "sell")}
    ordered_actuals = tuple(by_slot[slot] for slot in sorted(by_slot))
    try:
        source = json.dumps({"forecast": [by_hour[h] for h in range(24)], "actual": ordered_actuals},
                            sort_keys=True, separators=(",", ":"), default=str, allow_nan=False)
    except (ValueError, TypeError):
        return None
    return PvBiasDay(day, next(iter(issuance)), max(available), next(iter(run_ids)), pv, cap, load, weather,
                     totals["pv"], totals["load"], totals["buy"], totals["sell"],
                     {h: float(by_slot[(h, 0)]["soc"]) for h in range(24)}, ordered_actuals,
                     hashlib.sha256(source.encode()).hexdigest(), response)


def collect_pv_bias_days(*, target_date: str, as_of: datetime, timezone_name: str,
                         actual_rows: list[dict[str, Any]], snapshot_rows: list[dict[str, Any]] | None = None,
                         ) -> tuple[tuple[PvBiasDay, ...], dict[str, Any]]:
    if as_of.utcoffset() is None:
        raise ValueError("PV bias requires aware as_of")
    target, zone = date.fromisoformat(target_date), ZoneInfo(timezone_name)
    start = target - timedelta(days=LOOKBACK_DAYS)
    actuals: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for row in actual_rows:
        stamp = row.get("dt")
        if isinstance(stamp, datetime):
            local = stamp.replace(tzinfo=zone) if stamp.utcoffset() is None else stamp.astimezone(zone)
            if start <= local.date() < min(target, as_of.astimezone(zone).date()):
                actuals[local.date()].append(row)
    if not actuals:
        return (), {"source": "not_read_no_prior_actuals", "accepted_days": 0,
                    "rejected_days": 0, "reason": "no_prior_actuals_with_availability"}
    if not any(all(_actual_availability(row) is not None for row in rows) for rows in actuals.values()):
        return (), {"source": "not_read_missing_actual_availability", "accepted_days": 0,
                    "rejected_days": len(actuals), "reason": "missing_actual_acquisition_provenance"}
    source = "supplied_frozen_snapshots"
    if snapshot_rows is None:
        snapshot_rows, source = load_pv_bias_snapshots(target_date=target_date)
    if len(snapshot_rows) > MAX_SNAPSHOT_ROWS:
        return (), {"source": source, "accepted_days": 0, "rejected_days": len(actuals),
                    "reason": "snapshot_read_budget"}
    runs: dict[date, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for row in snapshot_rows:
        try:
            day = date.fromisoformat(str(row.get("date")))
        except ValueError:
            continue
        if day in actuals:
            runs[day][str(row.get("forecast_run_id", ""))].append(row)
    days: list[PvBiasDay] = []
    for day in sorted(actuals):
        candidates = sorted(runs[day].values(), key=lambda rows:
                            _stamp(rows[0].get("issued_at")) or datetime.max.replace(tzinfo=zone))
        for candidate in candidates:
            record = _collect_day(day, candidate, actuals[day], zone=zone, as_of=as_of)
            if record is not None:
                days.append(record)
                break
    return tuple(days), {"source": source, "accepted_days": len(days), "rejected_days": len(actuals) - len(days),
                         "lookback_days": LOOKBACK_DAYS, "snapshot_rows": len(snapshot_rows),
                         "reason": "complete_frozen_pairs" if days else "missing_complete_provenance_or_pairs",
                         "source_sha256": [day.source_sha256 for day in days]}


def _fit(days: tuple[PvBiasDay, ...]) -> tuple[dict[tuple[int, str], float], dict[str, int]]:
    samples: dict[tuple[int, str], dict[date, float]] = defaultdict(dict)
    for day in days:
        for hour in range(24):
            cls = day.hourly_weather_class[hour]
            if cls != "unknown":
                samples[(hour, cls)][day.day] = day.hourly_actual_pv_kwh[hour] - day.hourly_forecast_pv_kwh[hour]
    deltas: dict[tuple[int, str], float] = {}
    counts: dict[str, int] = {}
    for key, values in samples.items():
        count = len(values)
        counts[f"{key[0]}:{key[1]}"] = count
        if count >= MIN_BIN_DAYS:
            # n/(n+20) times the ordinary mean, with one sample per day/bin.
            deltas[key] = math.fsum(value / (count + MIN_BIN_DAYS) for value in values.values())
    return deltas, counts


def _apply(pv: dict[int, float], maximum: dict[int, float], weather: dict[int, str],
           deltas: dict[tuple[int, str], float]) -> dict[int, float]:
    result = {}
    for hour in range(24):
        baseline, limit = _number(pv.get(hour)), _number(maximum.get(hour))
        delta = deltas.get((hour, weather.get(hour, "unknown")), 0.0)
        if baseline is None or limit is None or not math.isfinite(delta):
            raise ValueError("PV bias requires complete finite bounded forecast")
        result[hour] = min(limit, max(0.0, baseline + delta))
    return result


def select_hourly_pv_bias(*, days: tuple[PvBiasDay, ...], as_of: datetime, target_date: str,
                          forecast_pv: dict[int, float], maximum: dict[int, float],
                          weather_by_hour: dict[int, str],
                          evaluate_day: Callable[[PvBiasDay, dict[int, float]], ReplayErrors] | None,
                          previous_model: PvBiasModel | None = None,
                          ) -> tuple[dict[int, float], dict[str, Any], PvBiasModel | None]:
    """Admit only a forward-tested algorithm; no callback means neutral/retained."""
    if as_of.utcoffset() is None:
        raise ValueError("PV bias requires aware as_of")
    target = date.fromisoformat(target_date)
    eligible = tuple(sorted((day for day in days if day.day < target and day.available_at <= as_of
                             and (target - day.day).days <= LOOKBACK_DAYS), key=lambda day: day.day))
    if len({day.day for day in eligible}) != len(eligible):
        raise ValueError("PV bias cannot count duplicate forecast days")
    deltas, counts = _fit(eligible)
    baseline_errors: list[ReplayErrors] = []
    candidate_errors: list[ReplayErrors] = []
    validation_dates: list[str] = []
    rejected_folds = 0
    if evaluate_day is not None:
        for validation in eligible:
            training = tuple(day for day in eligible if day.day < validation.day
                             and day.available_at <= validation.issued_at)
            rolling_deltas, _ = _fit(training)
            if not rolling_deltas:
                continue
            corrected = _apply(validation.hourly_forecast_pv_kwh, validation.hourly_maximum_kwh,
                               validation.hourly_weather_class, rolling_deltas)
            try:
                baseline_error = evaluate_day(validation, dict(validation.hourly_forecast_pv_kwh))
                candidate_error = evaluate_day(validation, corrected)
                if not baseline_error.valid() or not candidate_error.valid():
                    raise ValueError("invalid replay errors")
            except (KeyError, ValueError, ArithmeticError, TypeError):
                rejected_folds += 1
                continue
            baseline_errors.append(baseline_error)
            candidate_errors.append(candidate_error)
            validation_dates.append(validation.day.isoformat())
    means = {}
    admitted = False
    if len(validation_dates) >= MIN_VALIDATION_DAYS and rejected_folds == 0:
        for attr in ("soc_mae", "buy_mae", "fee_mae"):
            means[attr] = {"baseline": math.fsum(getattr(e, attr) / len(baseline_errors) for e in baseline_errors),
                           "candidate": math.fsum(getattr(e, attr) / len(candidate_errors) for e in candidate_errors)}
        admitted = (means["soc_mae"]["candidate"] < means["soc_mae"]["baseline"] - 1e-9
                    and means["buy_mae"]["candidate"] <= means["buy_mae"]["baseline"] + 1e-9
                    and means["fee_mae"]["candidate"] <= means["fee_mae"]["baseline"] + 1e-9)
    digest = hashlib.sha256("".join(day.source_sha256 for day in eligible).encode()).hexdigest()
    selected = PvBiasModel(deltas, tuple(day.day.isoformat() for day in eligible), as_of, digest) if admitted else None
    status = "accepted" if admitted else "neutral"
    if selected is None and previous_model is not None:
        if (previous_model.validated_at.utcoffset() is not None and previous_model.validated_at <= as_of
                and previous_model.training_days and max(previous_model.training_days) < target_date
                and all(math.isfinite(v) for v in previous_model.deltas.values())):
            selected, status = previous_model, "retained_previous"
    diagnostics: dict[str, Any] = {"model_id": MODEL_ID, "status": status, "enabled": selected is not None,
        "training_days": [day.day.isoformat() for day in eligible], "bin_days": counts,
        "minimum_bin_days": MIN_BIN_DAYS, "minimum_validation_days": MIN_VALIDATION_DAYS,
        "validation_days": validation_dates, "validation_errors": means, "rejected_folds": rejected_folds,
        "source_sha256": digest, "as_of": as_of.isoformat(),
        "reason": ("forward_metrics_passed" if admitted else "replay_unavailable" if evaluate_day is None
                   else "insufficient_forward_folds" if len(validation_dates) < MIN_VALIDATION_DAYS
                   else "replay_rejected"),
        "hourly_deltas_kwh": {f"{h}:{cls}": delta for (h, cls), delta in (selected.deltas if selected else {}).items()},
        "uncorrected_hourly_pv_kwh": {str(h): v for h, v in forecast_pv.items()}}
    return _apply(forecast_pv, maximum, weather_by_hour, selected.deltas if selected else {}), diagnostics, selected
