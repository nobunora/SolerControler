from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from app.parsing.numbers import to_float
from app.domain.measurement_time import CSV_TIME_BASIS, csv_measurement_window


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
        start, end = csv_measurement_window(self.timestamp)
        return {
            "ts": self.timestamp.isoformat(),
            "pv_kwh": self.pv_kwh,
            "load_kwh": self.load_kwh,
            "sell_kwh": self.sell_kwh,
            "buy_kwh": self.buy_kwh,
            "charge_kwh": self.charge_kwh,
            "discharge_kwh": self.discharge_kwh,
            "soc_percent": self.soc_percent,
            "interval_start": start.isoformat(), "interval_end": end.isoformat(),
            "soc_effective_at": end.isoformat() if self.soc_percent is not None else None,
            "measurement_time_basis": CSV_TIME_BASIS,
        }


def validated_soc_percent(value: object) -> float | None:
    parsed = to_float(value)
    if parsed is None or not math.isfinite(parsed) or not 0.0 <= parsed <= 100.0:
        return None
    return parsed
