"""Read-only device state capture for backup generations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.kpnet.client import KpNetClient
from app.kpnet.config import KpNetConfig


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def build_device_settings_snapshot() -> dict[str, Any]:
    """Read current KP-NET settings without changing the device."""
    config = KpNetConfig.from_env()
    client = KpNetClient(config)
    logged_in = False
    try:
        client.login()
        logged_in = True
        client.open_settings_page()
        settings = client.read_current_settings()
        return {
            "schema_version": 1,
            "captured_at_utc": utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "source": "kpnet_readback",
            "settings": settings,
        }
    finally:
        if logged_in:
            client.logout()
