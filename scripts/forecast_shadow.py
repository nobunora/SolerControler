"""Record and report the fixed Phase 1 hourly PV shadow gate.

The command is an explicit side-channel operation. It never changes
``forecast_hourly`` or production forecasts.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.operations import sqlite
from app.operations.shadow_gate import (
    _snapshot_rows,
    build_shadow_decision,
    ensure_shadow_schema,
    is_frozen_policy_target,
    open_sqlite_read_only,
    persist_shadow_decisions,
    persist_shadow_decision_diagnostics,
    persist_shadow_outcomes,
    report_shadow_outcomes,
    validate_separate_db_paths,
)


def _parse_monitoring_timestamp(value: str, site_timezone: ZoneInfo) -> datetime:
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=site_timezone)
    return parsed.astimezone(site_timezone)


def _actuals(conn: sqlite3.Connection, start: date | None, end: date | None, *, timezone_name: str = "Asia/Tokyo") -> dict[tuple[str, int], dict[str, Any]]:
    site_timezone = ZoneInfo(timezone_name)
    totals: defaultdict[tuple[str, int], dict[str, Any]] = defaultdict(
        lambda: {"actual": 0.0, "sample_count": 0, "first_sample_at": None, "last_sample_at": None, "completeness_contract_proven": False}
    )
    for row in conn.execute("SELECT ts, pv_kwh FROM monitoring_samples").fetchall():
        local = _parse_monitoring_timestamp(str(row["ts"]), site_timezone)
        key = (local.date().isoformat(), local.hour)
        if start and local.date() < start:
            continue
        if end and local.date() > end:
            continue
        totals[key]["actual"] = float(totals[key]["actual"]) + float(row["pv_kwh"] or 0.0)
        totals[key]["sample_count"] = int(totals[key]["sample_count"]) + 1
        iso = local.isoformat()
        first = totals[key]["first_sample_at"]
        last = totals[key]["last_sample_at"]
        totals[key]["first_sample_at"] = iso if first is None or iso < str(first) else first
        totals[key]["last_sample_at"] = iso if last is None or iso > str(last) else last
    return dict(totals)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db-path", type=Path)
    parser.add_argument("--shadow-db-path", type=Path)
    parser.add_argument("--mode", choices=("decision", "outcome", "report"), required=True)
    parser.add_argument("--target-start", type=date.fromisoformat)
    parser.add_argument("--target-end", type=date.fromisoformat)
    parser.add_argument("--cutoff-at")
    parser.add_argument("--decision-at")
    parser.add_argument("--recorded-at")
    parser.add_argument("--timezone", default=os.getenv("TIMEZONE", "Asia/Tokyo"))
    parser.add_argument("--evidence-class", choices=("prospective_primary", "retrospective_diagnostic"), default="prospective_primary")
    parser.add_argument("--source-code-version")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    args.cutoff_at = args.cutoff_at or now
    args.decision_at = args.decision_at or now
    args.recorded_at = args.recorded_at or now

    if args.mode in {"decision", "outcome"}:
        if args.source_db_path is None or args.shadow_db_path is None:
            parser.error("decision/outcome require --source-db-path and --shadow-db-path")
        try:
            source_path, shadow_path = validate_separate_db_paths(args.source_db_path, args.shadow_db_path)
        except (FileNotFoundError, ValueError) as exc:
            parser.error(str(exc))
        source_conn = open_sqlite_read_only(source_path)
        shadow_conn = sqlite.open_db(shadow_path)
    else:
        if args.shadow_db_path is None:
            parser.error("report requires --shadow-db-path")
        source_conn = None
        shadow_conn = sqlite.open_db(args.shadow_db_path)
    try:
        ensure_shadow_schema(shadow_conn)
        if args.mode == "decision":
            if source_conn is None:
                raise RuntimeError("source connection is required for decision mode")
            if not args.target_start or not args.target_end:
                parser.error("--target-start and --target-end are required for decision mode")
            if args.evidence_class == "prospective_primary" and not str(args.source_code_version or "").strip():
                parser.error("--source-code-version is required for prospective_primary decision mode")
            snapshots = _snapshot_rows(source_conn, args.cutoff_at, args.target_start, args.target_end)
            eligible = [row for row in snapshots if is_frozen_policy_target(int(row["hour"]), float(row.get("forecast_pv_kwh") or 0.0))]
            excluded = [row for row in snapshots if row not in eligible]
            if excluded:
                persist_shadow_decision_diagnostics(shadow_conn, excluded, attempted_at=args.decision_at, reason="outside_frozen_policy_domain", source_code_version=args.source_code_version)
            decisions = [
                build_shadow_decision(
                    source_conn,
                    shadow_conn,
                    snapshot,
                    decision_at=args.decision_at,
                    cutoff_at=args.cutoff_at,
                    source_code_version=args.source_code_version,
                    evidence_class=args.evidence_class,
                )
                for snapshot in eligible
            ]
            result = {"mode": "decision", "candidate_snapshot_count": len(snapshots), "eligible_target_count": len(eligible), "excluded_count": len(excluded), "inserted_decision_count": persist_shadow_decisions(shadow_conn, decisions)}
        elif args.mode == "outcome":
            if source_conn is None:
                raise RuntimeError("source connection is required for outcome mode")
            actuals = _actuals(source_conn, args.target_start, args.target_end, timezone_name=args.timezone)
            result = {"mode": "outcome", "actual_hour_count": len(actuals), "actual_completeness_rule": "BLOCKED", "inserted_outcome_count": persist_shadow_outcomes(shadow_conn, actuals, recorded_at=args.recorded_at)}
        else:
            result = {"mode": "report", **report_shadow_outcomes(shadow_conn, start=args.target_start, end=args.target_end)}
    finally:
        shadow_conn.close()
        if source_conn is not None:
            source_conn.close()
    payload = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
