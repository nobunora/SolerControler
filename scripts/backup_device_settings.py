from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.backup.device import build_device_settings_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Read and save current KP-NET settings without changing them.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    snapshot = build_device_settings_snapshot()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
