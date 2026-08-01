from __future__ import annotations

import app.db_sync as legacy
import app.operations.sync as canonical


def test_legacy_sync_exports_canonical_objects() -> None:
    assert legacy.TABLE_SPECS is canonical.TABLE_SPECS
    assert legacy.sync_firestore_to_sqlite is canonical.sync_firestore_to_sqlite
    assert legacy.sync_sqlite_to_firestore is canonical.sync_sqlite_to_firestore
