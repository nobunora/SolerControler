from __future__ import annotations

from app import soc_cost_optimizer as optimizer


def test_soc_optimization_request_forwards_legacy_arguments(monkeypatch) -> None:
    uncertainty = optimizer.PvForecastUncertainty(1.0, 0.2, 0.04, 10, "test")
    cost_model = optimizer.SocCostModel(
        night_buy_rate_yen_per_kwh=20.0,
        day_buy_rate_yen_per_kwh=30.0,
        charge_efficiency=0.9,
        sell_value_ratio=0.5,
        sell_opportunity_loss_yen_per_kwh_override=10.0,
    )
    request = optimizer.SocOptimizationRequest(
        capacity_kwh=10.0,
        soc_now_percent=30.0,
        reserve_soc_percent=20.0,
        hourly_load_kwh={7: 1.0},
        hourly_pv_kwh={12: 2.0},
        uncertainty=uncertainty,
        cost_model=cost_model,
        soc_step_percent=2.0,
        max_target_soc_percent=90.0,
        decision_prior_weight=0.25,
    )
    captured = {}
    sentinel = object()
    monkeypatch.setattr(
        optimizer,
        "optimize_soc_by_expected_cost",
        lambda **kwargs: captured.update(kwargs) or sentinel,
    )

    assert optimizer.optimize_soc_request(request) is sentinel
    assert captured["capacity_kwh"] == 10.0
    assert captured["hourly_load_kwh"] == {7: 1.0}
    assert captured["uncertainty"] is uncertainty
    assert captured["cost_model"] is cost_model
    assert captured["soc_step_percent"] == 2.0
    assert captured["max_target_soc_percent"] == 90.0
    assert captured["decision_prior_weight"] == 0.25
    assert set(captured) == set(request.__dataclass_fields__)
