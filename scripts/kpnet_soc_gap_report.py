from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.operations.firestore import open_firestore
from app.backup.night_plan_archive import load_night_plan_detail_from_firestore_doc
from app.parsing.numbers import parse_csv_float


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
    value = parse_csv_float(row.get(column, ""), default=None)
    if value is None or not math.isfinite(float(value)):
        raise ValueError(f"Missing or invalid monitoring energy: {column}")
    return float(value)


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
            try:
                energy = {
                    field: _float(row, column)
                    for field, column in (
                        ("pv_kwh", "発電電力量[kWh]"), ("load_kwh", "消費電力量[kWh]"),
                        ("sell_kwh", "売電電力量[kWh]"), ("buy_kwh", "買電電力量[kWh]"),
                        ("charge_kwh", "充電電力量[kWh]"), ("discharge_kwh", "放電電力量[kWh]"),
                    )
                }
            except ValueError:
                # A present row with blank energy is unavailable, not zero.
                continue
            rows.append(
                MonitorRow(
                    day=day,
                    hhmm=hhmm,
                    pv_kwh=energy["pv_kwh"],
                    load_kwh=energy["load_kwh"],
                    sell_kwh=energy["sell_kwh"],
                    buy_kwh=energy["buy_kwh"],
                    charge_kwh=energy["charge_kwh"],
                    discharge_kwh=energy["discharge_kwh"],
                    soc_percent=float(soc) if soc is not None else None,
                )
            )
    return sorted(rows, key=lambda item: item.timestamp)


def summarize(rows: list[MonitorRow]) -> WindowSummary:
    soc_rows = [row for row in rows if row.soc_percent is not None]
    soc_values = [(row.soc_percent, row.hhmm) for row in soc_rows if row.soc_percent is not None]
    soc_min = min(soc_values, default=None)
    soc_max = max(soc_values, default=None)
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
    try:
        data["_plan"] = load_night_plan_detail_from_firestore_doc(data) or {}
    except Exception:
        data["_plan"] = {}
    execution = client.collection("night_soc_execution").document(plan_date).get()
    data["night_soc_execution"] = execution.to_dict() or {} if execution.exists else {}
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


def _hourly_forecast(plan_doc: dict[str, Any], hour: int, kind: str) -> float | None:
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
        hourly = _nested(plan_doc, f"forecast.hourly.{hour}.{forecast_key}", None)
    try:
        value = float(hourly)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _complete_hours(rows: list[MonitorRow]) -> dict[int, list[MonitorRow]]:
    """KP-NET energy labels are interval starts; require both half-hours."""
    by_hour: dict[int, list[MonitorRow]] = {}
    for row in rows:
        by_hour.setdefault(row.timestamp.hour, []).append(row)
    return {
        hour: values for hour, values in by_hour.items()
        if len(values) == 2 and sorted(row.timestamp.minute for row in values) == [0, 30]
    }


def _matched_forecast(rows: list[MonitorRow], plan_doc: dict[str, Any], kind: str) -> float | None:
    values = [_hourly_forecast(plan_doc, hour, kind) for hour in _complete_hours(rows)]
    if not values or any(value is None for value in values):
        return None
    return sum(value for value in values if value is not None)


def _table_value(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def _build_hourly_table(rows: list[MonitorRow], plan_doc: dict[str, Any]) -> list[str]:
    by_hour = _complete_hours(rows)

    lines = [
        "| 時 | PV予測 | PV実績 | 消費予測 | 消費実績 | 充電実績 | 買電実績 | 売電実績 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for hour in range(7, 23):
        actual = by_hour.get(hour)
        values = [
            sum(getattr(row, field) for row in actual) if actual else None
            for field in ("pv_kwh", "load_kwh", "charge_kwh", "buy_kwh", "sell_kwh")
        ]
        lines.append(
            "| "
            f"{hour} | "
            f"{_table_value(_hourly_forecast(plan_doc, hour, 'pv'))} | "
            f"{_table_value(values[0])} | "
            f"{_table_value(_hourly_forecast(plan_doc, hour, 'load'))} | "
            + " | ".join(_table_value(value) for value in values[1:]) + " |"
        )
    return lines


# readable-code-audit: skip STRUCT-04 — report data loading and Markdown rendering use one run directory snapshot for reproducibility
def build_report(*, run_dir: Path, target_date: date, output_path: Path) -> Path:
    csv_paths = _collect_csv_paths(run_dir)
    if not csv_paths:
        raise FileNotFoundError(f"No CSV files were found in {run_dir}")

    all_rows = read_monitoring_rows(csv_paths)
    cutoff = datetime.now(JST)
    all_rows = [row for row in all_rows if row.timestamp + timedelta(minutes=30) <= cutoff]
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
    matched_day_rows = [row for values in _complete_hours(day_rows).values() for row in values]
    matched_daytime = summarize(sorted(matched_day_rows, key=lambda row: row.timestamp))
    matched_pv = _matched_forecast(day_rows, plan_doc, "pv")
    matched_load = _matched_forecast(day_rows, plan_doc, "load")
    complete_day = len(_complete_hours(day_rows)) == 16
    expected_night_starts = {
        datetime.combine(prev_day, time(23), tzinfo=JST) + timedelta(minutes=30 * index)
        for index in range(16)
    }
    complete_night = len(night_rows) == 16 and {row.timestamp for row in night_rows} == expected_night_starts

    result = _nested(plan_doc, "_plan.result", {})
    if not result:
        result = _nested(plan_doc, "result", {})
    final_pv = result.get("final_predicted_pv_kwh", plan_doc.get("final_predicted_pv_kwh"))
    target_soc = metrics.get("setting_soc_target_percent")
    if target_soc is None:
        target_soc = result.get("target_soc_7_percent")
    predicted_night_charge = metrics.get("night_charge_kwh")
    if predicted_night_charge is None:
        predicted_night_charge = result.get("required_night_charge_kwh")
    expected_day_buy = result.get("soc_expected_day_buy_kwh")
    expected_sell = result.get("soc_expected_sell_kwh")
    forecast_source = result.get("final_pv_forecast_source")
    pv_array_total = _nested(plan_doc, "pv_array_forecast_summary.totals.total_kwh")

    pv_gap = matched_daytime.pv_kwh - matched_pv if matched_pv is not None else None
    charge_gap = night.charge_kwh - float(predicted_night_charge) if complete_night and predicted_night_charge is not None else None

    execution = _nested(plan_doc, "night_soc_execution", {})
    if not isinstance(execution, dict) or not execution:
        execution = metrics.get("night_soc_execution", {})
    if not isinstance(execution, dict):
        execution = {}
    audit_status, audit_reasons, target_error = _night_soc_audit(
        target_soc=target_soc,
        night=night,
        daytime=daytime,
        execution=execution,
    )

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
        "",
        "## 夜間SOC監査",
        "",
        f"- 判定: **{audit_status}**",
        f"- 瞬時SOCの誤差（時刻検証済み証拠のみ）: {_fmt_percent(target_error)}",
        "- CSVの区間SOCを07:00の瞬時SOCとは扱いません。監査資料の欠損は制御失敗を意味しません。",
        f"- read-back一致: {execution.get('readback_match', '未記録')}",
        f"- 停止確認: {execution.get('standby_confirmed_at_utc', '未記録')}",
        f"- 競合書込み数: {execution.get('competing_writes', '未記録')}",
        f"- 観測鮮度秒: {execution.get('soc_observation_age_seconds', '未記録')}",
        f"- 理由: {', '.join(audit_reasons) if audit_reasons else 'なし'}",
        "",
        "## 実績サマリ",
        "",
        "| 区間 | 行数 | 発電 | 消費 | 買電 | 売電 | 充電 | 放電 | SOC開始 | SOC終了 | SOC最小 | SOC最大 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        _summary_row("夜間 23:00-06:30", night),
        _summary_row(
            f"日中 {daytime.first_hhmm or 'n/a'}-{daytime.last_hhmm or 'n/a'}",
            daytime,
        ),
        _summary_row("当日取得分", whole),
        "",
        "## 主要な乖離",
        "",
        "- 時間別の比較は:00/:30の両区間が揃った時間のみ。欠損・未完了はn/aです。",
        f"- 夜間充電量: 予測 {_fmt_kwh(predicted_night_charge if complete_night else None)} / 実績 {_fmt_kwh(night.charge_kwh if complete_night else None)} / 差分 {_fmt_kwh(charge_gap)}",
        f"- 日中PV発電量（完全区間）: 予測 {_fmt_kwh(matched_pv)} / 実績 {_fmt_kwh(matched_daytime.pv_kwh if matched_day_rows else None)} / 差分 {_fmt_kwh(pv_gap)}",
        f"- 日中消費量（完全区間）: 予測 {_fmt_kwh(matched_load)} / 実績 {_fmt_kwh(matched_daytime.load_kwh if matched_day_rows else None)}",
        f"- 全日中買電量（全区間取得時のみ）: 予測 {_fmt_kwh(expected_day_buy if complete_day else None)} / 実績 {_fmt_kwh(daytime.buy_kwh if complete_day else None)}",
        f"- 全日中売電量（全区間取得時のみ）: 予測 {_fmt_kwh(expected_sell if complete_day else None)} / 実績 {_fmt_kwh(daytime.sell_kwh if complete_day else None)}",
        "",
        "## 時間別比較",
        "",
        *_build_hourly_table(day_rows, plan_doc),
        "",
        "## 考察",
        "",
        _interpretation(
            target_soc,
            predicted_night_charge,
            night,
            matched_daytime,
            matched_pv,
            pv_array_total,
        ),
        "",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")
    return output_path


def _night_soc_audit(
    *,
    target_soc: Any,
    night: WindowSummary,
    daytime: WindowSummary,
    execution: dict[str, Any],
) -> tuple[str, list[str], float | None]:
    """Classify execution evidence separately from forecast/report availability."""
    reasons: list[str] = []
    try:
        target = float(target_soc)
    except (TypeError, ValueError):
        return "INCONCLUSIVE", ["target_soc_missing"], None
    if not math.isfinite(target) or not 0.0 <= target <= 100.0:
        return "INCONCLUSIVE", ["target_soc_invalid"], None
    # CSV energy labels identify interval starts; its SOC sample endpoint is
    # not a verified instantaneous 07:00 observation. Do not infer a missed
    # charging target from the first daytime CSV row. Exact realtime terminal
    # receipts require their own evidence path and are not loaded here.
    target_error = None
    reasons.append("instantaneous_soc_evidence_not_loaded")
    if execution.get("plan_id") is None:
        reasons.append("execution_record_missing")
    if execution.get("readback_match") is not True:
        reasons.append("readback_not_confirmed")
    if execution.get("standby_confirmed_at_utc") in {None, "", "未記録"}:
        reasons.append("standby_confirmation_missing")
    competing_writes = execution.get("competing_writes")
    if competing_writes is None:
        reasons.append("competing_write_count_missing")
    else:
        try:
            if int(competing_writes) > 0:
                reasons.append("competing_writes_detected")
        except (TypeError, ValueError):
            reasons.append("competing_write_count_invalid")
    if "competing_writes_detected" in reasons:
        return "FAIL", reasons, target_error
    return ("PASS" if not reasons else "INCONCLUSIVE"), reasons, target_error


def _summary_row(label: str, summary: WindowSummary) -> str:
    soc_min = f"{summary.soc_min[0]:.1f}% {summary.soc_min[1]}" if summary.soc_min else "n/a"
    soc_max = f"{summary.soc_max[0]:.1f}% {summary.soc_max[1]}" if summary.soc_max else "n/a"
    return (
        f"| {label} | {summary.rows} | {summary.pv_kwh:.3f} | {summary.load_kwh:.3f} | "
        f"{summary.buy_kwh:.3f} | {summary.sell_kwh:.3f} | {summary.charge_kwh:.3f} | "
        f"{summary.discharge_kwh:.3f} | {_fmt_percent(summary.soc_first)} | "
        f"{_fmt_percent(summary.soc_last)} | {soc_min} | {soc_max} |"
    )


# readable-code-audit: skip STRUCT-04 — each branch adds one independent report diagnosis, so keeping the decision table together makes every condition visible
def _interpretation(
    target_soc: Any,
    predicted_night_charge: Any,
    night: WindowSummary,
    daytime: WindowSummary,
    final_pv: Any,
    pv_array_total: Any,
) -> str:
    bullets = [
        "評価目的は電気料金の削減です。最大SOC・買電ゼロ・売電ゼロを成功条件にしません。",
        "予測誤差と制御結果は別に評価します。CSVの区間SOCだけから充電停止の成否を判定しません。",
    ]
    if final_pv is not None and daytime.rows:
        error = daytime.pv_kwh - float(final_pv)
        bullets.append(f"一致する完全区間でのPV実績−予測は{error:+.3f} kWhです。原因の特定には保存予報・負荷・充放電を別々に検証します。")
    else:
        bullets.append("PV予測と完全区間の比較資料が不足しているため、発電誤差の判定はINCONCLUSIVEです。")
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
