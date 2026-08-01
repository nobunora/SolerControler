from __future__ import annotations

import app.operations.postgres as canonical
import app.postgres_ops as legacy


def test_legacy_postgres_operations_exports_canonical_objects() -> None:
    public_names = (
        "ensure_schema", "ingest_monitoring_csvs", "ingest_settings_summary",
        "ingest_sunshine_from_night_plan", "open_postgres", "recalc_battery_end_of_day_soc",
        "recalc_battery_pv_charge_end_soc", "recalc_cost_daily", "recalc_model_hit_rates",
        "record_planned_day_mode", "upsert_battery_daily_metrics", "upsert_model_parameters_from_plan",
    )
    for name in public_names:
        assert getattr(legacy, name) is getattr(canonical, name)
