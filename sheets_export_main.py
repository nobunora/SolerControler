from __future__ import annotations

import os

from app.sheets_export import run_export


def main() -> int:
    slot = os.getenv("CLOUD_JOB_SLOT", "").strip().lower() or "unknown"
    if slot in {"night", "night23"}:
        slot = "23"
    elif slot in {"day", "day07"}:
        slot = "07"
    elif slot in {"3", "03", "adjust", "adjust03"}:
        slot = "03"
    return run_export(slot=slot)


if __name__ == "__main__":
    raise SystemExit(main())
