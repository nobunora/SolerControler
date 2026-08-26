"""Record and report the fixed Phase 1 hourly PV shadow gate.

The command is an explicit side-channel operation. It never changes
``forecast_hourly`` or production forecasts.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

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


def _parse_monitoring_timestamp(value: str, site_timezone: ZoneInfo) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=site_timezone)
    return parsed.astimezone(site_timezone)


def _actuals(conn, start: date | None, end: date | None, *, timezone_name: str = "Asia/Tokyo") -> dict[tuple[str, int], dict[str, float | int]]:
    site_timezone = ZoneInfo(timezone_name)
    totals: defaultdict[tuple[str, int], dict[str, float | int]] = defaultdict(lambda: {"actual": 0.0, "sample_count": 0})
    for row in conn.execute("SELECT ts, pv_kwh FROM monitoring_samples").fetchall():
        local = _parse_monitoring_timestamp(str(row["ts"]), site_timezone)
        key = (local.date().isoformat(), local.hour)
        if start and local.date() < start:
            continue
        if end and local.date() > end:
            continue
        totals[key]["actual"] = float(totals[key]["actual"]) + float(row["pv_kwh"] or 0.0)
        totals[key]["sample_count"] = int(totals[key]["sample_count"]) + 1
    return dict(totals)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-path", type=Path, default=Path("artifacts/solar_monitor.db"))
    parser.add_argument("--mode", choices=("decision", "outcome", "report"), required=True)
    parser.add_argument("--target-start", type=date.fromisoformat)
    parser.add_argument("--target-end", type=date.fromisoformat)
    parser.add_argument("--cutoff-at")
    parser.add_argument("--decision-at")
    parser.add_argument("--recorded-at")
    parser.add_argument("--timezone", default=os.getenv("TIMEZONE", "Asia/Tokyo"))
    parser.add_argument("--evidence-class", choices=("prospective_primary", "retrospective_diagnostic"), default="prospective_primary")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    args.cutoff_at = args.cutoff_at or now
    args.decision_at = args.decision_at or now
    args.recorded_at = args.recorded_at or now

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
                    evidence_class=args.evidence_class,
                )
                for snapshot in snapshots
            ]
            result = {"mode": "decision", "candidate_snapshot_count": len(snapshots), "inserted_decision_count": persist_shadow_decisions(conn, decisions)}
        elif args.mode == "outcome":
            actuals = _actuals(conn, args.target_start, args.target_end, timezone_name=args.timezone)
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
