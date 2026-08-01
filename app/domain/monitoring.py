from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from app.parsing.numbers import to_float


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
