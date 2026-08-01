from __future__ import annotations

import app.config as legacy
import app.local_control.config as canonical


def test_legacy_config_exports_canonical_object() -> None:
    assert legacy.AppConfig is canonical.AppConfig
