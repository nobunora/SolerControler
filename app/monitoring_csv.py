from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from app.utils import to_float


@dataclass(frozen=True)
class MonitoringPoint:
    timestamp: datetime
    pv_kwh: float | None
    load_kwh: float | None
    sell_kwh: float | None
    buy_kwh: float | None
    charge_kwh: float | None
    discharge_kwh: float | None
    soc_percent: float | None

    def as_storage_row(self) -> dict[str, str | float | None]:
        return {
            "ts": self.timestamp.isoformat(),
            "pv_kwh": self.pv_kwh,
            "load_kwh": self.load_kwh,
            "sell_kwh": self.sell_kwh,
            "buy_kwh": self.buy_kwh,
            "charge_kwh": self.charge_kwh,
            "discharge_kwh": self.discharge_kwh,
            "soc_percent": self.soc_percent,
        }


def validated_soc_percent(value: object) -> float | None:
    parsed = to_float(value)
    if parsed is None or not math.isfinite(parsed) or not 0.0 <= parsed <= 100.0:
        return None
    return parsed


def iter_monitoring_points(csv_path: Path) -> Iterator[MonitoringPoint]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            date_text = (row.get("年月日") or "").strip()
            time_text = (row.get("時刻") or "").strip()
            if not date_text or not time_text:
                continue
            try:
                timestamp = datetime.strptime(f"{date_text} {time_text}", "%Y/%m/%d %H:%M")
            except ValueError:
                continue
            yield MonitoringPoint(
                timestamp=timestamp,
                pv_kwh=to_float(row.get("発電電力量[kWh]")),
                load_kwh=to_float(row.get("消費電力量[kWh]")),
                sell_kwh=to_float(row.get("売電電力量[kWh]")),
                buy_kwh=to_float(row.get("買電電力量[kWh]")),
                charge_kwh=to_float(row.get("充電電力量[kWh]")),
                discharge_kwh=to_float(row.get("放電電力量[kWh]")),
                soc_percent=validated_soc_percent(row.get("蓄電残量(SOC)[%]")),
            )

