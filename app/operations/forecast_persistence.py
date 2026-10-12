"""Forecast-only Firestore persistence for the dedicated daily forecast job."""
from __future__ import annotations

import json
import math
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.cloud.firestore import transactional

from app.backup.night_plan_archive import read_plan_file
from app.energy_plan.soc_cost import simulate_daytime_soc_percent
from app.operations.domain import (
    extract_final_pv_source_from_plan,
    extract_final_pv_totals_from_plan,
    extract_hourly_forecast_from_plan,
)
from app.operations.forecast_snapshot import build_forecast_snapshot_rows, persist_forecast_snapshots
from app.parsing.numbers import to_float, to_int
from app.separated_runtime.plan import PublishedPlan


def _complete_hourly_rows(rows: list[dict[str, Any]], *, target_date: str) -> bool:
    # HISTORICAL_FAILURE_LOCK: validate values before replacing any saved vintage.
    if any(
        (value := to_float(row.get(field))) is None or not math.isfinite(value) or value < 0.0
        for row in rows for field in ("forecast_pv_kwh", "forecast_load_kwh")
    ):
        return False
    return len(rows) == 24 and {row.get("date") for row in rows} == {target_date} and {
        row.get("hour") for row in rows
    } == set(range(24))


def persist_forecast_only_plan(
    client: Any,
    *,
    plan_path: Path,
    target_date: str,
    timezone_name: str,
    recorded_at: str | None = None,
) -> int:
    """Persist a validated forecast without modifying control-plan documents."""
    data = read_plan_file(plan_path)
    now = recorded_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    writes = _forecast_projection_values(data, target_date=target_date, timezone_name=timezone_name, now=now)
    snapshot_count = persist_forecast_snapshots(
        client, backend="firestore", night_plan_path=plan_path, timezone=timezone_name, ingested_at=now,
    )
    if callable(getattr(client, "transaction", None)):
        raw = plan_path.read_bytes()
        if json.loads(raw) != data:
            raise ValueError("legacy forecast original changed during persistence")
        incoming = {"target_date": target_date, "generated_at": str(data.get("generated_at") or now),
                    "control_version_id": "", "raw_plan_sha256": hashlib.sha256(raw).hexdigest()}
        current_ref = client.collection("forecast_plans").document(target_date)

        @transactional
        def update(transaction: Any) -> None:
            previous = current_ref.get(transaction=transaction, timeout=3, retry=None).to_dict() or {}
            if previous.get("raw_plan_sha256") == incoming["raw_plan_sha256"]:
                return
            if not projection_may_advance(previous, incoming):
                return
            for collection, key, payload in writes:
                # A standalone forecast is not adopted control evidence. An
                # existing adopted identity must not survive a different run.
                transaction.set(client.collection(collection).document(key), {**payload, **incoming}, merge=True)

        update(client.transaction(max_attempts=3))
        return snapshot_count
    existing = list(client.collection("forecast_hourly").where("date", "==", target_date).stream())
    batch = client.batch()
    for document in existing:
        batch.delete(document.reference)
    for collection, key, payload in writes:
        batch.set(client.collection(collection).document(key), payload, merge=True)
    batch.commit()
    return snapshot_count


def _forecast_projection_values(
    data: dict[str, Any], *, target_date: str, timezone_name: str, now: str,
) -> list[tuple[str, str, dict[str, Any]]]:
    """Build the legacy projection values from a single already-fixed original."""
    forecast_value = data.get("forecast")
    forecast: dict[str, Any] = forecast_value if isinstance(forecast_value, dict) else {}
    forecast_date = str(forecast.get("date") or "").strip()
    hourly_rows = extract_hourly_forecast_from_plan(data)
    if forecast_date != target_date:
        raise ValueError("forecast target date does not match the intended date")
    if not _complete_hourly_rows(hourly_rows, target_date=target_date):
        raise ValueError("forecast hourly rows must contain exactly hours 0 through 23")

    result_value = data.get("result")
    plan_result: dict[str, Any] = result_value if isinstance(result_value, dict) else {}
    planned_target_soc_percent = to_float(plan_result.get("target_soc_7_percent"))
    planned_night_charge_kwh = to_float(plan_result.get("required_night_charge_kwh"))
    planned_capacity_kwh = to_float(plan_result.get("effective_capacity_kwh"))
    planned_hourly_soc_percent: dict[str, float] = {}
    if (planned_target_soc_percent is not None and 0.0 <= planned_target_soc_percent <= 100.0
            and planned_capacity_kwh is not None and math.isfinite(planned_capacity_kwh)
            and planned_capacity_kwh > 0.0):
        planned_hourly_soc_percent = {
            str(hour): soc for hour, soc in simulate_daytime_soc_percent(
                target_soc_percent=planned_target_soc_percent, capacity_kwh=planned_capacity_kwh,
                hourly_load_kwh={int(row["hour"]): float(row["forecast_load_kwh"]) for row in hourly_rows},
                hourly_pv_kwh={int(row["hour"]): float(row["forecast_pv_kwh"]) for row in hourly_rows},
            ).items()
        }

    snapshot_rows = build_forecast_snapshot_rows(data, ingested_at=now, timezone=timezone_name)
    if not _complete_hourly_rows(snapshot_rows, target_date=target_date):
        raise ValueError("forecast snapshot rows must contain exactly hours 0 through 23")
    forecast_run_id = str(snapshot_rows[0].get("forecast_run_id") or "").strip()
    forecast_issued_at = str(snapshot_rows[0].get("issued_at") or "").strip()
    if not forecast_run_id:
        raise ValueError("forecast snapshot rows must include a forecast_run_id")

    writes: list[tuple[str, str, dict[str, Any]]] = []
    for row in hourly_rows:
        writes.append((
            "forecast_hourly", f"{target_date}-{int(row['hour']):02d}",
            {
                **row,
                "source": "forecast-only-hourly",
                "forecast_run_id": forecast_run_id,
                "forecast_issued_at": forecast_issued_at or None,
                "updated_at": now,
            },
        ))

    pv_totals = extract_final_pv_totals_from_plan(data)
    pv_value = data.get("pv_array_forecast")
    pv_forecast: dict[str, Any] = pv_value if isinstance(pv_value, dict) else {}
    calibration_value = pv_forecast.get("calibration")
    calibration: dict[str, Any] = calibration_value if isinstance(calibration_value, dict) else {}
    writes.append((
        "sunshine_daily", target_date,
        {
            "date": target_date,
            "forecast_hours": to_float(forecast.get("sun_hours")),
            "forecast_temp_c": to_float(forecast.get("temp_c")),
            "forecast_weather_code": to_int(forecast.get("weather_code")),
            "forecast_precipitation_sum_mm": to_float(forecast.get("precipitation_sum_mm")),
            "forecast_precipitation_probability_mean": to_float(forecast.get("precipitation_probability_mean")),
            "forecast_shortwave_radiation_sum_mj_m2": to_float(forecast.get("shortwave_radiation_sum_mj_m2")),
            "forecast_pv_total_kwh": to_float(pv_totals.get("total_kwh")),
            "forecast_pv_morning_kwh": to_float(pv_totals.get("morning_kwh")),
            "forecast_pv_midday_kwh": to_float(pv_totals.get("midday_kwh")),
            "forecast_pv_evening_kwh": to_float(pv_totals.get("evening_kwh")),
            "forecast_pv_calibration_factor": to_float(calibration.get("effective_factor") or calibration.get("factor")),
            "source": extract_final_pv_source_from_plan(data),
            "updated_at": now,
        },
    ))
    writes.append((
        "forecast_plans", target_date,
        {
            "date": target_date,
            "hourly_row_count": len(hourly_rows),
            "forecast_pv_total_kwh": to_float(pv_totals.get("total_kwh")),
            "forecast_source": extract_final_pv_source_from_plan(data),
            "forecast_run_id": forecast_run_id,
            "forecast_issued_at": forecast_issued_at or None,
            "planned_target_soc_percent": planned_target_soc_percent,
            "planned_night_charge_kwh": planned_night_charge_kwh,
            "planned_capacity_kwh": planned_capacity_kwh,
            "planned_hourly_soc_percent": planned_hourly_soc_percent,
            "forecast_json": json.dumps(forecast, ensure_ascii=False, separators=(",", ":")),
            "updated_at": now,
        },
    ))
    return writes


def projection_identity(raw: bytes, plan: PublishedPlan) -> dict[str, str]:
    if raw != plan.raw_json.encode("utf-8") or hashlib.sha256(raw).hexdigest() != plan.sha256:
        raise ValueError("projection original checksum mismatch")
    data = json.loads(raw)
    if data["forecast"]["date"] != plan.target_date:
        raise ValueError("projection target date mismatch")
    stamp = str(data["generated_at"])
    issued = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if issued.utcoffset() is None:
        raise ValueError("projection generation requires timezone")
    return {"target_date": plan.target_date, "generated_at": stamp,
            "control_version_id": plan.version_id, "raw_plan_sha256": plan.sha256}


def projection_may_advance(previous: dict[str, Any], incoming: dict[str, str]) -> bool:
    """Compare the projection's own generation inside its transaction."""
    if not previous:
        return True
    previous_date = str(previous.get("target_date") or previous.get("date") or "")
    previous_stamp = previous.get("generated_at") or previous.get("forecast_issued_at")
    if not previous_date or not previous_stamp:
        # Legacy parameter documents have no plan generation. Their first
        # approved projection adds identity without interpreting updated_at.
        return True
    old_time = datetime.fromisoformat(str(previous_stamp).replace("Z", "+00:00"))
    new_time = datetime.fromisoformat(incoming["generated_at"].replace("Z", "+00:00"))
    if old_time.utcoffset() is None:
        raise ValueError("stored projection generation requires timezone")
    old_order, new_order = (previous_date, old_time), (incoming["target_date"], new_time)
    if old_order > new_order:
        raise ValueError("older projection cannot replace current generation")
    if old_order == new_order:
        if previous.get("control_version_id") == incoming["control_version_id"] and previous.get("raw_plan_sha256") == incoming["raw_plan_sha256"]:
            return False
        if (not previous.get("control_version_id") and incoming["control_version_id"]
                and previous.get("raw_plan_sha256") == incoming["raw_plan_sha256"]):
            # Bind an already persisted standalone original after adoption;
            # the values and original generation remain exactly the same.
            return True
        if "control_version_id" in previous:
            raise ValueError("conflicting projection generation")
        # A legacy same-time projection may only be bound after comparing its
        # existing forecast run identity in the caller.
    return True


def project_forecast_plan(
    client: Any, *, raw: bytes, plan: PublishedPlan, private_plan_path: Path,
    timezone_name: str, recorded_at: str | None = None,
) -> int:
    identity = projection_identity(raw, plan)
    if not private_plan_path.parent.name.startswith("forecast-plan-"):
        raise ValueError("projection requires a run-private plan path")
    if private_plan_path.read_bytes() != raw:
        raise ValueError("projection private original checksum mismatch")
    now = recorded_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    writes = _forecast_projection_values(json.loads(raw), target_date=plan.target_date, timezone_name=timezone_name, now=now)
    snapshot_count = persist_forecast_snapshots(
        client, backend="firestore", night_plan_path=private_plan_path, timezone=timezone_name, ingested_at=now,
    )
    if private_plan_path.read_bytes() != raw:
        raise ValueError("projection private original changed during snapshot persistence")
    current_ref = client.collection("forecast_plans").document(plan.target_date)
    forecast_value = next(payload for collection, _, payload in writes if collection == "forecast_plans")

    @transactional
    def update(transaction: Any) -> None:
        previous = current_ref.get(transaction=transaction, timeout=3, retry=None).to_dict() or {}
        if not projection_may_advance(previous, identity):
            return
        if (not previous.get("control_version_id") and previous.get("forecast_issued_at")
                and datetime.fromisoformat(str(previous["forecast_issued_at"]).replace("Z", "+00:00")) == datetime.fromisoformat(identity["generated_at"].replace("Z", "+00:00"))
                and previous.get("forecast_run_id") and previous["forecast_run_id"] != forecast_value["forecast_run_id"]):
            raise ValueError("legacy projection forecast generation conflict")
        for collection, key, payload in writes:
            transaction.set(client.collection(collection).document(key), {**payload, **identity}, merge=True)

    update(client.transaction(max_attempts=3))
    return snapshot_count
