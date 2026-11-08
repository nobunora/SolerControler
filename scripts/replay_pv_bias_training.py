"""Local non-applying PV replay from frozen originals; no remote calls or DB writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.energy_plan.hourly_pv_bias import LOOKBACK_DAYS, MAX_SNAPSHOT_ROWS, _apply, _stamp
from app.energy_plan.monitoring_history import _monitoring_row
from app.energy_plan.pv_bias_training import coefficient_at, collect_training_days, fit_training_bias, select_training_bias
from app.forecasting.pv_array import load_pv_array_configs
from app.forecasting.pv_ideal_max import IdealMaxArray
from app.operations.forecast_snapshot import build_forecast_snapshot_rows
from app.operations.pv_ideal_max import configured_ideal_max_geometry


def replay(*, database: Path, originals_directory: Path, ledger: Path, target_date: str,
           as_of: datetime, output: Path, array_config: Path | None = None) -> dict[str, Any]:
    if output.exists():
        raise ValueError("output exists; originals must not be overwritten")
    if as_of.utcoffset() is None:
        raise ValueError("as_of requires a timezone")
    hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (database, ledger)}
    geometry = configured_ideal_max_geometry()
    if array_config is not None:
        geometry = replace(geometry, arrays=tuple(IdealMaxArray(a.azimuth_deg, a.tilt_deg, a.capacity_kw,
                           a.performance_ratio, a.shading_factor) for a in load_pv_array_configs(array_config)))
    with sqlite3.connect(ledger.resolve().as_uri() + "?mode=ro", uri=True) as db:
        state = json.loads(db.execute("SELECT payload FROM current_state").fetchone()[0])
        if state["fingerprint"] != geometry.fingerprint:
            raise ValueError("ledger and geometry differ")
        events = [json.loads(r[0]) for r in db.execute("SELECT payload FROM observations")]
    events.sort(key=lambda r: (r["available_at"], r["interval_start"]))
    coefficients = [(datetime.fromisoformat(r["available_at"]), float(r["coefficient_after"])) for r in events]
    if not coefficients:
        coefficients = [(datetime.fromisoformat(state["updated_at"]), float(state["coefficient"]))]
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        start = (date.fromisoformat(target_date) - timedelta(days=LOOKBACK_DAYS)).isoformat()
        actuals = [_monitoring_row(dict(r)) for r in db.execute(
            "SELECT * FROM monitoring_samples WHERE ts>=? AND ts<? ORDER BY ts LIMIT ?",
            (start, target_date, LOOKBACK_DAYS * 48 + 1))]
        frozen = [json.loads(r[0]) for r in db.execute(
            "SELECT payload FROM simulation_original_forecasts WHERE date>=? AND date<=? LIMIT ?",
            (start, target_date, MAX_SNAPSHOT_ROWS + 193))]
        plans = [dict(r) for r in db.execute(
            "SELECT * FROM simulation_original_plans WHERE date>=? AND date<=? LIMIT ?",
            (start, target_date, LOOKBACK_DAYS * 8 + 9))]
        if len(actuals) > LOOKBACK_DAYS * 48 or len(frozen) > MAX_SNAPSHOT_ROWS + 192 or len(plans) > LOOKBACK_DAYS * 8 + 8:
            raise ValueError("local input read budget exceeded")
    origins = []
    for row in plans:
        path = originals_directory / (row["sha256"] + ".private.json")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != row["sha256"]:
            raise ValueError("original checksum mismatch")
        plan = json.loads(raw)
        candidates = [r for r in frozen if r["date"] == row["date"]]
        receipts = [_stamp(r.get("recorded_at")) for r in candidates]
        valid_receipts = [t for t in receipts if t is not None]
        if not valid_receipts:
            continue
        snapshots = build_forecast_snapshot_rows(plan, ingested_at=max(valid_receipts).isoformat(), timezone=geometry.timezone_name)
        if len(snapshots) != 24 or snapshots[0]["issued_at_source"] != "plan":
            continue
        origins.append({"date": row["date"], "issued_at": snapshots[0]["issued_at"],
                        "available_at": max(valid_receipts), "sha256": row["sha256"], "plan": plan})
    days, coverage = collect_training_days(origins, actuals, geometry=geometry, target_date=target_date, as_of=as_of)
    targets = sorted((o for o in origins if o["date"] == target_date and (_stamp(o["issued_at"]) or as_of) <= as_of),
                     key=lambda o: _stamp(o["issued_at"]) or as_of)
    if not targets:
        raise ValueError("target original unavailable")
    from app.energy_plan.pv_bias_training import reforecast_origin
    basis, factors, weather = reforecast_origin(targets[0]["plan"], geometry=geometry)
    coefficient = coefficient_at(coefficients, as_of)
    if coefficient is None:
        raise ValueError("coefficient is unavailable at as_of")
    maximum = {h: x * coefficient for h, x in basis.items()}
    if len(factors) != 24:
        raise ValueError("target weather incomplete")
    baseline = {h: maximum[h] * factors[h] for h in range(24)}
    delta, counts = fit_training_bias(days, coefficient=coefficient, as_of=as_of, before=datetime.fromisoformat(target_date).date())
    shadow = _apply(baseline, maximum, weather, delta)
    _, diagnostics, _ = select_training_bias(days=days, coefficients=coefficients, as_of=as_of,
        target_date=target_date, forecast_pv=baseline, maximum=maximum, weather=weather,
        economic_days=(), evaluate_day=None)
    result = {"mode": "shadow", "production_applied": False, "remote_calls": 0, "DB_writes": 0,
              "target_date": target_date, "geometry_fingerprint": geometry.fingerprint,
              "coefficient": coefficient, "coverage": coverage, "bin_days": counts,
              "baseline_kwh": baseline, "candidate_shadow_kwh": shadow,
              "adoption": diagnostics, "economic_replay": "NOT_RUN", "source_files_unchanged": True}
    for name, value in hashes.items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest() != value:
            raise ValueError("a source changed")
    output.mkdir(parents=True)
    (output / "replay.safe.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database", type=Path, required=True)
    p.add_argument("--originals-directory", type=Path, required=True)
    p.add_argument("--ledger", type=Path, required=True)
    p.add_argument("--array-config", type=Path)
    p.add_argument("--target-date", required=True)
    p.add_argument("--as-of", type=datetime.fromisoformat, required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args(argv)
    r = replay(database=a.database, originals_directory=a.originals_directory, ledger=a.ledger,
               target_date=a.target_date, as_of=a.as_of, output=a.output, array_config=a.array_config)
    print(json.dumps({k: r[k] for k in ("mode", "production_applied", "remote_calls", "DB_writes", "coverage", "source_files_unchanged")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
