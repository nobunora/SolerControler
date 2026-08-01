"""Compatibility exports for the SQLite operations adapter.

New code should import from :mod:`app.operations.sqlite`.
"""

from app.operations.sqlite import (
    PipelineConfig,
    ensure_schema,
    find_latest_csv_and_settings_runs,
    ingest_monitoring_csvs,
    ingest_settings_summary,
    ingest_sunshine_from_night_plan,
    open_db,
    recalc_battery_end_of_day_soc,
    recalc_battery_pv_charge_end_soc,
    recalc_cost_daily,
    recalc_model_hit_rates,
    upsert_battery_daily_metrics,
    upsert_model_parameters_from_plan,
)

__all__ = [
    "PipelineConfig",
    "ensure_schema",
    "find_latest_csv_and_settings_runs",
    "ingest_monitoring_csvs",
    "ingest_settings_summary",
    "ingest_sunshine_from_night_plan",
    "open_db",
    "recalc_battery_end_of_day_soc",
    "recalc_battery_pv_charge_end_soc",
    "recalc_cost_daily",
    "recalc_model_hit_rates",
    "upsert_battery_daily_metrics",
    "upsert_model_parameters_from_plan",
]
