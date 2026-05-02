from __future__ import annotations

import csv
import re
from pathlib import Path
from statistics import mean
from typing import Iterable, Optional

from app.config import AppConfig
from app.models import MonitoringMetrics

_NUM_PATTERN = re.compile(r"[-+]?\d+(?:\.\d+)?")


def _read_rows(csv_path: Path) -> list[dict[str, str]]:
    raw = ""
    for enc in ("utf-8-sig", "cp932", "utf-8"):
        try:
            raw = csv_path.read_text(encoding=enc)
            break
        except UnicodeDecodeError:
            continue
    if not raw:
        raw = csv_path.read_text(encoding="utf-8-sig", errors="ignore")
    sample = raw[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample)
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(raw.splitlines(), dialect=dialect)
    return list(reader)


def _to_float(raw: str) -> Optional[float]:
    if raw is None:
        return None
    match = _NUM_PATTERN.search(raw.replace(",", ""))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _column_values(rows: Iterable[dict[str, str]], column: str) -> list[float]:
    values: list[float] = []
    if not column:
        return values
    for row in rows:
        v = _to_float(row.get(column, ""))
        if v is not None:
            values.append(v)
    return values


def parse_monitoring_csv(csv_path: Path, cfg: AppConfig) -> MonitoringMetrics:
    rows = _read_rows(csv_path)
    if not rows:
        return MonitoringMetrics(
            row_count=0,
            latest_soc=None,
            avg_soc=None,
            total_charge=0.0,
            total_discharge=0.0,
        )

    soc_values = _column_values(rows, cfg.csv_soc_column)
    latest_soc = soc_values[-1] if soc_values else None
    avg_soc = mean(soc_values) if soc_values else None

    charge_values = _column_values(rows, cfg.csv_charge_power_column)
    discharge_values = _column_values(rows, cfg.csv_discharge_power_column)

    return MonitoringMetrics(
        row_count=len(rows),
        latest_soc=latest_soc,
        avg_soc=avg_soc,
        total_charge=sum(charge_values),
        total_discharge=sum(discharge_values),
    )
