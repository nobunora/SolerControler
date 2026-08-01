"""Compatibility exports for the Firestore operations adapter.

New code should import from :mod:`app.operations.firestore`.
"""

from app.operations.firestore import (
    ensure_schema,
    ingest_monitoring_csvs,
    ingest_settings_summary,
    ingest_sunshine_from_night_plan,
    open_firestore,
    pipeline_run_exists,
    recalc_battery_end_of_day_soc,
    recalc_battery_pv_charge_end_soc,
    recalc_cost_daily,
    recalc_dashboard_daily_metrics,
    recalc_model_hit_rates,
    record_planned_day_mode,
    upsert_battery_daily_metrics,
    upsert_model_parameters_from_plan,
    upsert_pipeline_run,
)

__all__ = [
    "ensure_schema",
    "ingest_monitoring_csvs",
    "ingest_settings_summary",
    "ingest_sunshine_from_night_plan",
    "open_firestore",
    "pipeline_run_exists",
    "recalc_battery_end_of_day_soc",
    "recalc_battery_pv_charge_end_soc",
    "recalc_cost_daily",
    "recalc_dashboard_daily_metrics",
    "recalc_model_hit_rates",
    "record_planned_day_mode",
    "upsert_battery_daily_metrics",
    "upsert_model_parameters_from_plan",
    "upsert_pipeline_run",
]
