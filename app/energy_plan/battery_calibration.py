"""Bounded, past-only empirical battery fitting and independent holdout gates.

The source interval contract deliberately matches energy_model: a row's AC
energies explain the SOC change from the preceding contiguous half-hour row.
Temperature is observational: outdoor fits remain labelled outdoor proxies.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite
from typing import Any, Callable

import numpy as np

from app.energy_plan.battery_response import BatterySocResponse

ValidationEvaluator = Callable[
    [BatterySocResponse, BatterySocResponse, list[dict[str, Any]]], dict[str, float]
]
_MEASURED_SOURCES = ("measured_battery", "observed_outdoor")


@dataclass(frozen=True)
class BatteryCalibrationResult:
    response: BatterySocResponse
    diagnostics: dict[str, Any]


def _time(value: Any, as_of: datetime) -> datetime | None:
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=as_of.tzinfo)
        if as_of.tzinfo is None:
            return dt.replace(tzinfo=None)
        return dt.astimezone(as_of.tzinfo)
    except (TypeError, ValueError, OverflowError):
        return None


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _intervals(rows: list[dict[str, Any]], as_of: datetime) -> list[dict[str, Any]]:
    cutoff = as_of.replace(hour=0, minute=0, second=0, microsecond=0)
    first = cutoff - timedelta(days=90)
    dated: list[tuple[datetime, dict[str, Any]]] = []
    for row in rows:
        dt = _time(row.get("dt"), as_of)
        receipts = [_time(row[key], as_of) for key in ("available_at", "ingested_at", "updated_at") if row.get(key) is not None]
        if dt is None or not first <= dt < cutoff or not receipts:
            continue
        if row.get("temperature_available_at") is not None:
            receipts.append(_time(row["temperature_available_at"], as_of))
        temperature_receipts = row.get("temperature_receipts")
        if temperature_receipts is not None:
            if not isinstance(temperature_receipts, dict):
                continue
            receipts.extend(_time(value, as_of) for value in temperature_receipts.values())
        if any(receipt is None or receipt > as_of for receipt in receipts):
            continue
        dated.append((dt, row))
    dated.sort(key=lambda item: item[0])
    intervals: list[dict[str, Any]] = []
    for (previous_dt, previous), (dt, current) in zip(dated, dated[1:]):
        if dt - previous_dt != timedelta(minutes=30):
            continue
        soc_start, soc_end = _number(previous.get("soc")), _number(current.get("soc"))
        charge = _number(current.get("charge", current.get("chg")))
        discharge = _number(current.get("discharge", current.get("dchg")))
        if soc_start is None or soc_end is None or charge is None or discharge is None:
            continue
        if not (5 <= soc_start <= 95 and 5 <= soc_end <= 95 and charge >= 0 and discharge >= 0):
            continue
        interval = dict(current)
        interval.update(dt=dt, soc_start=soc_start, soc=soc_end, charge=charge, discharge=discharge, duration_hours=0.5)
        intervals.append(interval)
    return intervals


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "intervals": len(rows),
        "charge_intervals": sum(float(row["charge"]) > 0.01 for row in rows),
        "discharge_intervals": sum(float(row["discharge"]) > 0.01 for row in rows),
        "dates": len({row["dt"].date() for row in rows}),
    }


def _temperature_configuration(rows: list[dict[str, Any]]) -> tuple[str | None, str | None, float, float, float, str]:
    for source in _MEASURED_SOURCES:
        for resolution in ("interval_observation", "daily_mean"):
            measured = [row for row in rows if row.get("temperature_source") == source and row.get("temperature_resolution", "interval_observation") == resolution and _number(row.get("temperature_c")) is not None]
            counts = _counts(measured)
            if counts["charge_intervals"] < 100 or counts["discharge_intervals"] < 100 or counts["dates"] < 15:
                continue
            temperatures = np.asarray([float(row["temperature_c"]) for row in measured])
            low, high = float(np.percentile(temperatures, 10)), float(np.percentile(temperatures, 90))
            if high - low < 8.0:
                continue
            by_hour: dict[int, list[float]] = {}
            for row in measured:
                by_hour.setdefault(row["dt"].hour, []).append(float(row["temperature_c"]))
            residual = [float(row["temperature_c"]) - float(np.mean(by_hour[row["dt"].hour])) for row in measured]
            if float(np.std(residual)) < 1.0:
                continue
            return source, resolution, float(np.mean(temperatures)), float(np.min(temperatures)), float(np.max(temperatures)), "enabled"
    return None, None, 25.0, 25.0, 25.0, "insufficient_observed_temperature_or_identifiability"


def _row_coefficients(model: dict[str, Any], row: dict[str, Any]) -> tuple[float, float]:
    temperature = _number(row.get("temperature_c"))
    reference = float(model["reference_temperature_c"])
    if row.get("temperature_source") != model["temperature_source"] or row.get("temperature_resolution", "interval_observation") != model.get("temperature_resolution", "interval_observation") or temperature is None:
        temperature = reference
    temperature = min(float(model["temperature_max_c"]), max(float(model["temperature_min_c"]), temperature))
    hour = row["dt"].hour
    charge = float(model["charge_intercept"]) + float(model["charge_temperature_slope"]) * (temperature - reference)
    discharge = float(model["discharge_intercept"]) + float(model["discharge_temperature_slope"]) * (temperature - reference)
    return charge + float(model["hourly_charge_adjustments"].get(str(hour), 0.0)), discharge + float(model["hourly_discharge_adjustments"].get(str(hour), 0.0))


def _project_model(model: dict[str, Any], temperatures: dict[int, float], forecast_source: str, daily_temperature_c: float | None = None) -> BatterySocResponse:
    if model.get("schema_version") != 1:
        raise ValueError("unsupported calibration model")
    numeric_keys = ("charge_intercept", "discharge_intercept", "charge_temperature_slope", "discharge_temperature_slope", "drift_soc_per_hour", "reference_temperature_c", "temperature_min_c", "temperature_max_c")
    if any(_number(model.get(key)) is None for key in numeric_keys):
        raise ValueError("nonfinite calibration model")
    if not float(model["temperature_min_c"]) <= float(model["reference_temperature_c"]) <= float(model["temperature_max_c"]):
        raise ValueError("invalid learned temperature range")
    if model.get("temperature_source") not in (None, *_MEASURED_SOURCES):
        raise ValueError("invalid temperature provenance")
    if model.get("temperature_resolution", "interval_observation") not in (None, "interval_observation", "daily_mean"):
        raise ValueError("invalid temperature resolution")
    for key in ("hourly_charge_adjustments", "hourly_discharge_adjustments"):
        if not isinstance(model.get(key), dict):
            raise ValueError("invalid hourly adjustments")
        if any(str(hour) not in {str(h) for h in range(24)} or _number(value) is None for hour, value in model[key].items()):
            raise ValueError("invalid hourly adjustment values")
    compatible = (model["temperature_source"] == "observed_outdoor" and forecast_source in ("forecast_outdoor", "observed_outdoor")) or (model["temperature_source"] == "measured_battery" and forecast_source in ("measured_battery", "battery_temperature_estimate"))
    charge_by_hour: dict[int, float] = {}
    discharge_by_hour: dict[int, float] = {}
    for hour in range(24):
        row = {
            "dt": datetime(2000, 1, 1, hour),
            "temperature_source": model["temperature_source"] if compatible else None,
            "temperature_c": daily_temperature_c if model.get("temperature_resolution") == "daily_mean" else temperatures.get(hour),
            "temperature_resolution": model.get("temperature_resolution", "interval_observation"),
        }
        charge_by_hour[hour], discharge_by_hour[hour] = _row_coefficients(model, row)
    return BatterySocResponse(
        float(model["charge_intercept"]), float(model["discharge_intercept"]), float(model["drift_soc_per_hour"]),
        charge_by_hour, discharge_by_hour,
    )


def _fit(rows: list[dict[str, Any]], *, allow_temperature: bool = True, allow_hourly: bool = True) -> dict[str, Any]:
    source, resolution, reference, minimum, maximum, temperature_reason = _temperature_configuration(rows) if allow_temperature else (None, None, 25.0, 25.0, 25.0, "training_only_constant_reference")
    features: list[list[float]] = []
    actual: list[float] = []
    for row in rows:
        charge, discharge = float(row["charge"]), float(row["discharge"])
        temperature = _number(row.get("temperature_c"))
        delta = temperature - reference if source is not None and temperature is not None and row.get("temperature_source") == source and row.get("temperature_resolution", "interval_observation") == resolution else 0.0
        features.append([charge, -discharge, charge * delta, -discharge * delta, -0.5])
        actual.append(float(row["soc"]) - float(row["soc_start"]))
    matrix = np.asarray(features, dtype=float)
    target = np.asarray(actual, dtype=float)
    active_columns = (0, 1, 2, 3, 4) if source is not None else (0, 1, 4)
    active = matrix[:, active_columns]
    norms = np.linalg.norm(active, axis=0)
    identifiable = bool(np.all(norms > 0)) and float(np.linalg.cond(active / np.maximum(norms, 1e-12))) <= 1e8
    if not identifiable:
        if source is not None:
            neutral = _fit(rows, allow_temperature=False, allow_hourly=allow_hourly)
            neutral["temperature_reason"] = "temperature_regressors_not_identifiable"
            return neutral
        raise ValueError("charge/discharge/drift regressors not identifiable")
    # Strictly positive ridge makes each finite quadratic solve unique. The
    # drift boundary is an exact two-case active-set solve, not clipping after
    # fitting. Remaining physically infeasible responses are rejected.
    ridge = 0.0001
    fitted = np.linalg.solve(matrix.T @ matrix + ridge * np.eye(5), matrix.T @ target)
    iterations = 1
    if fitted[4] < 0:
        reduced = matrix[:, :4]
        fitted[:4] = np.linalg.solve(reduced.T @ reduced + ridge * np.eye(4), reduced.T @ target)
        fitted[4] = 0.0
        iterations = 2
    if not bool(np.all(np.isfinite(fitted))):
        raise ValueError("nonfinite fitted coefficients")
    model: dict[str, Any] = {
        "schema_version": 1,
        "charge_intercept": float(fitted[0]), "discharge_intercept": float(fitted[1]),
        "charge_temperature_slope": float(fitted[2]), "discharge_temperature_slope": float(fitted[3]),
        "drift_soc_per_hour": float(fitted[4]), "reference_temperature_c": reference,
        "temperature_min_c": minimum, "temperature_max_c": maximum,
        "temperature_source": source, "temperature_reason": temperature_reason,
        "temperature_resolution": resolution,
        "hourly_charge_adjustments": {}, "hourly_discharge_adjustments": {},
        "solver": "positive_ridge_two_case_nonnegative_drift_reject_infeasible", "iterations": iterations, "iteration_limit": 200,
        "ridge": ridge,
        "maximum_normalized_condition_number": 1e8,
        "minimum_hourly_flow_intervals": 20, "hourly_shrinkage_prior_intervals": 20,
    }
    residuals = target - matrix @ fitted
    for hour in range(24):
        if not allow_hourly:
            break
        for flow, index, sign in (("charge", 0, 1.0), ("discharge", 1, -1.0)):
            samples = [(row, float(residuals[i])) for i, row in enumerate(rows) if row["dt"].hour == hour and float(row[flow]) > 0.01 and float(row["discharge" if index == 0 else "charge"]) <= 0.01]
            if len(samples) < 20:
                continue
            numerator = sum(float(row[flow]) * residual * sign for row, residual in samples)
            denominator = sum(float(row[flow]) ** 2 for row, _ in samples)
            model[f"hourly_{flow}_adjustments"][str(hour)] = len(samples) / (len(samples) + 20.0) * numerator / denominator
    for temperature in (minimum, maximum):
        _project_model(model, dict.fromkeys(range(24), temperature), "observed_outdoor" if source == "observed_outdoor" else "measured_battery", temperature)
    return model


def calibrate_battery_response(
    *, rows: list[dict[str, Any]], baseline: BatterySocResponse, as_of: datetime,
    forecast_temperatures: dict[int, float], previous_payload: dict[str, Any] | None = None,
    forecast_temperature_source: str = "forecast_outdoor",
    validation_evaluator: ValidationEvaluator | None = None,
    forecast_daily_temperature_c: float | None = None,
) -> BatteryCalibrationResult:
    """Adopt only a chronological SOC improvement with non-worse buy/fee MAE.

    The evaluator must independently replay held-out actual PV/load and the same
    tariff for each response. Augmented per-row coefficients use historical
    observed temperatures, rather than tomorrow's forecast temperatures.
    """
    selected = baseline
    selected_model: dict[str, Any] | None = None
    retained_previous_response = False
    diagnostics: dict[str, Any] = {
        "schema_version": 1, "adopted": False, "as_of": as_of.isoformat(),
        "forecast_temperature_source": forecast_temperature_source,
        "interval_contract": "current_row_ac_energy_explains_previous_to_current_soc_30min",
        "training_window_days": 90, "minimum_charge_intervals": 100,
        "minimum_discharge_intervals": 100, "minimum_dates": 15,
        "temperature_minimum_p90_minus_p10_c": 8.0,
        "comparison_baseline_provenance": "caller_supplied_operational_baseline_not_refitted",
        "candidate_ridge_prior": "zero_independent_of_holdout_and_operational_baseline",
        "availability_rule": "at_least_one_receipt_and_all_available_ingested_updated_timestamps_at_or_before_as_of",
    }
    if previous_payload:
        prior = previous_payload.get("diagnostics", previous_payload)
        try:
            if not isinstance(prior, dict):
                raise ValueError("invalid previous diagnostics")
            prior_as_of = _time(prior.get("as_of"), as_of)
            if prior_as_of is None or prior_as_of > as_of:
                raise ValueError("previous calibration must precede forecast")
            if isinstance(prior.get("model"), dict):
                selected_model = prior["model"]
                selected = _project_model(selected_model, forecast_temperatures, forecast_temperature_source, forecast_daily_temperature_c)
            elif (prior.get("adopted") is True or prior.get("retained_previous_response") is True) and isinstance(prior.get("response"), dict):
                selected = BatterySocResponse.from_payload(prior["response"])
                retained_previous_response = True
        except (KeyError, TypeError, ValueError, OverflowError):
            selected_model = None
            selected = baseline
            diagnostics["previous_model_rejected"] = "invalid_previous_payload"

    def result(reason: str) -> BatteryCalibrationResult:
        diagnostics.update(reason=reason, model=selected_model, response=selected.to_payload())
        diagnostics["retained_previous_model"] = selected_model is not None and not diagnostics["adopted"]
        diagnostics["retained_previous_response"] = retained_previous_response and not diagnostics["adopted"]
        return BatteryCalibrationResult(selected, diagnostics)

    intervals = _intervals(rows, as_of)
    canonical = [{key: row.get(key) for key in ("dt", "soc_start", "soc", "charge", "discharge", "temperature_c", "temperature_source", "temperature_resolution", "temperature_available_at", "temperature_receipts", "available_at", "ingested_at", "updated_at")} for row in intervals]
    diagnostics["input_sha256"] = hashlib.sha256(json.dumps(canonical, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    days = sorted({row["dt"].date() for row in intervals})
    if len(days) < 19:
        return result("insufficient_prior_dates")
    split_day = days[int(len(days) * 0.8)]
    training = [row for row in intervals if row["dt"].date() < split_day]
    holdout = [row for row in intervals if row["dt"].date() >= split_day]
    counts = _counts(training)
    diagnostics.update(training_counts=counts, holdout_counts=_counts(holdout), holdout_start=split_day.isoformat())
    training_canonical = [row for row in canonical if isinstance(row["dt"], datetime) and row["dt"].date() < split_day]
    diagnostics["training_sha256"] = hashlib.sha256(json.dumps(training_canonical, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()
    if counts["dates"] < 15 or counts["charge_intervals"] < 100 or counts["discharge_intervals"] < 100:
        return result("insufficient_training_intervals")
    try:
        model = _fit(training)
        candidate = _project_model(model, forecast_temperatures, forecast_temperature_source, forecast_daily_temperature_c)
        reference_model = _fit(training, allow_temperature=False, allow_hourly=False)
        reference_response = _project_model(reference_model, {}, forecast_temperature_source)
    except (ValueError, np.linalg.LinAlgError, OverflowError):
        return result("infeasible_or_nonfinite_fit")
    diagnostics.update(candidate_model=model, candidate_response=candidate.to_payload(),
                       chronological_reference_model=reference_model, chronological_reference_response=reference_response.to_payload())
    candidate_errors: list[float] = []
    baseline_errors: list[float] = []
    reference_errors: list[float] = []
    for row in holdout:
        charge, discharge = _row_coefficients(model, row)
        if selected_model is not None:
            previous_charge, previous_discharge = _row_coefficients(selected_model, row)
        else:
            previous_charge, previous_discharge = selected.charge_coefficient(row["dt"].hour), selected.discharge_coefficient(row["dt"].hour)
        row.update(candidate_charge_soc_per_kwh=charge, candidate_discharge_soc_per_kwh=discharge,
                   baseline_charge_soc_per_kwh=reference_response.charge_coefficient(row["dt"].hour),
                   baseline_discharge_soc_per_kwh=reference_response.discharge_coefficient(row["dt"].hour),
                   runtime_charge_soc_per_kwh=previous_charge, runtime_discharge_soc_per_kwh=previous_discharge)
        delta = float(row["soc"]) - float(row["soc_start"])
        predicted = charge * float(row["charge"]) - discharge * float(row["discharge"]) - candidate.drift_soc_per_hour * 0.5
        previous = previous_charge * float(row["charge"]) - previous_discharge * float(row["discharge"]) - selected.drift_soc_per_hour * 0.5
        candidate_errors.append(abs(delta - predicted))
        baseline_errors.append(abs(delta - previous))
        reference_prediction = reference_response.charge_coefficient(row["dt"].hour) * float(row["charge"]) - reference_response.discharge_coefficient(row["dt"].hour) * float(row["discharge"]) - reference_response.drift_soc_per_hour * 0.5
        reference_errors.append(abs(delta - reference_prediction))
    candidate_mae, baseline_mae = float(np.mean(candidate_errors)), float(np.mean(baseline_errors))
    reference_mae = float(np.mean(reference_errors))
    diagnostics.update(candidate_soc_mae=candidate_mae, baseline_soc_mae=baseline_mae, chronological_reference_soc_mae=reference_mae)
    if not (isfinite(candidate_mae) and isfinite(baseline_mae) and isfinite(reference_mae) and candidate_mae < reference_mae):
        return result("soc_holdout_not_strictly_improved")
    if candidate_mae > baseline_mae:
        return result("runtime_baseline_soc_holdout_worsened")
    if validation_evaluator is None:
        return result("financial_validation_missing")
    try:
        metrics = validation_evaluator(candidate, reference_response, holdout)
        keys = ("candidate_buy_mae", "baseline_buy_mae", "candidate_fee_mae", "baseline_fee_mae")
        validated = {key: float(metrics[key]) for key in keys}
        if not all(isfinite(value) and value >= 0 for value in validated.values()):
            return result("invalid_financial_validation")
    except (KeyError, TypeError, ValueError, ArithmeticError, RuntimeError, OSError):
        return result("financial_validation_unavailable")
    diagnostics["financial_validation"] = validated
    if validated["candidate_buy_mae"] > validated["baseline_buy_mae"] or validated["candidate_fee_mae"] > validated["baseline_fee_mae"]:
        return result("financial_holdout_worsened")
    runtime_rows = [dict(row, baseline_charge_soc_per_kwh=row["runtime_charge_soc_per_kwh"], baseline_discharge_soc_per_kwh=row["runtime_discharge_soc_per_kwh"]) for row in holdout]
    try:
        runtime_metrics = validation_evaluator(candidate, selected, runtime_rows)
        runtime_validated = {key: float(runtime_metrics[key]) for key in keys}
        if not all(isfinite(value) and value >= 0 for value in runtime_validated.values()):
            return result("invalid_runtime_financial_validation")
    except (KeyError, TypeError, ValueError, ArithmeticError, RuntimeError, OSError):
        return result("runtime_financial_validation_unavailable")
    diagnostics["runtime_baseline_financial_validation"] = runtime_validated
    if runtime_validated["candidate_buy_mae"] > runtime_validated["baseline_buy_mae"] or runtime_validated["candidate_fee_mae"] > runtime_validated["baseline_fee_mae"]:
        return result("runtime_financial_holdout_worsened")
    selected, selected_model = candidate, model
    diagnostics["adopted"] = True
    return result("adopted" if model["temperature_source"] is not None else "adopted_without_temperature")
