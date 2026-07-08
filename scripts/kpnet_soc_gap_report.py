from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.firestore_ops import open_firestore
from app.utils import parse_csv_float


JST = ZoneInfo("Asia/Tokyo")


@dataclass(frozen=True)
class MonitorRow:
    day: date
    hhmm: str
    pv_kwh: float
    load_kwh: float
    sell_kwh: float
    buy_kwh: float
    charge_kwh: float
    discharge_kwh: float
    soc_percent: float | None

    @property
    def timestamp(self) -> datetime:
        hour, minute = (int(part) for part in self.hhmm.split(":", 1))
        return datetime.combine(self.day, time(hour, minute), tzinfo=JST)


@dataclass(frozen=True)
class WindowSummary:
    rows: int
    first_hhmm: str
    last_hhmm: str
    pv_kwh: float
    load_kwh: float
    sell_kwh: float
    buy_kwh: float
    charge_kwh: float
    discharge_kwh: float
    soc_first: float | None
    soc_last: float | None
    soc_min: tuple[float, str] | None
    soc_max: tuple[float, str] | None


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "cp932", "utf-8"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8-sig", errors="ignore")


def _parse_day(raw: str) -> date | None:
    raw = raw.strip()
    if not raw:
        return None
    for fmt in ("%Y/%m/%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _float(row: dict[str, str], column: str) -> float:
    return float(parse_csv_float(row.get(column, ""), default=0.0) or 0.0)


def read_monitoring_rows(csv_paths: Iterable[Path]) -> list[MonitorRow]:
    rows: list[MonitorRow] = []
    for csv_path in csv_paths:
        raw = _read_text(csv_path)
        try:
            dialect = csv.Sniffer().sniff(raw[:4096])
        except csv.Error:
            dialect = csv.excel
        for row in csv.DictReader(raw.splitlines(), dialect=dialect):
            day = _parse_day(str(row.get("年月日", "")))
            hhmm = str(row.get("時刻", "")).strip()
            if day is None or not hhmm:
                continue
            soc_raw = row.get("蓄電残量(SOC)[%]", "")
            soc = parse_csv_float(soc_raw, default=None) if soc_raw is not None else None
            rows.append(
                MonitorRow(
                    day=day,
                    hhmm=hhmm,
                    pv_kwh=_float(row, "発電電力量[kWh]"),
                    load_kwh=_float(row, "消費電力量[kWh]"),
                    sell_kwh=_float(row, "売電電力量[kWh]"),
                    buy_kwh=_float(row, "買電電力量[kWh]"),
                    charge_kwh=_float(row, "充電電力量[kWh]"),
                    discharge_kwh=_float(row, "放電電力量[kWh]"),
                    soc_percent=float(soc) if soc is not None else None,
                )
            )
    return sorted(rows, key=lambda item: item.timestamp)


def summarize(rows: list[MonitorRow]) -> WindowSummary:
    soc_rows = [row for row in rows if row.soc_percent is not None]
    soc_min = min(((float(row.soc_percent), row.hhmm) for row in soc_rows), default=None)
    soc_max = max(((float(row.soc_percent), row.hhmm) for row in soc_rows), default=None)
    return WindowSummary(
        rows=len(rows),
        first_hhmm=rows[0].hhmm if rows else "",
        last_hhmm=rows[-1].hhmm if rows else "",
        pv_kwh=sum(row.pv_kwh for row in rows),
        load_kwh=sum(row.load_kwh for row in rows),
        sell_kwh=sum(row.sell_kwh for row in rows),
        buy_kwh=sum(row.buy_kwh for row in rows),
        charge_kwh=sum(row.charge_kwh for row in rows),
        discharge_kwh=sum(row.discharge_kwh for row in rows),
        soc_first=soc_rows[0].soc_percent if soc_rows else None,
        soc_last=soc_rows[-1].soc_percent if soc_rows else None,
        soc_min=soc_min,
        soc_max=soc_max,
    )


def _collect_csv_paths(run_dir: Path) -> list[Path]:
    summary_path = run_dir / "kpnet_summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        paths = [Path(str(item.get("path", ""))) for item in summary.get("csv_downloads", [])]
        existing = [path for path in paths if path.exists()]
        if existing:
            return existing
    return sorted((run_dir / "csv").glob("*.csv"))


def _latest_run_dir(artifacts_dir: Path) -> Path:
    candidates = [path for path in artifacts_dir.iterdir() if path.is_dir() and (path / "kpnet_summary.json").exists()]
    if not candidates:
        raise FileNotFoundError(f"KP-NET run directory was not found under {artifacts_dir}")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _load_plan(plan_date: str) -> dict[str, Any]:
    client = open_firestore()
    snap = client.collection("night_charge_plans").document(plan_date).get()
    if not snap.exists:
        raise RuntimeError(f"night_charge_plans/{plan_date} was not found")
    data = snap.to_dict() or {}
    plan_json = data.get("plan_json")
    if isinstance(plan_json, str) and plan_json.strip():
        try:
            data["_plan"] = json.loads(plan_json)
        except json.JSONDecodeError:
            data["_plan"] = {}
    return data


def _load_battery_metrics(plan_date: str) -> dict[str, Any]:
    client = open_firestore()
    snap = client.collection("battery_daily_metrics").document(plan_date).get()
    return snap.to_dict() or {} if snap.exists else {}


def _nested(data: dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(part)
    return cur if cur is not None else default


def _fmt_kwh(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.3f} kWh"


def _fmt_percent(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.1f}%"


def _money(value: float | int | None) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.1f}円"


def _hourly_forecast(plan_doc: dict[str, Any], hour: int, kind: str) -> float:
    result_key = "hourly_pv_forecast_kwh" if kind == "pv" else "hourly_load_forecast_kwh"
    hourly = _nested(plan_doc, f"_plan.result.{result_key}.{hour}", None)
    if hourly is None:
        hourly = _nested(plan_doc, f"result.{result_key}.{hour}", None)
    if hourly is None:
        hourly = _nested(plan_doc, f"_plan.daytime_soc_optimization.{result_key}.{hour}", None)
    if hourly is None:
        hourly = _nested(plan_doc, f"daytime_soc_optimization.{result_key}.{hour}", None)
    if hourly is None:
        forecast_key = "pv_kwh" if kind == "pv" else "consumption_kwh"
        hourly = _nested(plan_doc, f"_plan.forecast.hourly.{hour}.{forecast_key}", None)
    if hourly is None:
        forecast_key = "pv_kwh" if kind == "pv" else "consumption_kwh"
        hourly = _nested(plan_doc, f"forecast.hourly.{hour}.{forecast_key}", 0.0)
    try:
        return float(hourly or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _build_hourly_table(rows: list[MonitorRow], plan_doc: dict[str, Any]) -> list[str]:
    by_hour: dict[int, list[MonitorRow]] = {}
    for row in rows:
        by_hour.setdefault(row.timestamp.hour, []).append(row)

    lines = [
        "| 時 | PV予測 | PV実績 | 消費予測 | 消費実績 | 充電実績 | 買電実績 | 売電実績 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for hour in range(7, 23):
        actual = by_hour.get(hour, [])
        pv_actual = sum(row.pv_kwh for row in actual)
        load_actual = sum(row.load_kwh for row in actual)
        charge_actual = sum(row.charge_kwh for row in actual)
        buy_actual = sum(row.buy_kwh for row in actual)
        sell_actual = sum(row.sell_kwh for row in actual)
        lines.append(
            "| "
            f"{hour} | "
            f"{_hourly_forecast(plan_doc, hour, 'pv'):.3f} | "
            f"{pv_actual:.3f} | "
            f"{_hourly_forecast(plan_doc, hour, 'load'):.3f} | "
            f"{load_actual:.3f} | "
            f"{charge_actual:.3f} | "
            f"{buy_actual:.3f} | "
            f"{sell_actual:.3f} |"
        )
    return lines


def build_report(*, run_dir: Path, target_date: date, output_path: Path) -> Path:
    csv_paths = _collect_csv_paths(run_dir)
    if not csv_paths:
        raise FileNotFoundError(f"No CSV files were found in {run_dir}")

    all_rows = read_monitoring_rows(csv_paths)
    today_rows = [row for row in all_rows if row.day == target_date]
    prev_day = target_date - timedelta(days=1)
    night_rows = [
        row
        for row in all_rows
        if (row.day == prev_day and row.hhmm >= "23:00") or (row.day == target_date and row.hhmm <= "06:30")
    ]
    day_rows = [row for row in today_rows if "07:00" <= row.hhmm <= "22:30"]

    if not today_rows:
        raise RuntimeError(f"No rows were found for {target_date.isoformat()}")

    plan_doc = _load_plan(target_date.isoformat())
    metrics = _load_battery_metrics(target_date.isoformat())

    whole = summarize(today_rows)
    night = summarize(night_rows)
    daytime = summarize(day_rows)

    result = _nested(plan_doc, "_plan.result", {})
    if not result:
        result = _nested(plan_doc, "result", {})
    final_pv = result.get("final_predicted_pv_kwh", plan_doc.get("final_predicted_pv_kwh"))
    target_soc = result.get("target_soc_7_percent", metrics.get("setting_soc_target_percent"))
    predicted_night_charge = result.get("required_night_charge_kwh", metrics.get("night_charge_kwh"))
    expected_day_buy = result.get("soc_expected_day_buy_kwh")
    expected_sell = result.get("soc_expected_sell_kwh")
    peak_unmet = result.get("soc_expected_peak_unmet_kwh")
    peak_unmet_cost = result.get("soc_expected_peak_unmet_cost_yen")
    forecast_source = result.get("final_pv_forecast_source")
    pv_array_total = _nested(plan_doc, "pv_array_forecast_summary.totals.total_kwh")

    pv_gap = float(final_pv or 0.0) - whole.pv_kwh
    charge_gap = night.charge_kwh - float(predicted_night_charge or 0.0)

    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S %Z")
    lines = [
        f"# KP-NET SOC・予測充電量乖離レポート {target_date.isoformat()}",
        "",
        f"- 作成日時: {now}",
        f"- KP-NET実績取得run: `{run_dir}`",
        f"- 対象CSV: {', '.join(f'`{path}`' for path in csv_paths)}",
        f"- 実績データ範囲: {whole.first_hhmm} - {whole.last_hhmm}",
        "",
        "## 計画値",
        "",
        f"- 設定SOC: **{_fmt_percent(target_soc)}**",
        f"- 夜間充電予測量: **{_fmt_kwh(predicted_night_charge)}**",
        f"- 最終採用PV予測: **{_fmt_kwh(final_pv)}**",
        f"- 最終PV予測ソース: `{forecast_source or 'n/a'}`",
        f"- PV配列予測サマリ合計: {_fmt_kwh(pv_array_total)}",
        f"- 期待日中買電量: {_fmt_kwh(expected_day_buy)}",
        f"- 期待売電量: {_fmt_kwh(expected_sell)}",
        f"- 95%ピークSOC未達想定: {_fmt_kwh(peak_unmet)}",
        f"- 95%ピークSOC未達ペナルティ: {_money(peak_unmet_cost)}",
        "",
        "## 実績サマリ",
        "",
        "| 区間 | 行数 | 発電 | 消費 | 買電 | 売電 | 充電 | 放電 | SOC開始 | SOC終了 | SOC最小 | SOC最大 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        _summary_row("夜間 23:00-06:30", night),
        _summary_row("日中 07:00-22:30", daytime),
        _summary_row("当日取得分", whole),
        "",
        "## 主要な乖離",
        "",
        f"- 夜間充電量: 予測 {_fmt_kwh(predicted_night_charge)} / 実績 {_fmt_kwh(night.charge_kwh)} / 差分 {_fmt_kwh(charge_gap)}",
        f"- PV発電量: 予測 {_fmt_kwh(final_pv)} / 実績 {_fmt_kwh(whole.pv_kwh)} / 差分 {_fmt_kwh(-pv_gap)}",
        f"- 日中買電量: 予測 {_fmt_kwh(expected_day_buy)} / 実績 {_fmt_kwh(daytime.buy_kwh)}",
        f"- 売電量: 予測 {_fmt_kwh(expected_sell)} / 実績 {_fmt_kwh(whole.sell_kwh)}",
        f"- 日中最大SOC: {_fmt_percent(daytime.soc_max[0] if daytime.soc_max else None)}",
        "",
        "## 時間別比較",
        "",
        *_build_hourly_table(day_rows, plan_doc),
        "",
        "## 考察",
        "",
        _interpretation(target_soc, predicted_night_charge, night, whole, daytime, final_pv, pv_array_total),
        "",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def _summary_row(label: str, summary: WindowSummary) -> str:
    soc_min = f"{summary.soc_min[0]:.1f}% {summary.soc_min[1]}" if summary.soc_min else "n/a"
    soc_max = f"{summary.soc_max[0]:.1f}% {summary.soc_max[1]}" if summary.soc_max else "n/a"
    return (
        f"| {label} | {summary.rows} | {summary.pv_kwh:.3f} | {summary.load_kwh:.3f} | "
        f"{summary.buy_kwh:.3f} | {summary.sell_kwh:.3f} | {summary.charge_kwh:.3f} | "
        f"{summary.discharge_kwh:.3f} | {_fmt_percent(summary.soc_first)} | "
        f"{_fmt_percent(summary.soc_last)} | {soc_min} | {soc_max} |"
    )


def _interpretation(
    target_soc: Any,
    predicted_night_charge: Any,
    night: WindowSummary,
    whole: WindowSummary,
    daytime: WindowSummary,
    final_pv: Any,
    pv_array_total: Any,
) -> str:
    target = float(target_soc or 0.0)
    predicted_charge = float(predicted_night_charge or 0.0)
    final_pv_value = float(final_pv or 0.0)
    bullets: list[str] = []
    if target <= 5.0 and predicted_charge < 0.5:
        bullets.append(
            "設定SOCと夜間充電予測量はいずれも低く、計画時点で「夜間はほぼ充電しない」判断になっています。"
        )
    if night.soc_min and night.soc_min[0] <= 1.0:
        bullets.append(
            "夜間から早朝にSOCがほぼ0%まで低下しており、低SOC目標では早朝負荷に対する余裕が不足します。"
        )
    if final_pv_value > 0 and whole.pv_kwh < final_pv_value * 0.85:
        bullets.append(
            "当日取得分のPV実績が最終PV予測を大きく下回っており、低SOC判断の前提だった日中回復力が不足しています。"
        )
    if pv_array_total is not None:
        try:
            pv_array = float(pv_array_total)
            if pv_array > 0 and final_pv_value / pv_array >= 1.5:
                bullets.append(
                    "最終PV予測がPV配列予測サマリより大きく上振れており、物理PV予測の信頼度ガードやブレンド条件の検討余地があります。"
                )
        except (TypeError, ValueError):
            pass
    if daytime.soc_max and daytime.soc_max[0] < 95.0:
        bullets.append(
            "日中最大SOCが95%に届いていないため、ピークSOC未達は計画上の想定より大きかった可能性があります。"
        )
    if not bullets:
        bullets.append("大きな構造的乖離は検出されませんでした。")
    return "\n".join(f"- {item}" for item in bullets)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a KP-NET SOC gap report from downloaded CSV and Firestore plan.")
    parser.add_argument("--run-dir", type=Path, default=None, help="KP-NET artifact run directory")
    parser.add_argument("--artifacts-dir", type=Path, default=Path("artifacts"), help="Artifact root")
    parser.add_argument("--date", default=datetime.now(JST).date().isoformat(), help="Target date: YYYY-MM-DD")
    parser.add_argument("--output", type=Path, default=None, help="Output markdown path")
    args = parser.parse_args()

    target_date = date.fromisoformat(args.date)
    run_dir = args.run_dir or _latest_run_dir(args.artifacts_dir)
    output_path = args.output or run_dir / f"kpnet_soc_gap_report_{target_date.isoformat()}.md"
    report_path = build_report(run_dir=run_dir, target_date=target_date, output_path=output_path)
    print(f"[kpnet_soc_gap_report] wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
