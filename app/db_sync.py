"""Compatibility exports for backend synchronization.

New code should import from :mod:`app.operations.sync`.
"""

from app.operations.sync import TABLE_SPECS, sync_firestore_to_sqlite, sync_sqlite_to_firestore

__all__ = ["TABLE_SPECS", "sync_firestore_to_sqlite", "sync_sqlite_to_firestore"]
