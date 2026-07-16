from __future__ import annotations

from app.energy_plan import PlanDocumentV1


def test_plan_document_v1_preserves_consumer_contract() -> None:
    document = PlanDocumentV1(
        csv_paths=["monitor.csv"],
        plan_quality={"should_apply": True},
        forecast={"date": "2026-07-16"},
        pv_array_forecast={},
        historical_profile={},
        consumption_forecast={},
        base_consumption_forecast={},
        weather_history={},
        occupancy_adjustment={},
        coefficients={},
        inputs={"soc_now_percent": 50.0},
        result={"target_soc_7_percent": 80.0, "required_night_charge_kwh": 3.0},
        daytime_soc_optimization=None,
        decision_rationale={"objective": "test"},
    )

    payload = document.to_payload()

    assert list(payload) == [
        "csv_paths",
        "plan_quality",
        "forecast",
        "pv_array_forecast",
        "historical_profile",
        "consumption_forecast",
        "base_consumption_forecast",
        "weather_history",
        "occupancy_adjustment",
        "coefficients",
        "inputs",
        "result",
        "daytime_soc_optimization",
        "decision_rationale",
    ]
    assert payload["forecast"] == {"date": "2026-07-16"}
    assert payload["result"] == {"target_soc_7_percent": 80.0, "required_night_charge_kwh": 3.0}
