"""Read locally downloaded KP-NET monitoring CSV history."""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path


def iter_charge_soc_points(csv_paths: list[Path]) -> list[tuple[datetime, float, float]]:
    """Return valid charge/SOC observations ordered by their local timestamp."""
    points: list[tuple[datetime, float, float]] = []
    for csv_path in csv_paths:
        if not csv_path.exists():
            continue
        with csv_path.open("r", encoding="utf-8-sig", newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            for row in reader:
                date_text = (row.get("年月日") or "").strip()
                time_text = (row.get("時刻") or "").strip()
                soc_text = (row.get("蓄電残量(SOC)[%]") or "").strip()
                charge_text = (row.get("充電電力量[kWh]") or "").strip()
                if not date_text or not time_text or not soc_text:
                    continue
                try:
                    timestamp = datetime.strptime(f"{date_text} {time_text}", "%Y/%m/%d %H:%M")
                    soc_percent = float(soc_text)
                    charge_kwh = float(charge_text) if charge_text else 0.0
                except (TypeError, ValueError):
                    continue
                points.append((timestamp, soc_percent, charge_kwh))
    return sorted(points, key=lambda point: point[0])


def find_latest_kpnet_csv_paths(artifacts_dir: Path) -> list[Path]:
    """Return CSV files in the newest timestamp-named KP-NET run, or an empty list."""
    run_dirs = [path for path in artifacts_dir.glob("*") if path.is_dir() and path.name[:8].isdigit()]
    for run_dir in sorted(run_dirs, key=lambda path: path.name, reverse=True):
        csv_paths = sorted((run_dir / "csv").glob("*.csv"))
        if csv_paths:
            return csv_paths
    return []
