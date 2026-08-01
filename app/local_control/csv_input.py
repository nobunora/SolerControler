from __future__ import annotations

import csv
from pathlib import Path
from statistics import mean
from typing import Iterable

from app.config import AppConfig
from app.models import MonitoringMetrics
from app.utils import parse_csv_float


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


def _column_values(rows: Iterable[dict[str, str]], column: str) -> list[float]:
    values: list[float] = []
    if not column:
        return values
    for row in rows:
        raw = row.get(column, "")
        if raw is not None and raw.strip():
            parsed = parse_csv_float(raw, default=None)
            if parsed is not None:
                values.append(parsed)
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
