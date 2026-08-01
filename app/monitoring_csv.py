from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Iterator

from app.domain.monitoring import MonitoringPoint, validated_soc_percent
from app.parsing.numbers import to_float


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

