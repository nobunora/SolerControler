#!/usr/bin/env bash
set -euo pipefail

echo "[remote-codex-smoke] start"

if [[ ! -f "AGENTS.md" ]]; then
  echo "[remote-codex-smoke] AGENTS.md not found. Run from repository root." >&2
  exit 1
fi

python --version

echo "[remote-codex-smoke] import check"
python - <<'PY'
from app.soc_cost_optimizer import ForecastScenario, SocCostModel, optimize_soc_by_expected_cost
from app.forecast_correction import _risk_adjusted_peak_penalty

print("imports ok")
print("scenario", ForecastScenario("smoke", 1.0, 1.0, 1.0))
print("sell_loss", SocCostModel(39.1, 31.0, 0.8, 0.0, sell_opportunity_loss_yen_per_kwh_override=38.75).sell_opportunity_loss_yen_per_kwh)
PY

echo "[remote-codex-smoke] focused tests"
python -m pytest tests/test_soc_cost_optimizer.py -q
python -m pytest tests/test_energy_model.py -q

echo "[remote-codex-smoke] ok"
