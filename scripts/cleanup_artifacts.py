from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.artifact_cleanup import collect_cleanup_candidates, delete_candidates


def main() -> int:
    parser = argparse.ArgumentParser(description="Prune regenerable local artifacts without deleting source data.")
    parser.add_argument("--artifacts-dir", type=Path, default=Path("artifacts"))
    parser.add_argument("--apply", action="store_true", help="Delete files. Default is dry-run.")
    parser.add_argument("--png-days", type=int, default=14)
    parser.add_argument("--temporary-days", type=int, default=14)
    parser.add_argument("--replay-output-days", type=int, default=30)
    parser.add_argument("--duplicate-csv-days", type=int, default=30)
    parser.add_argument("--duplicate-csv-keep-latest", type=int, default=14)
    args = parser.parse_args()

    candidates = collect_cleanup_candidates(
        args.artifacts_dir,
        png_days=args.png_days,
        temporary_days=args.temporary_days,
        replay_output_days=args.replay_output_days,
        duplicate_csv_days=args.duplicate_csv_days,
        duplicate_csv_keep_latest=args.duplicate_csv_keep_latest,
    )
    total = sum(item.size_bytes for item in candidates)
    for item in candidates:
        print(f"{item.reason}\t{item.size_bytes}\t{item.path}")
    if not args.apply:
        print(f"[cleanup] dry-run candidates={len(candidates)} bytes={total}")
        return 0
    count, bytes_deleted = delete_candidates(candidates, artifacts_dir=args.artifacts_dir)
    print(f"[cleanup] deleted={count} bytes={bytes_deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
