from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.configuration.environment import load_dotenv_if_present
from app.energy_plan.settings import HistoricalInputSettings
from app.energy_plan.workflow import _csv_paths_from_env_or_latest, _read_rows
from app.operations.historical_forecast_replay import (
    DEFAULT_SINGLE_RUN_MODEL,
    build_historical_replay_plan,
    filter_pre_target_history,
    replay_forecast_hash,
    write_historical_replay_plan,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a forecast-only historical replay artifact. No Firestore/control writes."
    )
    parser.add_argument("--target-date", required=True, help="Target date in YYYY-MM-DD")
    parser.add_argument(
        "--csv-path",
        action="append",
        default=[],
        help="Historical KP-NET CSV path. Repeat for multiple files. Defaults to normal history discovery.",
    )
    parser.add_argument("--model", default=DEFAULT_SINGLE_RUN_MODEL)
    parser.add_argument(
        "--run",
        default="",
        help="Explicit Single Runs UTC model initialization, e.g. 2026-08-29T12:00. Defaults to D-1 12:00 UTC.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output plan path. Defaults to artifacts/historical_replay/<target-date>.json",
    )
    parser.add_argument(
        "--verify-anti-lookahead",
        action="store_true",
        help="Rebuild from an explicitly pre-target-only row set and require an identical forecast hash.",
    )
    return parser


def main() -> int:
    args = _parser().parse_args()
    load_dotenv_if_present()
    history_settings = HistoricalInputSettings.from_env()
    csv_paths = [Path(value) for value in args.csv_path]
    if not csv_paths:
        csv_paths = _csv_paths_from_env_or_latest(history_settings.artifacts_dir)
    missing = [str(path) for path in csv_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"historical replay CSV does not exist: {missing}")

    rows = _read_rows(csv_paths)
    result = build_historical_replay_plan(
        rows,
        target_date=args.target_date,
        model=args.model,
        run=args.run or None,
    )

    anti_lookahead: dict[str, object] = {
        "checked": False,
        "passed": None,
        "full_input_hash": result.forecast_hash,
        "pre_target_only_hash": None,
    }
    if args.verify_anti_lookahead:
        pre_target_rows = filter_pre_target_history(rows, target_date=args.target_date)
        restricted = build_historical_replay_plan(
            pre_target_rows,
            target_date=args.target_date,
            model=args.model,
            run=args.run or None,
        )
        passed = restricted.forecast_hash == result.forecast_hash
        anti_lookahead = {
            "checked": True,
            "passed": passed,
            "full_input_hash": result.forecast_hash,
            "pre_target_only_hash": restricted.forecast_hash,
        }
        if not passed:
            raise RuntimeError("historical replay anti-lookahead invariance check failed")

    output = Path(args.output) if args.output else Path("artifacts/historical_replay") / f"{args.target_date}.json"
    write_historical_replay_plan(result, output)
    optimization = result.plan["daytime_soc_optimization"]
    replay = result.plan["historical_replay"]
    summary = {
        "target_date": args.target_date,
        "output_path": str(output),
        "basis": replay["basis"],
        "model_version": replay["model_version"],
        "weather_forecast": replay["weather_forecast"],
        "history_cutoff": replay["history_cutoff"],
        "eligible_history_start": result.history_start,
        "eligible_history_end": result.history_end,
        "eligible_history_row_count": result.eligible_history_row_count,
        "load_model_source": replay["load_model_source"],
        "load_model_sample_count": replay["load_model_sample_count"],
        "forecast_hash": replay_forecast_hash(result.plan),
        "forecast_pv_total_kwh": round(sum(optimization["hourly_pv_forecast_kwh"].values()), 6),
        "forecast_load_total_kwh": round(sum(optimization["hourly_load_forecast_kwh"].values()), 6),
        "anti_lookahead": anti_lookahead,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
