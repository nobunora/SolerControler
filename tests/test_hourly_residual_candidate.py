from copy import deepcopy

import pytest

from app.forecasting.hourly_residual_candidate import evaluate_hourly_residuals


def record(day, q=1.0, actual=2.0):
    return {"date": day, "hour": 7, "q": q, "actual": actual,
            "issued_at": f"{day}T02:35:00+09:00", "target_at": f"{day}T07:00:00+09:00",
            "baseline_version": "v1", "kind": "pv", "basis": "reconstructed",
            "actual_interval_minutes": [0, 30]}


def test_walk_forward_missing_zero_and_version_partition():
    rows = [record("2026-09-01"), record("2026-09-02", actual=None), record("2026-09-03", actual=0)]
    rows.append({**record("2026-09-04"), "baseline_version": "v2"})
    original = deepcopy(rows)
    result = evaluate_hourly_residuals(rows, alpha=0.2)
    assert rows == original
    assert [r.get("candidate") for r in result["records"]] == [1, None, 2, 1]
    assert result["records"][2]["updated_bias"] == pytest.approx(0.6)
    assert result["prospective_days"] == 0
    assert not result["production_connected"]


@pytest.mark.parametrize("alpha", [0, 1])
def test_alpha_edges(alpha):
    result = evaluate_hourly_residuals([record("2026-09-01"), record("2026-09-02", actual=0)], alpha=alpha)
    assert result["records"][1]["updated_bias"] == 1 - 2 * alpha


@pytest.mark.parametrize("patch", [{"actual_interval_minutes": [0]}, {"issued_at": "2026-09-01T08:00:00+09:00"},
                                   {"q": float("nan")}, {"target_at": "2026-09-02T07:00:00+09:00"}])
def test_ineligible_inputs(patch):
    result = evaluate_hourly_residuals([{**record("2026-09-01"), **patch}], alpha=0.2)
    assert result["hours"] == 0


def test_duplicate_hour_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        evaluate_hourly_residuals([record("2026-09-01"), record("2026-09-01")], alpha=0.2)
