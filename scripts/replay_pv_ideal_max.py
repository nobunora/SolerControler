"""Local CSV replay/import for the independent ideal maximum ledger.

No weather requests, existing forecast writes, or production job execution.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.energy_plan.workflow import EnergyModelConfig, _read_rows
from app.forecasting.pv_array import load_pv_array_configs
from app.forecasting.pv_ideal_max import IdealMaxArray, IdealMaxGeometry
from app.forecasting.pv_ideal_max_store import IdealMaxStore
from app.operations.pv_ideal_max_csv import hourly_observations


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.output.exists():
        raise FileExistsError("output already exists; choose a new report path")
    if args.as_of.tzinfo is None or args.as_of.utcoffset() is None:
        raise ValueError("--as-of requires a timezone offset")
    parts = args.learning_hours.split(":")
    if len(parts) != 2:
        raise ValueError("--learning-hours must be START:END (end exclusive)")
    hours = (int(parts[0]), int(parts[1]))
    if not 0 <= hours[0] < hours[1] <= 24:
        raise ValueError("learning hours must satisfy 0 <= START < END <= 24")
    cfg = EnergyModelConfig.from_env()
    geometry = IdealMaxGeometry(cfg.latitude, cfg.longitude, cfg.timezone, tuple(
        IdealMaxArray(a.azimuth_deg, a.tilt_deg, a.capacity_kw, a.performance_ratio, a.shading_factor)
        for a in load_pv_array_configs()))
    paths = sorted(args.csv_dir.glob("*.csv"))
    if not paths:
        raise ValueError("no CSV input files")
    observations, counts = hourly_observations(_read_rows(paths), zone=ZoneInfo(cfg.timezone),
                                              as_of=args.as_of, hours=hours, replay=args.replay)
    if not observations:
        raise ValueError("no complete available hourly PV observations")
    initialize_at = (observations[0].interval_start if args.replay else args.as_of) if args.initialize else None
    records = []
    duplicates = 0
    with IdealMaxStore(args.state_dir, geometry, initialize_at=initialize_at) as store:
        for observation in observations:
            previous = store.observation_record(observation.interval_start)
            if previous is not None:
                if args.replay and previous["issued_prediction"] is None:
                    raise ValueError("imported history cannot be relabeled as preissued replay")
                store.ingest(observation, as_of=args.as_of)
                duplicates += 1
                continue
            if args.replay:
                store.predict_hour(observation.interval_start, as_of=observation.interval_start)
            store.ingest(observation, as_of=observation.available_at)
            records.append(store.observation_record(observation.interval_start))
        predictions = store.predict_day(args.target_date, as_of=args.as_of)
        state = store.read_current()
    result = {"mode": "replay_assuming_available_at_interval_end" if args.replay else "historical_import_received_at_as_of",
              "learning_hours": {"start": hours[0], "end_exclusive": hours[1]},
              "as_of": args.as_of.isoformat(), "target_date": args.target_date.isoformat(),
              "input_csv_count": len(paths), "available_hours": len(observations), "duplicate_hours": duplicates,
              "excluded": counts, "coefficient": state.coefficient, "revision": state.revision,
              "fingerprint": state.fingerprint, "new_observations": records,
              "hourly_maximum": [{"interval_start": p.interval_start.isoformat(), "base_kwh": p.base_kwh,
                                  "maximum_kwh": p.maximum_kwh, "coefficient": p.coefficient,
                                  "revision": p.revision, "issued_at": p.issued_at.isoformat()}
                                 for p in predictions]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Do not silently replace a previous report, even if the ledger is idempotent.
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv-dir", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--as-of", type=datetime.fromisoformat, required=True)
    parser.add_argument("--target-date", type=date.fromisoformat, required=True)
    parser.add_argument("--learning-hours", required=True, help="explicit START:END, e.g. 5:18 or 0:24")
    parser.add_argument("--initialize", action="store_true", help="permit creation only when no ledger exists")
    parser.add_argument("--replay", action="store_true", help="simulation: observations available at interval end")
    args = parser.parse_args(argv)
    try:
        result = run(args)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps({"available_hours": result["available_hours"], "duplicate_hours": result["duplicate_hours"],
                      "coefficient": result["coefficient"], "revision": result["revision"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
