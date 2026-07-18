from __future__ import annotations

import json

from app.energy_plan import EnergyPlanOutput, PlanDocumentV1


def test_energy_plan_output_preserves_utf8_document_contract(tmp_path) -> None:
    document = PlanDocumentV1(
        csv_paths=["監視.csv"],
        plan_quality={"should_apply": True},
        forecast={"date": "2026-07-19"},
        pv_array_forecast=None,
        historical_profile={},
        consumption_forecast={},
        base_consumption_forecast={},
        weather_history={},
        occupancy_adjustment=None,
        coefficients={},
        inputs={},
        result={"target_soc_7_percent": 80.0},
        daytime_soc_optimization=None,
        decision_rationale={"objective": "維持"},
    )
    path = tmp_path / "night_charge_plan.json"

    EnergyPlanOutput(document, path).persist()

    assert json.loads(path.read_text(encoding="utf-8")) == document.to_payload()
    assert "監視.csv" in path.read_text(encoding="utf-8")
