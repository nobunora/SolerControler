from __future__ import annotations

from datetime import datetime
from typing import Any


def build_historical_profile(rows: list[dict[str, Any]]) -> dict[str, float]:
    by_day: dict[str, dict[str, float]] = {}
    for row in rows:
        timestamp = row["dt"]
        assert isinstance(timestamp, datetime)
        day = timestamp.date().isoformat()
        values = by_day.setdefault(
            day,
            {"day_load": 0.0, "morning_load": 0.0, "day_pv": 0.0, "morning_pv": 0.0},
        )
        hour = timestamp.hour
        load = float(row["load"])
        pv = float(row["pv"])
        # 料金・放電計画で使う昼間窓と同じ区間だけを日中需給として学習する。
        if 7 <= hour < 23:
            values["day_load"] += load
            values["day_pv"] += pv
        if 7 <= hour < 10:
            values["morning_load"] += load
            values["morning_pv"] += pv

    days = list(by_day.values())
    if not days:
        raise RuntimeError("日次集計対象データがありません")
    avg_day_load = sum(day["day_load"] for day in days) / len(days)
    avg_morning_load = sum(day["morning_load"] for day in days) / len(days)
    sum_day_pv = sum(day["day_pv"] for day in days)
    sum_morning_pv = sum(day["morning_pv"] for day in days)
    return {
        "avg_day_load_kwh": avg_day_load,
        "avg_morning_load_kwh": avg_morning_load,
        # 発電実績がないときも朝不足の見積りを止めないため、保守的な既定比率を使う。
        "morning_pv_ratio": (sum_morning_pv / sum_day_pv) if sum_day_pv > 0 else 0.25,
        # 実績分解が未導入の初期状態でも昼間の充電余地を見積もるための暫定比率。
        "midday_surplus_ratio": 0.375,
    }
