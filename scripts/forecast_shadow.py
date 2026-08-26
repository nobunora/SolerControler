"""Record and report the fixed Phase 1 hourly PV shadow gate.

The command is an explicit side-channel operation. It never changes
``forecast_hourly`` or production forecasts.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.operations import sqlite
from app.operations.shadow_gate import (
    _snapshot_rows,
    build_shadow_decision,
    ensure_shadow_schema,
    persist_shadow_decisions,
    persist_shadow_outcomes,
    report_shadow_outcomes,
)


def _actuals(conn, start: date | None, end: date | None) -> dict[tuple[str, int], float]:
    clauses = []
    params: list[str] = []
    if start:
        clauses.append("substr(ts,1,10) >= ?")
        params.append(start.isoformat())
    if end:
        clauses.append("substr(ts,1,10) <= ?")
        params.append(end.isoformat())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        "SELECT substr(ts,1,10) AS day, CAST(substr(ts,12,2) AS INTEGER) AS hour, "
        f"SUM(COALESCE(pv_kwh,0)) AS actual FROM monitoring_samples {where} GROUP BY 1,2",
        params,
    ).fetchall()
    return {(str(row["day"]), int(row["hour"])): float(row["actual"]) for row in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=Path("artifacts/solar_monitor.db"))
    parser.add_argument("--mode", choices=("decision", "outcome", "report"), required=True)
    parser.add_argument("--target-start", type=date.fromisoformat)
    parser.add_argument("--target-end", type=date.fromisoformat)
    parser.add_argument("--cutoff-at", default=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    parser.add_argument("--decision-at", default=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    parser.add_argument("--recorded-at", default=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    conn = sqlite.open_db(args.db_path)
    try:
        ensure_shadow_schema(conn)
        if args.mode == "decision":
            if not args.target_start or not args.target_end:
                parser.error("--target-start and --target-end are required for decision mode")
            snapshots = _snapshot_rows(conn, args.cutoff_at, args.target_start, args.target_end)
            decisions = [
                build_shadow_decision(
                    conn,
                    snapshot,
                    decision_at=args.decision_at,
                    cutoff_at=args.cutoff_at,
                )
                for snapshot in snapshots
            ]
            result = {"mode": "decision", "candidate_snapshot_count": len(snapshots), "inserted_decision_count": persist_shadow_decisions(conn, decisions)}
        elif args.mode == "outcome":
            actuals = _actuals(conn, args.target_start, args.target_end)
            result = {"mode": "outcome", "actual_hour_count": len(actuals), "inserted_outcome_count": persist_shadow_outcomes(conn, actuals, recorded_at=args.recorded_at)}
        else:
            result = {"mode": "report", **report_shadow_outcomes(conn, start=args.target_start, end=args.target_end)}
    finally:
        conn.close()
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
