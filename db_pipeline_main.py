from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from app import operations_db as sqlite_ops
from app.weekly_backup import create_weekly_diff_backup

_SUCCESSFUL_SETTING_STATUSES = {"applied", "skipped-no-change"}


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _collect_csv_paths(csv_run_dir: Path) -> list[Path]:
    summary = json.loads((csv_run_dir / "kpnet_summary.json").read_text(encoding="utf-8"))
    entries = summary.get("csv_downloads", [])
    csv_paths: list[Path] = []
    for entry in entries:
        path = Path(str(entry.get("path", "")))
        if path.exists():
            csv_paths.append(path)
    return csv_paths


def _settings_summary_successful(summary_path: Path) -> bool:
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if summary.get("error"):
        return False
    results = summary.get("setting_results")
    if not isinstance(results, list) or not results:
        return False
    return all(
        isinstance(item, dict)
        and str(item.get("status", "")).strip() in _SUCCESSFUL_SETTING_STATUSES
        for item in results
    )


def _record_planned_day_mode_sqlite(conn, *, settings_summary_path: Path, recorded_at: str) -> None:
    summary = json.loads(settings_summary_path.read_text(encoding="utf-8"))
    run_id = str(summary.get("run_id", settings_summary_path.parent.name))
    day_plan = summary.get("daytime_mode_plan")
    if not isinstance(day_plan, dict):
        return
    conn.execute(
        """
        INSERT INTO settings_events (run_id, slot, profile, status, changed_fields_json, detail_json, source_doc_id, recorded_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            "07",
            "green-mode",
            "planned-from-23",
            "[]",
            json.dumps(day_plan, ensure_ascii=False, separators=(",", ":")),
            f"{run_id}-07-planned-green",
            recorded_at,
        ),
    )
    conn.commit()


def _maybe_weekly_backup(conn, *, cfg: sqlite_ops.PipelineConfig, backend: str, now_utc: datetime) -> None:
    if not cfg.weekly_backup_enabled:
        print("[db_pipeline] weekly backup: disabled")
        return
    force = os.getenv("DATA_WEEKLY_BACKUP_FORCE", "false").strip().lower() in {"1", "true", "yes", "on"}
    result = create_weekly_diff_backup(
        conn,
        backend=backend,
        out_dir=cfg.weekly_backup_dir,
        now_utc=now_utc,
        weekday=cfg.weekly_backup_weekday,
        force=force,
    )
    print(f"[db_pipeline] weekly backup: created={result.created} reason={result.reason} path={result.path}")


def _ingest_sqlite(
    cfg: sqlite_ops.PipelineConfig,
    *,
    csv_run_dir: Path | None,
    settings_run_dir: Path | None,
    now_iso: str,
    now_utc: datetime,
    include_night_plan: bool = True,
) -> None:
    conn = sqlite_ops.open_db(cfg.db_path)
    try:
        sqlite_ops.ensure_schema(conn)
        csv_rows = 0
        csv_run_id = csv_run_dir.name if csv_run_dir else ""
        settings_run_id = settings_run_dir.name if settings_run_dir else ""
        run_key = f"{cfg.site_id}:{cfg.slot}:{csv_run_id}:{settings_run_id}"
        exists = conn.execute("SELECT 1 FROM pipeline_runs WHERE run_key=?", (run_key,)).fetchone()
        if exists:
            print(f"[db_pipeline] already ingested: {run_key}")
            return

        if csv_run_dir is not None:
            csv_paths = _collect_csv_paths(csv_run_dir)
            csv_rows = sqlite_ops.ingest_monitoring_csvs(conn, csv_paths=csv_paths, ingested_at=now_iso)

        if settings_run_dir is not None:
            summary_path = settings_run_dir / "kpnet_summary.json"
            sqlite_ops.ingest_settings_summary(
                conn,
                settings_summary_path=summary_path,
                slot=cfg.slot,
                ingested_at=now_iso,
            )
            if cfg.slot == "23":
                _record_planned_day_mode_sqlite(conn, settings_summary_path=summary_path, recorded_at=now_iso)
            if _settings_summary_successful(summary_path):
                sqlite_ops.upsert_battery_daily_metrics(
                    conn,
                    summary_path=summary_path,
                    updated_at=now_iso,
                    night_plan_path=cfg.artifacts_dir / "night_charge_plan.json",
                    slot=cfg.slot,
                )
            else:
                print(f"[db_pipeline] skip battery metrics: settings summary not successful path={summary_path}")
        pv_charge_end_updated = sqlite_ops.recalc_battery_pv_charge_end_soc(conn, updated_at=now_iso)
        print(f"[db_pipeline] battery pv_charge_end_soc updated rows={pv_charge_end_updated}")

        if include_night_plan:
            night_plan_path = cfg.artifacts_dir / "night_charge_plan.json"
            sqlite_ops.ingest_sunshine_from_night_plan(
                conn,
                night_plan_path=night_plan_path,
                timezone=cfg.timezone,
                ingested_at=now_iso,
            )
            sqlite_ops.upsert_model_parameters_from_plan(conn, night_plan_path=night_plan_path, updated_at=now_iso)
        else:
            print("[db_pipeline] skip night plan and forecast ingestion")
        hit_rate = sqlite_ops.recalc_model_hit_rates(conn, updated_at=now_iso)
        print(f"[db_pipeline] model hit_rate={hit_rate!r}")
        sqlite_ops.recalc_cost_daily(
            conn,
            day_rate_yen_per_kwh=cfg.day_rate_yen_per_kwh,
            updated_at=now_iso,
            tariff_mode=cfg.cost_tariff_mode,
            night8_day_start_hhmm=cfg.night8_day_start_hhmm,
            night8_day_end_hhmm=cfg.night8_day_end_hhmm,
            night8_day_tier1_upper_kwh=cfg.night8_day_tier1_upper_kwh,
            night8_day_tier2_upper_kwh=cfg.night8_day_tier2_upper_kwh,
            night8_day_rate_tier1_yen=cfg.night8_day_rate_tier1_yen,
            night8_day_rate_tier2_yen=cfg.night8_day_rate_tier2_yen,
            night8_day_rate_tier3_yen=cfg.night8_day_rate_tier3_yen,
            night8_night_rate_yen=cfg.night8_night_rate_yen,
        )

        conn.execute(
            """
            INSERT INTO pipeline_runs (run_key, slot, csv_run_id, settings_run_id, csv_rows_upserted, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (run_key, cfg.slot, csv_run_id or None, settings_run_id or None, csv_rows, now_iso),
        )
        conn.commit()
        _maybe_weekly_backup(conn, cfg=cfg, backend="sqlite", now_utc=now_utc)
    finally:
        conn.close()
    print(f"[db_pipeline] done backend=sqlite path={cfg.db_path}")


def _ingest_postgres(
    cfg: sqlite_ops.PipelineConfig,
    *,
    csv_run_dir: Path | None,
    settings_run_dir: Path | None,
    now_iso: str,
    now_utc: datetime,
    include_night_plan: bool = True,
) -> None:
    from app import postgres_ops

    conn = postgres_ops.open_postgres()
    try:
        postgres_ops.ensure_schema(conn)
        csv_rows = 0
        csv_run_id = csv_run_dir.name if csv_run_dir else ""
        settings_run_id = settings_run_dir.name if settings_run_dir else ""
        run_key = f"{cfg.site_id}:{cfg.slot}:{csv_run_id}:{settings_run_id}"

        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pipeline_runs WHERE run_key=%s", (run_key,))
            exists = cur.fetchone()
        if exists:
            print(f"[db_pipeline] already ingested: {run_key}")
            return

        if csv_run_dir is not None:
            csv_paths = _collect_csv_paths(csv_run_dir)
            csv_rows = postgres_ops.ingest_monitoring_csvs(conn, csv_paths=csv_paths, ingested_at=now_iso)

        if settings_run_dir is not None:
            summary_path = settings_run_dir / "kpnet_summary.json"
            postgres_ops.ingest_settings_summary(
                conn,
                settings_summary_path=summary_path,
                slot=cfg.slot,
                ingested_at=now_iso,
            )
            if cfg.slot == "23":
                postgres_ops.record_planned_day_mode(conn, settings_summary_path=summary_path, recorded_at=now_iso)
            if _settings_summary_successful(summary_path):
                postgres_ops.upsert_battery_daily_metrics(
                    conn,
                    summary_path=summary_path,
                    updated_at=now_iso,
                    night_plan_path=cfg.artifacts_dir / "night_charge_plan.json",
                    slot=cfg.slot,
                )
            else:
                print(f"[db_pipeline] skip battery metrics: settings summary not successful path={summary_path}")
        pv_charge_end_updated = postgres_ops.recalc_battery_pv_charge_end_soc(conn, updated_at=now_iso)
        print(f"[db_pipeline] battery pv_charge_end_soc updated rows={pv_charge_end_updated}")

        if include_night_plan:
            night_plan_path = cfg.artifacts_dir / "night_charge_plan.json"
            postgres_ops.ingest_sunshine_from_night_plan(
                conn,
                night_plan_path=night_plan_path,
                timezone=cfg.timezone,
                ingested_at=now_iso,
            )
            postgres_ops.upsert_model_parameters_from_plan(conn, night_plan_path=night_plan_path, updated_at=now_iso)
        else:
            print("[db_pipeline] skip night plan and forecast ingestion")
        hit_rate = postgres_ops.recalc_model_hit_rates(conn, updated_at=now_iso)
        print(f"[db_pipeline] model hit_rate={hit_rate!r}")
        postgres_ops.recalc_cost_daily(
            conn,
            day_rate_yen_per_kwh=cfg.day_rate_yen_per_kwh,
            updated_at=now_iso,
            tariff_mode=cfg.cost_tariff_mode,
            night8_day_start_hhmm=cfg.night8_day_start_hhmm,
            night8_day_end_hhmm=cfg.night8_day_end_hhmm,
            night8_day_tier1_upper_kwh=cfg.night8_day_tier1_upper_kwh,
            night8_day_tier2_upper_kwh=cfg.night8_day_tier2_upper_kwh,
            night8_day_rate_tier1_yen=cfg.night8_day_rate_tier1_yen,
            night8_day_rate_tier2_yen=cfg.night8_day_rate_tier2_yen,
            night8_day_rate_tier3_yen=cfg.night8_day_rate_tier3_yen,
            night8_night_rate_yen=cfg.night8_night_rate_yen,
        )

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO pipeline_runs (run_key, slot, csv_run_id, settings_run_id, csv_rows_upserted, recorded_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (run_key, cfg.slot, csv_run_id or None, settings_run_id or None, csv_rows, now_iso),
            )
        conn.commit()
        _maybe_weekly_backup(conn, cfg=cfg, backend="postgres", now_utc=now_utc)
    finally:
        conn.close()
    print("[db_pipeline] done backend=postgres")


def _ingest_firestore(
    cfg: sqlite_ops.PipelineConfig,
    *,
    csv_run_dir: Path | None,
    settings_run_dir: Path | None,
    now_iso: str,
    include_night_plan: bool = True,
) -> None:
    from app import firestore_ops

    client = firestore_ops.open_firestore()
    firestore_ops.ensure_schema(client)
    csv_rows = 0
    csv_run_id = csv_run_dir.name if csv_run_dir else ""
    settings_run_id = settings_run_dir.name if settings_run_dir else ""
    run_key = f"{cfg.site_id}:{cfg.slot}:{csv_run_id}:{settings_run_id}"
    if firestore_ops.pipeline_run_exists(client, run_key=run_key):
        print(f"[db_pipeline] already ingested: {run_key}")
        return

    if csv_run_dir is not None:
        csv_paths = _collect_csv_paths(csv_run_dir)
        csv_rows = firestore_ops.ingest_monitoring_csvs(client, csv_paths=csv_paths, ingested_at=now_iso)

    if settings_run_dir is not None:
        summary_path = settings_run_dir / "kpnet_summary.json"
        firestore_ops.ingest_settings_summary(
            client,
            settings_summary_path=summary_path,
            slot=cfg.slot,
            ingested_at=now_iso,
        )
        if cfg.slot == "23":
            firestore_ops.record_planned_day_mode(client, settings_summary_path=summary_path, recorded_at=now_iso)
        if _settings_summary_successful(summary_path):
            firestore_ops.upsert_battery_daily_metrics(
                client,
                summary_path=summary_path,
                updated_at=now_iso,
                night_plan_path=cfg.artifacts_dir / "night_charge_plan.json",
                slot=cfg.slot,
            )
        else:
            print(f"[db_pipeline] skip battery metrics: settings summary not successful path={summary_path}")
    pv_charge_end_updated = firestore_ops.recalc_battery_pv_charge_end_soc(client, updated_at=now_iso)
    print(f"[db_pipeline] battery pv_charge_end_soc updated rows={pv_charge_end_updated}")
    dashboard_daily_updated = firestore_ops.recalc_dashboard_daily_metrics(client, updated_at=now_iso)
    print(f"[db_pipeline] dashboard daily metrics updated rows={dashboard_daily_updated}")

    if include_night_plan:
        night_plan_path = cfg.artifacts_dir / "night_charge_plan.json"
        firestore_ops.ingest_sunshine_from_night_plan(
            client,
            night_plan_path=night_plan_path,
            timezone=cfg.timezone,
            ingested_at=now_iso,
        )
        firestore_ops.upsert_model_parameters_from_plan(client, night_plan_path=night_plan_path, updated_at=now_iso)
    else:
        print("[db_pipeline] skip night plan and forecast ingestion")
    hit_rate = firestore_ops.recalc_model_hit_rates(client, updated_at=now_iso)
    print(f"[db_pipeline] model hit_rate={hit_rate!r}")
    firestore_ops.recalc_cost_daily(
        client,
        day_rate_yen_per_kwh=cfg.day_rate_yen_per_kwh,
        updated_at=now_iso,
        tariff_mode=cfg.cost_tariff_mode,
        night8_day_start_hhmm=cfg.night8_day_start_hhmm,
        night8_day_end_hhmm=cfg.night8_day_end_hhmm,
        night8_day_tier1_upper_kwh=cfg.night8_day_tier1_upper_kwh,
        night8_day_tier2_upper_kwh=cfg.night8_day_tier2_upper_kwh,
        night8_day_rate_tier1_yen=cfg.night8_day_rate_tier1_yen,
        night8_day_rate_tier2_yen=cfg.night8_day_rate_tier2_yen,
        night8_day_rate_tier3_yen=cfg.night8_day_rate_tier3_yen,
        night8_night_rate_yen=cfg.night8_night_rate_yen,
    )
    firestore_ops.upsert_pipeline_run(
        client,
        run_key=run_key,
        slot=cfg.slot,
        csv_run_id=csv_run_id or None,
        settings_run_id=settings_run_id or None,
        csv_rows_upserted=csv_rows,
        recorded_at=now_iso,
    )
    print("[db_pipeline] weekly backup: disabled (firestore backend)")
    print("[db_pipeline] done backend=firestore")


def main() -> int:
    cfg = sqlite_ops.PipelineConfig.from_env()
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    now_iso = now_utc.isoformat().replace("+00:00", "Z")

    if cfg.write_only_slot_23 and cfg.slot != "23":
        print(f"[db_pipeline] skip write: slot={cfg.slot} and DATA_DB_WRITE_ONLY_23=true")
        return 0

    include_csv = _env_bool("DATA_PIPELINE_INCLUDE_CSV", True)
    include_settings = _env_bool("DATA_PIPELINE_INCLUDE_SETTINGS", True)
    include_night_plan = _env_bool("DATA_PIPELINE_INCLUDE_NIGHT_PLAN", True)
    csv_run_dir, settings_run_dir = sqlite_ops.find_latest_csv_and_settings_runs(cfg.artifacts_dir)
    if not include_csv:
        csv_run_dir = None
    if not include_settings:
        settings_run_dir = None
    if csv_run_dir is None and settings_run_dir is None:
        print("[db_pipeline] no eligible run dirs found")
        return 0

    if cfg.storage_sync_enabled:
        print("[db_pipeline] note: DATA_DB_SYNC_ENABLED=true ですが、逐次Cloud Storage同期は無効化されています。")

    if cfg.data_backend == "sqlite":
        _ingest_sqlite(
            cfg,
            csv_run_dir=csv_run_dir,
            settings_run_dir=settings_run_dir,
            now_iso=now_iso,
            now_utc=now_utc,
            include_night_plan=include_night_plan,
        )
        return 0
    if cfg.data_backend == "postgres":
        _ingest_postgres(
            cfg,
            csv_run_dir=csv_run_dir,
            settings_run_dir=settings_run_dir,
            now_iso=now_iso,
            now_utc=now_utc,
            include_night_plan=include_night_plan,
        )
        return 0
    if cfg.data_backend == "firestore":
        _ingest_firestore(
            cfg,
            csv_run_dir=csv_run_dir,
            settings_run_dir=settings_run_dir,
            now_iso=now_iso,
            include_night_plan=include_night_plan,
        )
        return 0

    raise RuntimeError(f"unsupported DATA_BACKEND: {cfg.data_backend}")


if __name__ == "__main__":
    raise SystemExit(main())
