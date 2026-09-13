from __future__ import annotations

import json
import os

from app.runtime.cloud_job import main
from app.runtime.kpnet_unknown_guard import KpNetUnknownTaskTerminal, install_unknown_write_guard


def _run_main() -> int:
    install_unknown_write_guard()
    try:
        return main()
    except KpNetUnknownTaskTerminal:
        print(
            json.dumps(
                {
                    "message": "kpnet-unknown-task-terminal",
                    "slot": os.getenv("CLOUD_JOB_SLOT", "") or None,
                    "classification": "unknown",
                    "platform_retry": "suppressed",
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(_run_main())
