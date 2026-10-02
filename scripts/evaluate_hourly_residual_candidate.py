"""Read local frozen baseline records and write a local walk-forward report."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.forecasting.hourly_residual_candidate import evaluate_hourly_residuals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--alpha", type=float, required=True)
    args = parser.parse_args()
    records = json.loads(args.input.read_text(encoding="utf-8-sig"))
    result = evaluate_hourly_residuals(records, alpha=args.alpha)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "records"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
