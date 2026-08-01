"""Compatibility exports for artifact cleanup.

New code should import from :mod:`app.backup.artifacts`.
"""

from app.backup.artifacts import CleanupCandidate, collect_cleanup_candidates, delete_candidates

__all__ = ["CleanupCandidate", "collect_cleanup_candidates", "delete_candidates"]
