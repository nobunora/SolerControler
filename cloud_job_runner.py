from __future__ import annotations

import json
import os

from app.runtime.cloud_job import main
from app.runtime.kpnet_unknown_guard import KpNetUnknownTaskTerminal, install_unknown_write_guard


def _unknown_terminal_exit_code() -> int:
    """Keep scheduled retries suppressed unless a dedicated probe asks to observe UNKNOWN."""
    allow_success_exit = os.getenv("KP_NET_UNKNOWN_EXIT_ZERO", "true").strip().lower()
    return 0 if allow_success_exit in {"1", "true", "yes", "on"} else 1


def _run_main() -> int:
    install_unknown_write_guard()
    try:
        return main()
    except KpNetUnknownTaskTerminal:
        exit_code = _unknown_terminal_exit_code()
        print(
            json.dumps(
                {
                    "message": "kpnet-unknown-task-terminal",
                    "slot": os.getenv("CLOUD_JOB_SLOT", "") or None,
                    "classification": "unknown",
                    "platform_retry": "suppressed" if exit_code == 0 else "probe-failed",
                },
                separators=(",", ":"),
            ),
            flush=True,
        )
        return exit_code


if __name__ == "__main__":
    raise SystemExit(_run_main())
