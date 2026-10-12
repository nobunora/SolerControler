"""Canonical one-time repair dispatch; stdout contains only safe evidence."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.operations.firestore import open_firestore  # noqa: E402
from app.operations.monitoring_repair import (  # noqa: E402
    _journal, _load, _save_stage, apply_source_repair, prepare_source_repair, regenerate_derived,
    verify_repair, verify_source_repair,
)
from app.operations.sqlite import PipelineConfig  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-path", required=True, type=Path)
    parser.add_argument("--stage", required=True, choices=("prepare", "source", "daily", "cost", "history", "verify"))
    parser.add_argument("--csv-run-dirs", nargs="*", type=Path, default=[])
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--reviewed-state-sha256", default="")
    args = parser.parse_args(argv)
    try:
        cfg = PipelineConfig.from_env()
        if cfg.data_backend != "firestore":
            raise ValueError("repair requires the existing Firestore backend")
        if args.stage != "prepare" and args.csv_run_dirs:
            raise ValueError("prepared CSV evidence cannot be replaced on resume")
        client = open_firestore()
        if args.stage == "prepare":
            if args.write or args.resume or not args.csv_run_dirs:
                raise ValueError("prepare requires explicit CSV run directories and no Write/Resume")
            end = (datetime.now(ZoneInfo(cfg.timezone)).date() + timedelta(days=1)).isoformat() + "T00:00:00"
            result = prepare_source_repair(client=client, cfg=cfg, cost_end_ts=end,
                csv_run_dirs=tuple(args.csv_run_dirs), target_dates=(date(2026, 9, 20),), state_path=args.state_path)
        elif args.stage == "verify":
            result = verify_repair(client=client, cfg=cfg, state_path=args.state_path)
        elif not args.write:
            result = verify_source_repair(client=client, state_path=args.state_path)
            result = {"status": "dry_run", "source_status": result["status"], "raw_writes": 0}
        else:
            state = _load(args.state_path, client)
            if args.reviewed_state_sha256 != state["reviewed_state_sha256"]:
                raise ValueError("Write requires the reviewed immutable manifest SHA")
            if args.stage == "source":
                result = apply_source_repair(client=client, state_path=args.state_path,
                    reviewed_state_sha256=args.reviewed_state_sha256)
            else:
                result = regenerate_derived(client=client, cfg=cfg, state_path=args.state_path, stage=args.stage)
        safe_keys = ("status", "reason_code", "target_dates", "candidate_count", "reviewed_state_sha256",
                     "verified_count", "attempted_count", "raw_writes", "elapsed_ms", "source_status")
        print(json.dumps({key: result[key] for key in safe_keys if key in result}, sort_keys=True), flush=True)
        return 0 if result["status"] in {"prepared", "verified", "dry_run"} else 1
    except Exception as error:
        result = {"status": "failed", "stage": args.stage, "reason_code": type(error).__name__}
        if args.state_path.exists():
            try:
                previous = _journal(args.state_path)["stages"].get(args.stage, {})
                _save_stage(args.state_path, args.stage, {**previous, **result})
            except Exception:
                pass
        print(json.dumps(result), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
