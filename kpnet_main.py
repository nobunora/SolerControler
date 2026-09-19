from __future__ import annotations

import os


def _run_main() -> int:
    if os.getenv("KP_WORKFLOW_MODE", "all").strip().lower() == "csv":
        from app.kpnet.csv_direct import run_csv_workflow

        return run_csv_workflow()

    from app.kpnet.workflow import main

    return main()


if __name__ == "__main__":
    raise SystemExit(_run_main())
