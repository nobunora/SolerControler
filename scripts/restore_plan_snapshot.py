"""Restore a downloaded Drive JSON(.gz) to an isolated local directory."""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.backup.plan_snapshot import restore_snapshot_payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    raw = args.snapshot.read_bytes()
    if args.snapshot.suffix == ".gz":
        raw = gzip.decompress(raw)
    restored = restore_snapshot_payload(json.loads(raw), args.destination)
    print(json.dumps({"restored": len(restored)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
