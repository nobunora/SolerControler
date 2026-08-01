"""Compatibility exports for weekly backups."""

from app.backup.weekly import WeeklyBackupResult, create_weekly_diff_backup

__all__ = ["WeeklyBackupResult", "create_weekly_diff_backup"]
