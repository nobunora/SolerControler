from __future__ import annotations

from pathlib import Path

import pytest

from app.soc_decision_feedback import build_soc_decision_feedback, build_soc_decision_prior


def test_build_soc_decision_feedback_finds_realized_best_soc(tmp_path: Path) -> None:
    csv_path = tmp_path / "kpnet.csv"
    csv_path.write_text(
        "\n".join(
                [
                    "年月日,時刻,発電電力量[kWh],消費電力量[kWh],売電電力量[kWh],買電電力量[kWh],充電電力量[kWh],放電電力量[kWh],蓄電残量(SOC)[%]",
                    "2026/07/10,07:00,0.0,0.0,0,0,0,0,0",
                    "2026/07/10,08:00,0.0,0.0,0,0,0,0,0",
                    "2026/07/10,09:00,0.0,0.0,0,0,0,0,0",
                    "2026/07/10,10:00,6.0,0.0,0,0,0,0,0",
                    "2026/07/10,11:00,0.0,0.0,0,0,0,0,0",
                    "2026/07/10,12:00,0.0,0.0,0,0,0,0,0",
                    "2026/07/10,13:00,0.0,0.0,0,0,0,0,0",
                    "2026/07/10,20:00,0.0,8.0,0,0,0,0,0",
                ]
            ),
        encoding="utf-8-sig",
    )
    plan = {
        "inputs": {"soc_now_percent": 0.0},
        "result": {"effective_capacity_kwh": 10.0},
        "daytime_soc_optimization": {
            "forecast_correction": {
                "soc_peak_unmet_penalty": {"target_peak_soc_percent": 0.0},
            },
            "cost_model": {
                "day_buy_rate_yen_per_kwh": 40.0,
                "night_buy_rate_yen_per_kwh": 10.0,
                "charge_efficiency": 1.0,
                "sell_value_ratio": 0.0,
                "export_value_mode": "penalty",
                "sell_opportunity_loss_yen_per_kwh_override": 40.0,
            }
        },
    }

    feedback = build_soc_decision_feedback(
        plan=plan,
        csv_paths=[csv_path],
        target_date="2026-07-10",
        min_rows=1,
        step_percent=10.0,
    )

    assert feedback is not None
    assert feedback["best_target_soc_percent"] == pytest.approx(20.0)
    assert feedback["decision_features"]["actual_pv_kwh"] == pytest.approx(6.0)
    assert feedback["decision_features"]["actual_load_kwh"] == pytest.approx(8.0)
    regret_at_zero = next(point["regret_yen"] for point in feedback["points"] if point["target_soc_percent"] == 0.0)
    assert regret_at_zero > 0.0


def test_build_soc_decision_prior_uses_recent_regret_curve(monkeypatch) -> None:
    monkeypatch.setenv("SOC_DECISION_FEEDBACK_WEIGHT", "0.3")
    monkeypatch.setenv("SOC_DECISION_FEEDBACK_CONFIDENCE_DAYS", "1")
    prior = build_soc_decision_prior(
        [
            {
                "date": "2026-07-10",
                "best_target_soc_percent": 20.0,
                "points": [
                    {"target_soc_percent": 0.0, "regret_yen": 90.0},
                    {"target_soc_percent": 20.0, "regret_yen": 0.0},
                ],
            }
        ],
        target_date="2026-07-11",
    )

    assert prior["applied"] is True
    assert prior["sample_count"] == 1
    assert prior["weight"] == pytest.approx(0.15)
    assert prior["regret_yen_by_soc"]["0"] == pytest.approx(90.0)


def test_build_soc_decision_prior_excludes_dissimilar_pv_day(monkeypatch) -> None:
    monkeypatch.setenv("SOC_DECISION_FEEDBACK_WEIGHT", "0.3")
    monkeypatch.setenv("SOC_DECISION_FEEDBACK_CONFIDENCE_DAYS", "1")
    prior = build_soc_decision_prior(
        [
            {
                "date": "2026-07-10",
                "best_target_soc_percent": 24.0,
                "actual_summary": {"pv_kwh": 18.138, "load_kwh": 21.883},
                "plan_reference": {"final_predicted_pv_kwh": 19.6471},
                "points": [
                    {"target_soc_percent": 24.0, "regret_yen": 0.0},
                    {"target_soc_percent": 95.0, "regret_yen": 400.0},
                ],
            }
        ],
        target_date="2026-07-12",
        target_features={
            "forecast_pv_kwh": 7.4877,
            "forecast_load_kwh": 18.0,
            "final_pv_forecast_source": "physical_pv_forecast",
        },
    )

    assert prior["applied"] is False
    assert prior["reason"] == "no_similar_feedback"
    assert prior["considered_count"] == 1
    assert prior["excluded_feedback"][0]["date"] == "2026-07-10"
