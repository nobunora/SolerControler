from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.operations.domain import extract_final_pv_source_from_plan, extract_hourly_forecast_from_plan
from app.parsing.numbers import to_float, to_int

_SNAPSHOT_COLUMNS = (
    "snapshot_id",
    "forecast_run_id",
    "issued_at",
    "issued_at_source",
    "target_at",
    "lead_minutes",
    "date",
    "hour",
    "forecast_pv_kwh",
    "forecast_load_kwh",
    "forecast_charge_kwh",
    "forecast_weather_code",
    "forecast_precipitation_mm",
    "forecast_precipitation_probability",
    "forecast_cloud_cover",
    "forecast_shortwave_radiation_w_m2",
    "forecast_temp_c",
    "forecast_relative_humidity_percent",
    "forecast_dew_point_c",
    "forecast_wind_speed_10m",
    "forecast_provider",
    "pv_input_source",
    "pv_provider",
    "physical_pv_kwh",
    "pv_forecast_detail_json",
    "pv_model_source",
    "pv_model_version",
    "weather_model_version",
    "forecast_pv_calibration_factor",
    "quality_flags_json",
    "source",
    "recorded_at",
)
_JSON_COLUMNS = {"quality_flags_json", "pv_forecast_detail_json"}


def _dict_section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value: Any = data.get(name)
    return dict(value) if isinstance(value, dict) else {}


def _parse_timestamp(value: str, *, timezone: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone))
    return parsed


def _issued_at_from_plan(data: dict[str, Any], *, ingested_at: str) -> tuple[str, str]:
    forecast = _dict_section(data, "forecast")
    candidates = (
        data.get("issued_at"),
        data.get("generated_at"),
        data.get("created_at"),
        forecast.get("issued_at"),
        forecast.get("generated_at"),
    )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip(), "plan"
    return ingested_at, "ingested_at_fallback"


def _model_version(data: dict[str, Any], section_name: str) -> str | None:
    section = _dict_section(data, section_name)
    for key in ("model_version", "version", "model"):
        value = section.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _hourly_row_for_hour(
    section: dict[str, Any],
    *,
    target_date: str,
    hour: int,
) -> dict[str, Any] | None:
    rows = section.get("hourly")
    if not isinstance(rows, list):
        return None
    for raw_row in rows:
        if not isinstance(raw_row, dict):
            continue
        row = dict(raw_row)
        explicit_hour = to_int(row.get("hour"))
        if explicit_hour == hour:
            return row
        raw_time = row.get("time")
        if not isinstance(raw_time, str) or not raw_time.strip():
            continue
        try:
            timestamp = datetime.fromisoformat(raw_time.strip().replace("Z", "+00:00"))
        except ValueError:
            continue
        if timestamp.date().isoformat() == target_date and timestamp.hour == hour:
            return row
    return None


def _pv_forecast_evidence(
    pv_forecast: dict[str, Any],
    *,
    target_date: str,
    hour: int,
) -> dict[str, Any]:
    selected = _hourly_row_for_hour(pv_forecast, target_date=target_date, hour=hour)
    providers: dict[str, Any] = {}
    provider_forecasts = pv_forecast.get("provider_forecasts")
    if isinstance(provider_forecasts, dict):
        for raw_name, raw_forecast in provider_forecasts.items():
            if not isinstance(raw_forecast, dict):
                continue
            provider_row = _hourly_row_for_hour(
                dict(raw_forecast),
                target_date=target_date,
                hour=hour,
            )
            if provider_row is not None:
                providers[str(raw_name)] = provider_row
    evidence: dict[str, Any] = {}
    if selected is not None:
        evidence["selected"] = selected
    if providers:
        evidence["providers"] = providers
    return evidence


def _quality_flags(
    row: dict[str, Any],
    *,
    issued_at_source: str,
    lead_minutes: int,
    pv_model_source: str,
    physical_pv_kwh: float | None,
) -> list[str]:
    flags: list[str] = []
    if issued_at_source != "plan":
        flags.append("issued_at_ingested_fallback")
    if lead_minutes < 0:
        flags.append("issued_after_target")
    pv = to_float(row.get("forecast_pv_kwh")) or 0.0
    shortwave = to_float(row.get("forecast_shortwave_radiation_w_m2"))
    if shortwave is None:
        flags.append("missing_shortwave")
    elif pv > 0.0 and shortwave <= 0.0:
        flags.append("nonpositive_shortwave_with_positive_pv")
    if row.get("forecast_cloud_cover") is None:
        flags.append("missing_cloud_cover")
    if row.get("forecast_weather_code") is None:
        flags.append("missing_weather_code")
    if "physical" in pv_model_source.lower() and physical_pv_kwh is None:
        flags.append("missing_physical_pv_detail")
    return flags


def _forecast_run_id(
    *,
    data: dict[str, Any],
    hourly_rows: list[dict[str, Any]],
    issued_at: str,
    forecast_source: str,
    source_run_key: str | None,
) -> str:
    if source_run_key:
        identity = {"pipeline_run_key": source_run_key}
    else:
        identity = {
            "issued_at": issued_at,
            "forecast_date": hourly_rows[0]["date"],
            "source": forecast_source,
            "rows": hourly_rows,
            "plan_quality": data.get("plan_quality"),
        }
    return hashlib.sha256(
        json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()[:24]


def build_forecast_snapshot_rows(
    data: dict[str, Any],
    *,
    ingested_at: str,
    timezone: str,
    source_run_key: str | None = None,
) -> list[dict[str, Any]]:
    """Build immutable forecast evidence rows without changing the latest-value contract."""
    hourly_rows = extract_hourly_forecast_from_plan(data)
    if not hourly_rows:
        return []
    issued_at, issued_at_source = _issued_at_from_plan(data, ingested_at=ingested_at)
    try:
        local_zone = ZoneInfo(timezone)
        issued_dt = _parse_timestamp(issued_at, timezone=timezone)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        issued_at = ingested_at
        issued_at_source = "ingested_at_fallback"
        local_zone = ZoneInfo("UTC")
        issued_dt = _parse_timestamp(ingested_at, timezone="UTC")
    issued_local = issued_dt.astimezone(local_zone)
    forecast_source = extract_final_pv_source_from_plan(data)
    forecast = _dict_section(data, "forecast")
    pv_forecast = _dict_section(data, "pv_array_forecast")
    weather_provider = str(forecast.get("source") or "").strip() or None
    pv_input_source = str(pv_forecast.get("source") or "").strip() or None
    pv_provider = str(pv_forecast.get("provider") or "").strip() or None
    pv_model_version = _model_version(data, "pv_array_forecast")
    weather_model_version = _model_version(data, "forecast")
    calibration = pv_forecast.get("calibration")
    calibration_factor = None
    if isinstance(calibration, dict):
        calibration_factor = to_float(calibration.get("effective_factor"))
        if calibration_factor is None:
            calibration_factor = to_float(calibration.get("factor"))

    forecast_run_id = _forecast_run_id(
        data=data,
        hourly_rows=hourly_rows,
        issued_at=issued_at,
        forecast_source=forecast_source,
        source_run_key=source_run_key,
    )
    result: list[dict[str, Any]] = []
    for row in hourly_rows:
        target_date = str(row["date"])
        hour = int(row["hour"])
        target_local = datetime.fromisoformat(f"{target_date}T{hour:02d}:00:00").replace(tzinfo=local_zone)
        lead_minutes = int(round((target_local - issued_local).total_seconds() / 60.0))
        pv_evidence = _pv_forecast_evidence(
            pv_forecast,
            target_date=target_date,
            hour=hour,
        )
        selected_pv = pv_evidence.get("selected")
        physical_pv_kwh = (
            to_float(selected_pv.get("total_kwh"))
            if isinstance(selected_pv, dict)
            else None
        )
        flags = _quality_flags(
            row,
            issued_at_source=issued_at_source,
            lead_minutes=lead_minutes,
            pv_model_source=forecast_source,
            physical_pv_kwh=physical_pv_kwh,
        )
        result.append(
            {
                "snapshot_id": f"{forecast_run_id}-{target_date}-{hour:02d}",
                "forecast_run_id": forecast_run_id,
                "issued_at": issued_at,
                "issued_at_source": issued_at_source,
                "target_at": target_local.isoformat(),
                "lead_minutes": lead_minutes,
                **row,
                "forecast_provider": weather_provider,
                "pv_input_source": pv_input_source,
                "pv_provider": pv_provider,
                "physical_pv_kwh": physical_pv_kwh,
                "pv_forecast_detail_json": json.dumps(
                    pv_evidence,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                ),
                "pv_model_source": forecast_source,
                "pv_model_version": pv_model_version,
                "weather_model_version": weather_model_version,
                "forecast_pv_calibration_factor": calibration_factor,
                "quality_flags_json": json.dumps(flags, ensure_ascii=False, separators=(",", ":")),
                "source": "night-charge-plan-hourly-snapshot",
                "recorded_at": ingested_at,
            }
        )
    return result


def _ensure_sqlite_schema(conn: Any) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS forecast_hourly_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            forecast_run_id TEXT NOT NULL,
            issued_at TEXT NOT NULL,
            issued_at_source TEXT NOT NULL,
            target_at TEXT NOT NULL,
            lead_minutes INTEGER NOT NULL,
            date TEXT NOT NULL,
            hour INTEGER NOT NULL,
            forecast_pv_kwh REAL,
            forecast_load_kwh REAL,
            forecast_charge_kwh REAL,
            forecast_weather_code INTEGER,
            forecast_precipitation_mm REAL,
            forecast_precipitation_probability REAL,
            forecast_cloud_cover REAL,
            forecast_shortwave_radiation_w_m2 REAL,
            forecast_temp_c REAL,
            forecast_relative_humidity_percent REAL,
            forecast_dew_point_c REAL,
            forecast_wind_speed_10m REAL,
            forecast_provider TEXT,
            pv_input_source TEXT,
            pv_provider TEXT,
            physical_pv_kwh REAL,
            pv_forecast_detail_json TEXT NOT NULL DEFAULT '{}',
            pv_model_source TEXT,
            pv_model_version TEXT,
            weather_model_version TEXT,
            forecast_pv_calibration_factor REAL,
            quality_flags_json TEXT NOT NULL,
            source TEXT NOT NULL,
            recorded_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_forecast_hourly_snapshots_target_issue
            ON forecast_hourly_snapshots(date, hour, issued_at);
        """
    )
    existing = {str(row[1]) for row in conn.execute("PRAGMA table_info(forecast_hourly_snapshots)").fetchall()}
    additions = {
        "pv_provider": "pv_provider TEXT",
        "physical_pv_kwh": "physical_pv_kwh REAL",
        "pv_forecast_detail_json": "pv_forecast_detail_json TEXT NOT NULL DEFAULT '{}'",
    }
    for column, definition in additions.items():
        if column not in existing:
            conn.execute(f"ALTER TABLE forecast_hourly_snapshots ADD COLUMN {definition}")


def _persist_sqlite(conn: Any, rows: list[dict[str, Any]]) -> int:
    _ensure_sqlite_schema(conn)
    before = conn.total_changes
    placeholders = ", ".join(f":{column}" for column in _SNAPSHOT_COLUMNS)
    conn.executemany(
        f"INSERT OR IGNORE INTO forecast_hourly_snapshots ({', '.join(_SNAPSHOT_COLUMNS)}) VALUES ({placeholders})",
        rows,
    )
    conn.commit()
    return int(conn.total_changes - before)


def _ensure_postgres_schema(conn: Any) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS forecast_hourly_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                forecast_run_id TEXT NOT NULL,
                issued_at TEXT NOT NULL,
                issued_at_source TEXT NOT NULL,
                target_at TEXT NOT NULL,
                lead_minutes INTEGER NOT NULL,
                date TEXT NOT NULL,
                hour INTEGER NOT NULL,
                forecast_pv_kwh DOUBLE PRECISION,
                forecast_load_kwh DOUBLE PRECISION,
                forecast_charge_kwh DOUBLE PRECISION,
                forecast_weather_code INTEGER,
                forecast_precipitation_mm DOUBLE PRECISION,
                forecast_precipitation_probability DOUBLE PRECISION,
                forecast_cloud_cover DOUBLE PRECISION,
                forecast_shortwave_radiation_w_m2 DOUBLE PRECISION,
                forecast_temp_c DOUBLE PRECISION,
                forecast_relative_humidity_percent DOUBLE PRECISION,
                forecast_dew_point_c DOUBLE PRECISION,
                forecast_wind_speed_10m DOUBLE PRECISION,
                forecast_provider TEXT,
                pv_input_source TEXT,
                pv_provider TEXT,
                physical_pv_kwh DOUBLE PRECISION,
                pv_forecast_detail_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                pv_model_source TEXT,
                pv_model_version TEXT,
                weather_model_version TEXT,
                forecast_pv_calibration_factor DOUBLE PRECISION,
                quality_flags_json JSONB NOT NULL,
                source TEXT NOT NULL,
                recorded_at TEXT NOT NULL
            )
            """
        )
        cur.execute("ALTER TABLE forecast_hourly_snapshots ADD COLUMN IF NOT EXISTS pv_provider TEXT")
        cur.execute("ALTER TABLE forecast_hourly_snapshots ADD COLUMN IF NOT EXISTS physical_pv_kwh DOUBLE PRECISION")
        cur.execute(
            "ALTER TABLE forecast_hourly_snapshots ADD COLUMN IF NOT EXISTS "
            "pv_forecast_detail_json JSONB NOT NULL DEFAULT '{}'::jsonb"
        )
        cur.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_forecast_hourly_snapshots_target_issue
            ON forecast_hourly_snapshots(date, hour, issued_at)
            """
        )


def _persist_postgres(conn: Any, rows: list[dict[str, Any]]) -> int:
    _ensure_postgres_schema(conn)
    placeholders = ", ".join(
        f"%({column})s::jsonb" if column in _JSON_COLUMNS else f"%({column})s"
        for column in _SNAPSHOT_COLUMNS
    )
    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO forecast_hourly_snapshots ({', '.join(_SNAPSHOT_COLUMNS)}) VALUES ({placeholders}) "
            "ON CONFLICT(snapshot_id) DO NOTHING",
            rows,
        )
        inserted = max(0, int(cur.rowcount or 0))
    conn.commit()
    return inserted


def _persist_firestore(client: Any, rows: list[dict[str, Any]]) -> int:
    collection = client.collection("forecast_hourly_snapshots")
    pending: list[tuple[Any, dict[str, Any]]] = []
    for row in rows:
        document = collection.document(str(row["snapshot_id"]))
        try:
            existing = document.get()
        except (AttributeError, TypeError):
            existing = None
        if existing is not None and bool(getattr(existing, "exists", False)):
            continue
        payload = dict(row)
        payload["quality_flags"] = json.loads(str(payload.pop("quality_flags_json")))
        payload["pv_forecast_detail"] = json.loads(str(payload.pop("pv_forecast_detail_json")))
        pending.append((document, payload))
    if not pending:
        return 0
    batch = client.batch()
    for document, payload in pending:
        create = getattr(batch, "create", None)
        if callable(create):
            create(document, payload)
        else:
            batch.set(document, payload, merge=False)
    batch.commit()
    return len(pending)


def persist_forecast_snapshots(
    storage: Any,
    *,
    backend: str,
    night_plan_path: Path,
    timezone: str,
    ingested_at: str,
    source_run_key: str | None = None,
) -> int:
    """Persist one forecast vintage without modifying the latest forecast store."""
    if not night_plan_path.exists():
        return 0
    raw = json.loads(night_plan_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return 0
    rows = build_forecast_snapshot_rows(
        dict(raw),
        ingested_at=ingested_at,
        timezone=timezone,
        source_run_key=source_run_key,
    )
    if not rows:
        return 0
    normalized = backend.strip().lower()
    if normalized == "sqlite":
        return _persist_sqlite(storage, rows)
    if normalized == "postgres":
        return _persist_postgres(storage, rows)
    if normalized == "firestore":
        return _persist_firestore(storage, rows)
    raise ValueError(f"unsupported forecast snapshot backend: {backend}")


def select_latest_snapshot_before(
    rows: Iterable[dict[str, Any]],
    *,
    target_date: str,
    hour: int,
    cutoff_at: str,
    timezone: str,
) -> dict[str, Any] | None:
    """Return the newest matching vintage that existed no later than the operational cutoff."""
    cutoff = _parse_timestamp(cutoff_at, timezone=timezone)
    eligible: list[tuple[datetime, dict[str, Any]]] = []
    for row in rows:
        row_hour = to_int(row.get("hour"))
        if str(row.get("date") or "") != target_date or row_hour != hour:
            continue
        issued_raw = str(row.get("issued_at") or "").strip()
        if not issued_raw:
            continue
        try:
            issued = _parse_timestamp(issued_raw, timezone=timezone)
        except (TypeError, ValueError, ZoneInfoNotFoundError):
            continue
        if issued <= cutoff:
            eligible.append((issued, row))
    return max(eligible, key=lambda item: item[0])[1] if eligible else None
