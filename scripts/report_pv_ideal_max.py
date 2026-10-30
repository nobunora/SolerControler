"""Read-only hourly evaluation from a local ledger or verified cloud checkout."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.forecasting.pv_ideal_max_cloud import IdealMaxCloudArchive
from app.forecasting.pv_ideal_max_store import IdealMaxStore
from app.operations.pv_ideal_max import configured_ideal_max_geometry, ideal_max_archive_prefix
from app.operations.pv_ideal_max_evaluation import evaluate_ideal_max_hours


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--state-dir", type=Path)
    source.add_argument("--cloud", action="store_true")
    parser.add_argument("--from-date", type=date.fromisoformat, required=True)
    parser.add_argument("--through-date", type=date.fromisoformat, required=True)
    parser.add_argument("--as-of", type=datetime.fromisoformat, required=True)
    parser.add_argument("--issued-not-before", type=datetime.fromisoformat,
                        help="conservative independent issue evidence time for a restored research seed")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.output.exists():
        parser.error("output already exists")
    geometry = configured_ideal_max_geometry()
    zone = ZoneInfo(geometry.timezone_name)
    start = datetime.combine(args.from_date, time(), zone)
    end = datetime.combine(args.through_date + timedelta(days=1), time(), zone)
    issued_not_before = args.issued_not_before
    with tempfile.TemporaryDirectory(prefix="ideal-max-report-") as temporary:
        if args.cloud:
            from google.cloud.storage import Client
            archive = IdealMaxCloudArchive(Client(), ideal_max_archive_prefix(), geometry)
            store = archive.checkout(Path(temporary))
            if store is None:
                parser.error("cloud ledger is not initialized")
            if archive.original is None:
                raise ValueError("cloud checkout lacks initialization provenance")
            initialized_at = datetime.fromisoformat(archive.original["initialized_at"])
            issued_not_before = max(initialized_at, issued_not_before) if issued_not_before is not None else initialized_at
        else:
            store = IdealMaxStore(args.state_dir, geometry)
        with store:
            result = evaluate_ideal_max_hours(store, start, end, as_of=args.as_of, issued_not_before=issued_not_before)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({key: value for key, value in result.items() if key != "hourly"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
