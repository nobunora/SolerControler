"""Record a generated plan; invoked within the controller's bounded subprocess."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.cloud import storage
from app.backup.plan_snapshot import archive_plan_snapshot
from app.operations.firestore import open_firestore


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--source", choices=("adjust03-generated", "adjust03-reused"), default="adjust03-generated")
    args = parser.parse_args()
    result = archive_plan_snapshot(args.plan, storage=storage.Client(), firestore=open_firestore(), source=args.source)
    print(json.dumps({"message": "plan-archive", **result}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
