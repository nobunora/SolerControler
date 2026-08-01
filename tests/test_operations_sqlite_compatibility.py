from __future__ import annotations

import app.operations.sqlite as canonical
import app.operations_db as legacy


def test_legacy_sqlite_operations_exports_canonical_objects() -> None:
    assert legacy.PipelineConfig is canonical.PipelineConfig
    assert legacy.ensure_schema is canonical.ensure_schema
    assert legacy.find_latest_csv_and_settings_runs is canonical.find_latest_csv_and_settings_runs
    assert legacy.ingest_monitoring_csvs is canonical.ingest_monitoring_csvs
    assert legacy.ingest_settings_summary is canonical.ingest_settings_summary
    assert legacy.ingest_sunshine_from_night_plan is canonical.ingest_sunshine_from_night_plan
    assert legacy.open_db is canonical.open_db
    assert legacy.recalc_battery_end_of_day_soc is canonical.recalc_battery_end_of_day_soc
    assert legacy.recalc_battery_pv_charge_end_soc is canonical.recalc_battery_pv_charge_end_soc
    assert legacy.recalc_cost_daily is canonical.recalc_cost_daily
    assert legacy.recalc_model_hit_rates is canonical.recalc_model_hit_rates
    assert legacy.upsert_battery_daily_metrics is canonical.upsert_battery_daily_metrics
    assert legacy.upsert_model_parameters_from_plan is canonical.upsert_model_parameters_from_plan
