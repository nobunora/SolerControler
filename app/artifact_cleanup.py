from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class CleanupCandidate:
    path: Path
    reason: str
    size_bytes: int


def _older_than(path: Path, *, cutoff: datetime) -> bool:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime) < cutoff
    except FileNotFoundError:
        return False


def _file_size(path: Path) -> int:
    try:
        return int(path.stat().st_size)
    except FileNotFoundError:
        return 0


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def png_candidates(artifacts_dir: Path, *, cutoff: datetime) -> Iterable[CleanupCandidate]:
    for path in artifacts_dir.rglob("*.png"):
        if _older_than(path, cutoff=cutoff):
            yield CleanupCandidate(path=path, reason="regenerable_png", size_bytes=_file_size(path))


def cloud_pull_candidates(artifacts_dir: Path, *, cutoff: datetime) -> Iterable[CleanupCandidate]:
    for pattern in ("cloud_pull*.json", "cloud_pull*.db"):
        for path in artifacts_dir.glob(pattern):
            if path.is_file() and _older_than(path, cutoff=cutoff):
                yield CleanupCandidate(path=path, reason="temporary_cloud_pull", size_bytes=_file_size(path))


def replay_generated_candidates(artifacts_dir: Path, *, cutoff: datetime) -> Iterable[CleanupCandidate]:
    replay_dir = artifacts_dir / "replay"
    if not replay_dir.exists():
        return
    for pattern in ("**/replay.db", "**/kpi_plot.png", "**/night_charge_plan.json"):
        for path in replay_dir.glob(pattern):
            if path.is_file() and _older_than(path, cutoff=cutoff):
                yield CleanupCandidate(path=path, reason="regenerable_replay_output", size_bytes=_file_size(path))


def duplicate_csv_run_candidates(artifacts_dir: Path, *, keep_latest: int, cutoff: datetime) -> Iterable[CleanupCandidate]:
    run_dirs = [
        path
        for path in artifacts_dir.iterdir()
        if path.is_dir()
        and path.name[:8].isdigit()
        and len(path.name) >= 15
        and (path / "csv").exists()
        and not _is_under(path, artifacts_dir / "replay")
    ]
    run_dirs.sort(key=lambda item: item.name, reverse=True)
    seen_month_sets: dict[tuple[str, ...], int] = {}
    kept_recent = set(run_dirs[: max(0, keep_latest)])
    for run_dir in run_dirs:
        csv_files = sorted((run_dir / "csv").glob("*.csv"))
        if not csv_files:
            continue
        months = tuple(sorted(_csv_month_key(path) for path in csv_files))
        if run_dir in kept_recent:
            seen_month_sets[months] = seen_month_sets.get(months, 0) + 1
            continue
        if not _older_than(run_dir, cutoff=cutoff):
            continue
        if seen_month_sets.get(months, 0) > 0:
            for path in csv_files:
                yield CleanupCandidate(path=path, reason="duplicate_csv_run", size_bytes=_file_size(path))
            for extra in ("kpi_plot.png", "kpnet_summary.json"):
                path = run_dir / extra
                if path.exists():
                    yield CleanupCandidate(path=path, reason="duplicate_csv_run", size_bytes=_file_size(path))
        seen_month_sets[months] = seen_month_sets.get(months, 0) + 1


def _csv_month_key(path: Path) -> str:
    name = path.name
    for token in name.replace(".", "_").split("_"):
        if len(token) == 6 and token.isdigit() and token.startswith("20"):
            return token
    return name


def collect_cleanup_candidates(
    artifacts_dir: Path,
    *,
    png_days: int = 14,
    temporary_days: int = 14,
    replay_output_days: int = 30,
    duplicate_csv_days: int = 30,
    duplicate_csv_keep_latest: int = 14,
    now: datetime | None = None,
) -> list[CleanupCandidate]:
    current = now or datetime.now()
    candidates: dict[Path, CleanupCandidate] = {}
    groups = [
        png_candidates(artifacts_dir, cutoff=current - timedelta(days=png_days)),
        cloud_pull_candidates(artifacts_dir, cutoff=current - timedelta(days=temporary_days)),
        replay_generated_candidates(artifacts_dir, cutoff=current - timedelta(days=replay_output_days)),
        duplicate_csv_run_candidates(
            artifacts_dir,
            keep_latest=duplicate_csv_keep_latest,
            cutoff=current - timedelta(days=duplicate_csv_days),
        ),
    ]
    for group in groups:
        for candidate in group:
            candidates.setdefault(candidate.path, candidate)
    return sorted(candidates.values(), key=lambda item: str(item.path))


def delete_candidates(candidates: Iterable[CleanupCandidate], *, artifacts_dir: Path) -> tuple[int, int]:
    count = 0
    bytes_deleted = 0
    root = artifacts_dir.resolve()
    for candidate in candidates:
        path = candidate.path.resolve()
        if not _is_under(path, root):
            raise RuntimeError(f"refusing to delete outside artifacts: {path}")
        if not path.exists() or not path.is_file():
            continue
        size = _file_size(path)
        path.unlink()
        count += 1
        bytes_deleted += size
    return count, bytes_deleted
