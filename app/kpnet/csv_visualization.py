from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from app.parsing.numbers import parse_csv_float
from app.kpnet.rules import _now_in_timezone


def _month_key(month: str) -> tuple[int, int]:
    year, month_number = month.split("-")
    return int(year), int(month_number)
def _resolve_months(requested: list[str], available: list[str], include_latest: bool) -> list[str]:
    result: list[str] = []
    available_set = set(available)
    for month in requested:
        if month in available_set and month not in result:
            result.append(month)
    if include_latest and available:
        latest = sorted(available, key=_month_key, reverse=True)[0]
        if latest not in result:
            result.append(latest)
    return result


def _default_csv_target_months(now: datetime | None = None) -> list[str]:
    base = now or _now_in_timezone("Asia/Tokyo")
    current = base.strftime("%Y-%m")
    if base.month == 1:
        previous = f"{base.year - 1}-12"
    else:
        previous = f"{base.year}-{base.month - 1:02d}"
    return [previous, current]


def _parse_csv_points(csv_path: Path) -> tuple[list[datetime], list[float], list[float]]:
    datetimes: list[datetime] = []
    generation_values: list[float] = []
    soc_values: list[float] = []

    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date_text = (row.get("年月日") or "").strip()
            time_text = (row.get("時刻") or "").strip()
            if not date_text or not time_text:
                continue
            try:
                dt = datetime.strptime(f"{date_text} {time_text}", "%Y/%m/%d %H:%M")
            except ValueError:
                continue
            try:
                gen = float((row.get("発電電力量[kWh]") or "0").strip() or "0")
            except ValueError:
                gen = 0.0
            try:
                soc = float((row.get("蓄電残量(SOC)[%]") or "nan").strip())
            except ValueError:
                soc = float("nan")

            datetimes.append(dt)
            generation_values.append(gen)
            soc_values.append(soc)
    return datetimes, generation_values, soc_values


def _plot_csvs(csv_paths: list[Path], output_path: Path) -> dict[str, Any]:
    all_points: list[tuple[datetime, float, float]] = []
    for path in csv_paths:
        dts, gens, socs = _parse_csv_points(path)
        all_points.extend(zip(dts, gens, socs))
    if not all_points:
        raise RuntimeError("グラフ化できるCSVデータがありませんでした")

    all_points.sort(key=lambda x: x[0])
    xs = [p[0] for p in all_points]
    ys_gen = [p[1] for p in all_points]
    ys_soc = [p[2] for p in all_points]

    fig, ax1 = plt.subplots(figsize=(14, 6))
    # readable-code-audit: skip TOOL-01 — matplotlib stubs cannot express the datetime values collected from provider CSV files
    ax1.plot(xs, ys_gen, color="#1f77b4", linewidth=1.2, label="PV kWh/30min")  # type: ignore[arg-type]
    ax1.set_xlabel("Datetime")
    ax1.set_ylabel("Generation kWh/30min", color="#1f77b4")
    ax1.tick_params(axis="y", labelcolor="#1f77b4")
    ax1.grid(alpha=0.3)

    ax2 = ax1.twinx()
    # readable-code-audit: skip TOOL-01 — matplotlib stubs cannot express the datetime values collected from provider CSV files
    ax2.plot(xs, ys_soc, color="#ff7f0e", linewidth=1.0, label="Battery SOC %")  # type: ignore[arg-type]
    ax2.set_ylabel("SOC %", color="#ff7f0e")
    ax2.tick_params(axis="y", labelcolor="#ff7f0e")
    ax2.set_ylim(0, 100)

    fig.autofmt_xdate()
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=140)
    plt.close(fig)
    return {"points": len(xs), "plot_path": str(output_path)}


