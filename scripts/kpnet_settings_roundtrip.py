"""Run the explicit live KP-NET setting round-trip test mode."""

from __future__ import annotations

import argparse
import json

from app.kpnet.settings_roundtrip import run_settings_roundtrip


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-soc", type=float, required=True)
    parser.add_argument("--test-execution", action="store_true")
    args = parser.parse_args()
    if not args.test_execution:
        raise RuntimeError("refusing live setting mutation without --test-execution")
    summary = run_settings_roundtrip(target_soc_percent=args.target_soc)
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
