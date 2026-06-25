from __future__ import annotations

import json
import os
import base64
import hmac
import secrets
import time
import traceback
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app.dashboard_data import DashboardSlice, load_dashboard_slice


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _auth_enabled() -> bool:
    return bool(os.getenv("DASHBOARD_BASIC_USER", "").strip()) and bool(os.getenv("DASHBOARD_BASIC_PASSWORD", "").strip())


def _decode_auth_token(token: str) -> tuple[str, str] | None:
    raw = token.strip()
    if not raw:
        return None
    pad = "=" * (-len(raw) % 4)
    try:
        decoded = base64.urlsafe_b64decode((raw + pad).encode("utf-8")).decode("utf-8")
    except Exception:
        return None
    if ":" not in decoded:
        return None
    user, passwd = decoded.split(":", 1)
    return user, passwd


def _session_secret() -> bytes:
    explicit = os.getenv("DASHBOARD_SESSION_SECRET", "").strip()
    if explicit:
        return explicit.encode("utf-8")
    # Fallback keeps behavior deterministic even when secret is not explicitly set.
    return f"{os.getenv('DASHBOARD_BASIC_USER', '')}:{os.getenv('DASHBOARD_BASIC_PASSWORD', '')}".encode("utf-8")


def _sign_session(expire_unix: int) -> str:
    msg = str(expire_unix).encode("utf-8")
    sig = hmac.new(_session_secret(), msg, "sha256").digest()
    return base64.urlsafe_b64encode(sig).decode("utf-8").rstrip("=")


def _verify_session(token: str) -> bool:
    if "." not in token:
        return False
    exp_str, got_sig = token.split(".", 1)
    if not exp_str.isdigit():
        return False
    exp = int(exp_str)
    if exp <= int(time.time()):
        return False
    expected = _sign_session(exp)
    return hmac.compare_digest(got_sig, expected)


def _empty_dashboard_payload() -> dict:
    return {
        "sunshine_daily": [],
        "forecast_hourly": [],
        "energy_daily": [],
        "cost_daily": [],
        "cost_monthly": [],
        "battery_daily": [],
        "battery_flow_daily": [],
        "model_parameters": [],
        "latest_schedule": {},
        "dashboard_warnings": [],
        "pv_forecast_diagnostics": {},
        "meta": {
            "window_days": 31,
            "oldest_loaded_date": None,
            "newest_loaded_date": None,
            "global_oldest_date": None,
            "global_newest_date": None,
            "has_more_before": False,
        },
    }


def _html(payload: dict, script_nonce: str) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return """<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>おうち発電ダッシュボード</title>
  <script nonce="__NONCE__" src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <style>
    :root {
      --bg-1: #eef7ff;
      --bg-2: #fefcf7;
      --panel: #ffffff;
      --ink: #16314f;
      --sub: #5a6f85;
      --line: #d8e6f2;
      --blue: #147efb;
      --green: #14b86f;
      --orange: #ef8e1d;
      --red: #e6504f;
    }
    body {
      margin: 0;
      color: var(--ink);
      font-family: "BIZ UDPGothic", "Noto Sans JP", "Yu Gothic UI", sans-serif;
      -webkit-text-size-adjust: 100%;
      background:
        radial-gradient(circle at 12% -20%, #dcefff 0%, transparent 40%),
        radial-gradient(circle at 95% 0%, #fff4d6 0%, transparent 45%),
        linear-gradient(180deg, var(--bg-1), var(--bg-2));
    }
    .wrap { max-width: min(1360px, 100vw); margin: 0 auto; padding: clamp(8px, 2.5vw, 14px); box-sizing: border-box; }
    .hero {
      border: 1px solid var(--line);
      border-radius: 16px;
      background: linear-gradient(130deg, #f6fbff, #fffdf7);
      padding: 16px 18px;
      margin-bottom: 14px;
      box-sizing: border-box;
    }
    .hero h1 { margin: 0; font-size: 22px; letter-spacing: 0.02em; }
    .hero p { margin: 6px 0 0; color: var(--sub); font-size: 13px; }
    .grid { display: grid; gap: 12px; grid-template-columns: repeat(2, minmax(0, 1fr)); }
    .card {
      border: 1px solid var(--line);
      border-radius: 14px;
      background: var(--panel);
      padding: 12px;
      box-shadow: 0 6px 16px rgba(13, 49, 88, 0.07);
      box-sizing: border-box;
      min-width: 0;
    }
    .full { grid-column: 1 / -1; }
    .card h2 { margin: 0 0 6px; font-size: 16px; }
    .desc { margin: 0 0 10px; color: var(--sub); font-size: 12px; line-height: 1.5; }
    .chart-box { position: relative; width: 100%; min-width: 0; height: 300px; }
    .battery-life-card { display: grid; gap: 8px; }
    .battery-life-kpis {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 6px;
    }
    .battery-life-kpi {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      gap: 5px;
      min-height: 32px;
      padding: 7px 8px;
      border: 1px solid #e3edf6;
      border-radius: 11px;
      background: linear-gradient(180deg, #f8fbff, #ffffff);
      box-sizing: border-box;
    }
    .battery-life-label {
      color: #7a8da1;
      font-size: 10px;
      font-weight: 700;
      line-height: 1;
      white-space: nowrap;
    }
    .battery-life-value {
      color: #16314f;
      font-size: 14px;
      font-weight: 900;
      line-height: 1;
      letter-spacing: -0.02em;
      text-align: right;
      white-space: nowrap;
    }
    .battery-life-value small {
      color: #5a6f85;
      font-size: 10px;
      font-weight: 800;
      margin-left: 3px;
    }
    .battery-life-chart { height: 210px; }
    .gantt-wrap { width: 100%; overflow-x: auto; border: 1px solid var(--line); border-radius: 12px; background: #f8fbff; }
    .gantt-board { min-width: 980px; padding: 10px; }
    .gantt-axis {
      display: grid;
      grid-template-columns: 150px 1fr;
      align-items: center;
      margin-bottom: 6px;
      color: #4f667f;
      font-size: 11px;
      font-weight: 700;
    }
    .gantt-hours {
      position: relative;
      height: 18px;
      border-bottom: 1px solid #dae8f5;
      background-image: repeating-linear-gradient(to right, transparent 0, transparent calc(100% / 24 - 1px), #dce8f3 calc(100% / 24 - 1px), #dce8f3 calc(100% / 24));
    }
    .gantt-hours span {
      position: absolute;
      transform: translateX(-50%);
      top: 0;
      font-size: 10px;
      color: #5a6f85;
      white-space: nowrap;
    }
    .gantt-row {
      display: grid;
      grid-template-columns: 150px 1fr;
      align-items: center;
      min-height: 48px;
      border-bottom: 1px dashed #d9e5f1;
      gap: 8px;
      padding: 6px 0;
    }
    .gantt-row:last-child { border-bottom: 0; }
    .gantt-label { font-size: 13px; font-weight: 700; color: #20415f; padding-left: 2px; }
    .gantt-track {
      position: relative;
      height: 38px;
      border: 1px solid #d4e3f1;
      border-radius: 8px;
      background: #fff;
      overflow: hidden;
      background-image: repeating-linear-gradient(to right, transparent 0, transparent calc(100% / 24 - 1px), #edf3f8 calc(100% / 24 - 1px), #edf3f8 calc(100% / 24));
    }
    .gantt-bar {
      position: absolute;
      top: 6px;
      height: 26px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 700;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      white-space: nowrap;
      text-overflow: ellipsis;
      border: 1px solid rgba(0,0,0,0.08);
      padding: 0 6px;
      box-sizing: border-box;
    }
    .bar-plan-night { background: #9ed8f4; color: #0d3d66; }
    .bar-plan-day { background: #ffe08a; color: #704d00; }
    .bar-forecast { background: #cdeccf; color: #1e5c2f; }
    .bar-fixed-stop { background: #ffe2e2; color: #8f2b2b; }
    .bar-fixed-free { background: #dff3e8; color: #1f6b45; }
    .gantt-notes { margin-top: 8px; color: #5a6f85; font-size: 12px; line-height: 1.5; }
    .gantt-notes code { background: #eef4fb; padding: 1px 5px; border-radius: 5px; }
    .gantt-status-icon {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 2px 7px;
      border-radius: 999px;
      font-size: 11px;
      font-weight: 800;
      margin-right: 6px;
      border: 1px solid transparent;
    }
    .gantt-status-icon.ok { background: #e6f6ec; color: #17613a; border-color: #bce4cb; }
    .gantt-status-icon.warn { background: #fff4df; color: #825100; border-color: #f2d297; }
    .warning-panel {
      display: none;
      border: 1px solid #f0d6a8;
      border-radius: 14px;
      background: #fff9ec;
      padding: 10px 12px;
      margin-bottom: 14px;
      box-sizing: border-box;
    }
    .warning-panel.active { display: block; }
    .warning-list { display: grid; gap: 8px; margin: 0; padding: 0; list-style: none; }
    .warning-item {
      border-left: 5px solid #ef8e1d;
      background: rgba(255,255,255,0.72);
      border-radius: 10px;
      padding: 8px 10px;
    }
    .warning-item.danger { border-left-color: var(--red); }
    .warning-item.info { border-left-color: var(--blue); }
    .warning-title { font-weight: 800; margin-right: 8px; }
    .warning-message { color: var(--sub); font-size: 12px; line-height: 1.5; }
    .period-panel { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
    .period-button {
      appearance: none;
      border: 1px solid var(--line);
      border-radius: 999px;
      background: #f8fbff;
      color: var(--ink);
      padding: 8px 12px;
      font-weight: 700;
      cursor: pointer;
      transition: background 120ms ease, border-color 120ms ease, transform 120ms ease;
    }
    .period-button:hover:not(:disabled) { transform: translateY(-1px); border-color: #9cc8ed; }
    .period-button.active { background: #16314f; border-color: #16314f; color: #fff; }
    .period-button:disabled { cursor: not-allowed; color: #a1afbd; background: #f1f5f9; }
    .period-label { color: var(--sub); font-size: 13px; font-weight: 700; margin-left: 4px; }
    canvas { display: block; width: 100% !important; height: 100% !important; }
    .equation {
      background: #f6fbff;
      border: 1px solid #dce9f4;
      border-radius: 10px;
      padding: 10px;
      margin-bottom: 10px;
      color: #35506a;
      line-height: 1.6;
      font-size: 13px;
    }
    table { width: 100%; border-collapse: collapse; font-size: 13px; table-layout: fixed; }
    th, td { padding: 7px 5px; border-bottom: 1px solid #eaf0f6; text-align: left; overflow-wrap: anywhere; word-break: break-word; }
    th { background: #f8fbff; }
    .diag-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: 10px 0 14px; }
    .diag-kpi { border: 1px solid var(--line); border-radius: 8px; padding: 10px; background: #fbfdff; }
    .diag-label { display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
    .diag-value { font-weight: 700; font-size: 15px; overflow-wrap: anywhere; }
    .diag-path { display: flex; flex-wrap: wrap; gap: 6px; margin: 10px 0; }
    .diag-chip { border: 1px solid #d8e6f2; border-radius: 999px; padding: 4px 8px; background: #f8fbff; font-size: 12px; }
    @media (max-width: 900px) {
      .grid { grid-template-columns: 1fr; }
      .full { grid-column: auto; }
      .chart-box { height: 240px; }
      .battery-life-kpis { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .diag-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .battery-life-chart { height: 220px; }
      .hero h1 { font-size: 19px; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>おうち発電ダッシュボード</h1>
      <p>期間ボタンで、日次・月次・蓄電池グラフの表示範囲をまとめて切り替えます。</p>
      <p id="statusMsg" class="desc"></p>
    </section>

    <section id="dashboardWarnings" class="warning-panel" aria-live="polite"></section>

    <section class="grid">
      <article class="card full">
        <h2>0. 制約管理ガントチャート（最新設定）</h2>
        <p class="desc">固定制約と最新設定の時間帯を重ねて表示します。時間別の日中予測は下の折れ線グラフに分離しています。</p>
        <div class="gantt-wrap"><div id="constraintGantt" class="gantt-board"></div></div>
      </article>

      <article class="card full">
        <h2>0.1 時間別予測（最新計画）</h2>
        <p id="hourlyForecastNote" class="desc">1時間ごとの予想発電量・予想充電量・予想消費電量を表示します。</p>
        <div class="chart-box"><canvas id="hourlyForecastChart"></canvas></div>
      </article>

      <article class="card full">
        <h2>0.2 PV予測診断（最新計画）</h2>
        <p id="pvForecastDiagNote" class="desc">予測候補、採用経路、係数の状態を表示します。</p>
        <div class="diag-grid" aria-label="PV予測診断サマリー">
          <div class="diag-kpi"><span class="diag-label">採用モデル</span><span id="pvDiagMethod" class="diag-value">-</span></div>
          <div class="diag-kpi"><span class="diag-label">計画日</span><span id="pvDiagDate" class="diag-value">-</span></div>
          <div class="diag-kpi"><span class="diag-label">global scale</span><span id="pvDiagScale" class="diag-value">-</span></div>
          <div class="diag-kpi"><span class="diag-label">データ状態</span><span id="pvDiagQuality" class="diag-value">-</span></div>
        </div>
        <div id="pvDiagPath" class="diag-path" aria-label="PV予測判断経路"></div>
        <table id="pvCandidateTable">
          <thead><tr><th>候補</th><th>合計kWh</th><th>状態</th><th>備考</th></tr></thead>
          <tbody></tbody>
        </table>
      </article>

      <article class="card full">
        <h2>表示期間</h2>
        <p class="desc">集計月は「前月15日〜当月14日」を当月分として扱います。締め日は `DASHBOARD_AGGREGATION_CLOSE_DAY` で変更できます。</p>
        <div class="period-panel" aria-label="表示期間切り替え">
          <button id="periodMonthBtn" class="period-button" type="button">1ヶ月(日)</button>
          <button id="periodYearBtn" class="period-button" type="button">年(週)</button>
          <button id="periodAllBtn" class="period-button" type="button">全て(日)</button>
          <button id="periodPrevBtn" class="period-button" type="button">前</button>
          <button id="periodNextBtn" class="period-button" type="button">後</button>
          <span id="periodLabel" class="period-label"></span>
        </div>
      </article>

      <article class="card">
        <h2>1.1 日照時間（予測と実績）</h2>
        <p class="desc">青: 予測、緑: 実績、橙: 差分。差分は実績 - 予測です。</p>
        <div class="chart-box"><canvas id="sunChart"></canvas></div>
      </article>

      <article class="card">
        <h2>1.2 発電量（予測と実績）</h2>
        <p class="desc">青: 予測、緑: 実績、橙: 差分。差分は実績 - 予測です。</p>
        <div class="chart-box"><canvas id="pvChart"></canvas></div>
      </article>

      <article class="card">
        <h2>1.3 消費量（予測と実績）</h2>
        <p class="desc">青: 予測、緑: 実績、橙: 差分。差分は実績 - 予測です。</p>
        <div class="chart-box"><canvas id="loadChart"></canvas></div>
      </article>

      <article class="card">
        <h2 id="dailyKwhTitle">2. 自家消費kWh（日）</h2>
        <p id="dailyKwhDesc" class="desc">青の棒: 日次自家消費、緑の折れ線: 累計自家消費。左右で縦軸を分けます。</p>
        <div class="chart-box"><canvas id="dailyKwhChart"></canvas></div>
      </article>

      <article class="card">
        <h2 id="dailyYenTitle">3. 節約額（日）</h2>
        <p id="dailyYenDesc" class="desc">橙の棒: 日次節約額、赤の折れ線: 累計節約額。左右で縦軸を分けます。</p>
        <div class="chart-box"><canvas id="dailyYenChart"></canvas></div>
      </article>

      <article class="card">
        <h2>4. 自家消費と節約額（月）</h2>
        <p class="desc">月単位の合計。左軸(kWh)、右軸(円)。</p>
        <div class="chart-box"><canvas id="monthlyCostChart"></canvas></div>
      </article>

      <article class="card">
        <h2 id="batteryTitle">5. 蓄電池計画値と実績（日次）</h2>
        <p id="batteryDesc" class="desc">左軸はkWh/日、右軸はSOC(%)。夜間充電・PV蓄電余力は「日次の計画/予測値」で、瞬時充電電力(kW)ではありません。</p>
        <div class="chart-box"><canvas id="batteryChart"></canvas></div>
      </article>

      <article class="card">
        <h2>6. 蓄電池容量推定（実績）</h2>
        <p class="desc">表示期間の実測充放電量と実績気温から、月別の推定容量内訳を表示します。未来予測ではなく、制御には使わない可視化用の推定です。</p>
        <div class="battery-life-card">
          <div class="battery-life-kpis" aria-label="蓄電池容量予測サマリー">
            <div class="battery-life-kpi"><span class="battery-life-label">累計充電</span><span id="lifeTotalCharge" class="battery-life-value">-</span></div>
            <div class="battery-life-kpi"><span class="battery-life-label">累計放電</span><span id="lifeTotalDischarge" class="battery-life-value">-</span></div>
            <div class="battery-life-kpi"><span class="battery-life-label">等価サイクル</span><span id="lifeEquivalentCycles" class="battery-life-value">-</span></div>
            <div class="battery-life-kpi"><span class="battery-life-label">推定容量</span><span id="lifeEstimatedCapacity" class="battery-life-value">-</span></div>
            <div class="battery-life-kpi"><span class="battery-life-label">80%到達予測</span><span id="lifeEightyPercentYear" class="battery-life-value">-</span></div>
            <div class="battery-life-kpi"><span class="battery-life-label">月平均Eqサイクル</span><span id="lifeMonthlyCycles" class="battery-life-value">-</span></div>
          </div>
          <div class="chart-box battery-life-chart"><canvas id="batteryLifeChart"></canvas></div>
        </div>
      </article>

      <article class="card full">
        <h2>7. 蓄電池方程式とパラメータ</h2>
        <div class="equation">
          変数: <b>GTI</b>=面別傾斜面日射量, <b>TP</b>=気温[℃], <b>LD</b>=日中負荷[kWh], <b>RM</b>=朝負荷[kWh], <b>RS</b>=朝SOC[%], <b>RC</b>=目標SOC[%], <b>NC</b>=夜間充電[kWh], <b>PS</b>=日中余剰PV[kWh]<br>
          (1) PV予測: <b>PV(t) = Σ array(capacity × GTI(t)/1000 × PR × 補正係数 × 温度補正)</b><br>
          (2) 朝不足: <b>DF = max(0, RM - PV<sub>07-10</sub>)</b><br>
          (3) 日中余剰: <b>PS = max(0, PV<sub>10-16</sub> - 推定昼負荷)</b><br>
          (4) 7時目標SOC: <b>RC = clip(Rsv + (DF - PS) / Cp × 100, 0, 100)</b><br>
          (5) 夜間充電量: <b>NC = max(0, ((RC - RS)/100 × Cp) / Ef)</b><br>
          条件A: 23-07は放電禁止、07-23は放電許可。 条件B: 03ジョブが07:00カットオフで強制充電開始を逆算。 条件C: 充電開始は00:00未満にしない。<br>
          条件管理: <b>config/operation_conditions.json</b>（fixed=固定条件、variable=変動条件、priority=優先順位）<br>
          最優先固定条件: <b>0時跨ぎ禁止</b> / <b>開始=終了禁止</b><br>
        </div>
        <div class="equation" id="pvDeveloperSummary">
          PV物理予測: 診断データ未生成
        </div>
        <table id="pvDeveloperTable">
          <thead>
            <tr><th>分類</th><th>変数</th><th>値</th><th>意味</th></tr>
          </thead>
          <tbody></tbody>
        </table>
        <table id="paramsTable">
          <thead>
            <tr><th>短縮</th><th>パラメータ</th><th>意味</th><th>中心値</th><th>分散</th><th>サンプル数</th></tr>
          </thead>
          <tbody></tbody>
        </table>
      </article>
    </section>
  </div>

  <script nonce="__NONCE__">
    window.__DASHBOARD_DATA__ = __DASHBOARD_DATA_PLACEHOLDER__;

    const WINDOW_DAYS = 31;
    const CHUNK_DAYS = 120;
    const DEFAULT_AGGREGATION_CLOSE_DAY = 14;

    function n(v, d = 0) {
      const x = Number(v);
      if (!Number.isFinite(x)) return d;
      return x;
    }

    function niceCeil(v) {
      if (v <= 1) return 1;
      const p = Math.pow(10, Math.floor(Math.log10(v)));
      const m = v / p;
      const c = m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10;
      return c * p;
    }

    function maxAbs(values) {
      let m = 0;
      for (const v of values) {
        const x = Math.abs(n(v));
        if (x > m) m = x;
      }
      return m;
    }

    function maxPos(values) {
      let m = 0;
      for (const v of values) {
        const x = n(v);
        if (x > m) m = x;
      }
      return m;
    }

    function formatChartValue(value) {
      const x = n(value);
      if (Math.abs(x) >= 100) return Math.round(x).toString();
      if (Math.abs(x) >= 10) return x.toFixed(1);
      return x.toFixed(2);
    }

    function dualScales(leftValues, rightValues, options = {}) {
      const rightCanBeNegative = !!options.rightCanBeNegative;
      const leftUnit = options.leftUnit || "";
      const rightUnit = options.rightUnit || "";
      const tickCount = 6;
      const intervals = tickCount - 1;
      const leftRawMax = Math.max(1, maxPos(leftValues));
      const leftStep = Math.max(0.1, niceCeil(leftRawMax / intervals));
      const leftMax = leftStep * intervals;
      let rightMin = 0;
      let rightStep = Math.max(1, niceCeil(Math.max(1, maxPos(rightValues)) / intervals));
      let rightMax = rightStep * intervals;
      if (rightCanBeNegative) {
        const absMax = Math.max(1, maxAbs(rightValues));
        rightStep = Math.max(0.1, niceCeil(absMax / (intervals / 2)));
        rightMax = rightStep * (intervals / 2);
        rightMin = -rightMax;
      }
      return {
        y: {
          min: 0,
          max: leftMax,
          ticks: { count: tickCount, stepSize: leftStep, callback: (v) => `${v}${leftUnit}` },
          title: { display: !!leftUnit, text: leftUnit.replace(/[()]/g, "") },
          grid: { color: "#d8e6f2" },
        },
        y2: {
          min: rightMin,
          max: rightMax,
          position: "right",
          ticks: { count: tickCount, stepSize: rightStep, callback: (v) => `${v}${rightUnit}` },
          title: { display: !!rightUnit, text: rightUnit.replace(/[()]/g, "") },
          grid: { drawOnChartArea: false },
        },
      };
    }

    function setStatus(msg, color = "#5a6f85") {
      const el = document.getElementById("statusMsg");
      if (!el) return;
      el.textContent = msg || "";
      el.style.color = color;
    }

    function isoDateAdd(dateStr, deltaDays) {
      const m = /^([0-9]{4})-([0-9]{2})-([0-9]{2})$/.exec(String(dateStr || ""));
      if (!m) return dateStr;
      const y = Number(m[1]);
      const mo = Number(m[2]);
      const da = Number(m[3]);
      const base = new Date(Date.UTC(y, mo - 1, da));
      if (Number.isNaN(base.getTime())) return dateStr;
      base.setUTCDate(base.getUTCDate() + deltaDays);
      const yy = base.getUTCFullYear();
      const mm = String(base.getUTCMonth() + 1).padStart(2, "0");
      const dd = String(base.getUTCDate()).padStart(2, "0");
      return `${yy}-${mm}-${dd}`;
    }

    function buildDateRange(startDate, endDate) {
      const out = [];
      let cur = startDate;
      for (let i = 0; i < 5000; i += 1) {
        out.push(cur);
        if (cur >= endDate) break;
        cur = isoDateAdd(cur, 1);
      }
      return out;
    }

    function isoParts(dateStr) {
      const m = /^([0-9]{4})-([0-9]{2})-([0-9]{2})$/.exec(String(dateStr || ""));
      if (!m) return null;
      return { year: Number(m[1]), month: Number(m[2]), day: Number(m[3]) };
    }

    function isoFromParts(year, month, day) {
      return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    }

    function addMonthsParts(year, month, delta) {
      const index = year * 12 + (month - 1) + delta;
      return { year: Math.floor(index / 12), month: (index % 12) + 1 };
    }

    function daysInMonth(year, month) {
      return new Date(Date.UTC(year, month, 0)).getUTCDate();
    }

    function aggregationCloseDay() {
      const raw = Number(store.meta && store.meta.aggregation_close_day);
      if (Number.isFinite(raw)) return Math.max(1, Math.min(31, Math.round(raw)));
      return DEFAULT_AGGREGATION_CLOSE_DAY;
    }

    function effectiveCloseDay(year, month) {
      return Math.min(aggregationCloseDay(), daysInMonth(year, month));
    }

    function accountingMonthLabel(dateStr) {
      const p = isoParts(dateStr);
      if (!p) return null;
      let year = p.year;
      let month = p.month;
      if (p.day > effectiveCloseDay(year, month)) {
        const next = addMonthsParts(year, month, 1);
        year = next.year;
        month = next.month;
      }
      return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}`;
    }

    function accountingMonthBounds(monthLabel) {
      const p = isoParts(`${monthLabel}-01`);
      if (!p) return null;
      const end = isoFromParts(p.year, p.month, effectiveCloseDay(p.year, p.month));
      const prev = addMonthsParts(p.year, p.month, -1);
      const prevEnd = isoFromParts(prev.year, prev.month, effectiveCloseDay(prev.year, prev.month));
      return { start: isoDateAdd(prevEnd, 1), end };
    }

    function accountingYearBounds(year) {
      const start = accountingMonthBounds(`${String(year).padStart(4, "0")}-01`);
      const end = accountingMonthBounds(`${String(year).padStart(4, "0")}-12`);
      if (!start || !end) return null;
      return { start: start.start, end: end.end };
    }

    function qs(params) {
      const p = new URLSearchParams();
      for (const [k, v] of Object.entries(params)) {
        if (v === undefined || v === null || v === "") continue;
        p.set(k, String(v));
      }
      return p.toString();
    }

    function todayIsoJst() {
      const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: "Asia/Tokyo",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }).formatToParts(new Date());
      let y = "1970";
      let m = "01";
      let d = "01";
      for (const p of parts) {
        if (p.type === "year") y = p.value;
        if (p.type === "month") m = p.value.padStart(2, "0");
        if (p.type === "day") d = p.value.padStart(2, "0");
      }
      return `${y}-${m}-${d}`;
    }

    const store = {
      meta: null,
      sunshine: new Map(),
      hourly: new Map(),
      energy: new Map(),
      cost: new Map(),
      battery: new Map(),
      batteryFlow: new Map(),
      monthly: [],
      params: [],
      latestSchedule: null,
      dashboardWarnings: [],
      pvForecastDiagnostics: {},
      dates: [],
      loadingOlder: false,
    };

    const periodState = {
      mode: "all",
      month: null,
      year: null,
      initialized: false,
    };

    const charts = {};
    const paramAlias = {
      soc_per_kwh_charge: { code: "Cg", label: "充電1kWhあたりSOC上昇率" },
      soc_per_kwh_discharge: { code: "Dg", label: "放電1kWhあたりSOC低下率" },
      soc_drift_per_slot: { code: "Sd", label: "30分ごとのSOC自然変動" },
      battery_round_trip_efficiency: { code: "Ef", label: "蓄電池の往復効率" },
      battery_usable_capacity_kwh: { code: "Cp", label: "実効蓄電容量[kWh]" },
      pv_kwh_per_sunhour: { code: "Kp", label: "日照1時間あたりPV発電量[kWh]" },
      pv_temp_coeff_per_deg: { code: "Kt", label: "気温1℃あたりPV補正係数" },
      pv_direct_use_ratio: { code: "Kr", label: "朝のPV直接利用率" },
      pv_to_battery_ratio: { code: "Ks", label: "余剰PVの蓄電寄与率" },
      pv_self_consumption_ratio: { code: "Sc", label: "PV自家消費率" },
      pv_array_calibration_factor: { code: "Pa", label: "面別PV予測の実績補正係数" },
      pv_array_total_capacity_kw: { code: "Pc", label: "面別PV設定の合計容量[kW]" },
      pv_forecast_error_ratio_mean: { code: "Pem", label: "PV予測倍率の中心値（確率ではない）" },
      pv_forecast_error_ratio_std: { code: "Pes", label: "PV予測倍率のばらつき（確率ではない）" },
      pv_forecast_error_ratio_variance: { code: "Pev", label: "PV予測倍率の分散" },
      pv_forecast_error_ratio_sample_count: { code: "Pen", label: "PV予測誤差サンプル数" },
      physical_pv_radiation_scale: { code: "Prs", label: "短波放射からPVへ変換する物理モデル係数" },
      physical_pv_global_bias_scale: { code: "Pbs", label: "物理PV予測の全体バイアス補正係数" },
      battery_temp_coeff_per_deg: { code: "Bt", label: "気温1℃あたり蓄電容量補正係数" },
      battery_cycle_capacity_fade_per_cycle: { code: "Cf", label: "1サイクルあたり容量劣化率" },
    };

    function mergeRows(map, rows) {
      for (const row of rows || []) {
        if (!row || !row.date) continue;
        map.set(String(row.date), row);
      }
    }

    function mergeHourlyRows(rows) {
      for (const row of rows || []) {
        if (!row || !row.date) continue;
        const date = String(row.date);
        const bucket = store.hourly.get(date) || [];
        const hour = Number(row.hour);
        const next = bucket.filter((x) => Number(x.hour) !== hour);
        next.push(row);
        next.sort((a, b) => Number(a.hour) - Number(b.hour));
        store.hourly.set(date, next);
      }
    }

    function displayCutoffDate() {
      const today = todayIsoJst();
      const scheduleDate = store.latestSchedule && store.latestSchedule.plan_date
        ? String(store.latestSchedule.plan_date)
        : "";
      const hasScheduleForecast =
        scheduleDate &&
        (store.sunshine.has(scheduleDate) ||
          store.hourly.has(scheduleDate) ||
          store.energy.has(scheduleDate) ||
          store.battery.has(scheduleDate));
      return hasScheduleForecast && scheduleDate > today ? scheduleDate : today;
    }

    function rebuildDateIndex() {
      const all = new Set();
      for (const k of store.sunshine.keys()) all.add(k);
      for (const k of store.hourly.keys()) all.add(k);
      for (const k of store.energy.keys()) all.add(k);
      for (const k of store.cost.keys()) all.add(k);
      for (const k of store.battery.keys()) all.add(k);
      for (const k of store.batteryFlow.keys()) all.add(k);
      const cutoff = displayCutoffDate();
      store.dates = Array.from(all).filter((d) => d <= cutoff).sort();
    }

    async function fetchSlice(options = {}) {
      const query = qs({
        window_days: options.window_days ?? WINDOW_DAYS,
        end_date: options.end_date ?? "",
        include_static: options.include_static ? "1" : "0",
      });
      const res = await fetch(`/api/dashboard?${query}`, { credentials: "include" });
      if (!res.ok) {
        throw new Error(`api_error_${res.status}`);
      }
      return await res.json();
    }

    function absorbSlice(payload, includeStatic) {
      mergeRows(store.sunshine, payload.sunshine_daily || []);
      mergeHourlyRows(payload.forecast_hourly || []);
      mergeRows(store.energy, payload.energy_daily || []);
      mergeRows(store.cost, payload.cost_daily || []);
      mergeRows(store.battery, payload.battery_daily || []);
      mergeRows(store.batteryFlow, payload.battery_flow_daily || []);
      if (includeStatic) {
        store.monthly = payload.cost_monthly || [];
        store.params = payload.model_parameters || [];
        store.latestSchedule = payload.latest_schedule || store.latestSchedule;
        store.dashboardWarnings = payload.dashboard_warnings || [];
        store.pvForecastDiagnostics = payload.pv_forecast_diagnostics || {};
      }
      store.meta = payload.meta || store.meta;
      rebuildDateIndex();
    }

    function minuteOf(hhmm, fallback) {
      const m = /^([0-9]{1,2}):([0-9]{2})$/.exec(String(hhmm || ""));
      if (!m) return fallback;
      const h = Number(m[1]);
      const mm = Number(m[2]);
      if (!Number.isFinite(h) || !Number.isFinite(mm) || h < 0 || h > 23 || mm < 0 || mm > 59) return fallback;
      return h * 60 + mm;
    }

    function pushRange(segments, startMin, endMin, cssClass, label) {
      if (startMin == null || endMin == null) return;
      if (startMin === endMin) return;
      if (endMin > startMin) {
        segments.push({ start: startMin, end: endMin, cssClass, label });
        return;
      }
      segments.push({ start: startMin, end: 1440, cssClass, label });
      segments.push({ start: 0, end: endMin, cssClass, label });
    }

    function renderConstraintGantt() {
      const root = document.getElementById("constraintGantt");
      if (!root) return;
      const sch = store.latestSchedule || {};
      const chargeStart = minuteOf(sch.charge_start_time, null);
      const chargeEnd = minuteOf(sch.charge_end_time, minuteOf("07:00", 420));
      const dayStart = minuteOf(sch.day_discharge_window_start, minuteOf("07:00", 420));
      const dayEnd = minuteOf(sch.day_discharge_window_end, minuteOf("23:00", 1380));
      const nightStart = minuteOf(sch.night_window_start, minuteOf("23:00", 1380));
      const nightEnd = minuteOf(sch.night_window_end, minuteOf("07:00", 420));

      const planSegments = [];
      if (chargeStart != null && chargeEnd != null) {
        pushRange(
          planSegments,
          chargeStart,
          chargeEnd,
          "bar-plan-night",
          `夜間充電 ${sch.charge_start_time || "--:--"}-${sch.charge_end_time || "--:--"}`
        );
      }
      pushRange(
        planSegments,
        dayStart,
        dayEnd,
        "bar-plan-day",
        `日中放電 ${sch.day_discharge_window_start || "07:00"}-${sch.day_discharge_window_end || "23:00"}`
      );

      const fixedSegments = [];
      pushRange(
        fixedSegments,
        nightStart,
        nightEnd,
        "bar-fixed-stop",
        `放電禁止 ${sch.night_window_start || "23:00"}-${sch.night_window_end || "07:00"}`
      );
      pushRange(
        fixedSegments,
        dayStart,
        dayEnd,
        "bar-fixed-free",
        `放電許可 ${sch.day_discharge_window_start || "07:00"}-${sch.day_discharge_window_end || "23:00"}`
      );

      const hours = [];
      for (let h = 0; h <= 24; h += 1) {
        const left = (h / 24) * 100;
        const label = `${String(h).padStart(2, "0")}:00`;
        hours.push(`<span style="left:${left}%">${label}</span>`);
      }

      const renderRow = (title, segments) => {
        const bars = segments.map((s) => {
          const left = (s.start / 1440) * 100;
          const width = Math.max(0.6, ((s.end - s.start) / 1440) * 100);
          return `<div class="gantt-bar ${s.cssClass}" style="left:${left}%;width:${width}%">${s.label}</div>`;
        }).join("");
        return `<div class="gantt-row"><div class="gantt-label">${title}</div><div class="gantt-track">${bars}</div></div>`;
      };

      const fixedNotes = ((sch.constraints && sch.constraints.fixed) || [])
        .filter((x) => x && x.enabled !== false)
        .slice(0, 4)
        .map((x) => `<code>${x.id || ""}</code> ${x.description || ""}`)
        .join(" / ");
      const noteSoc = `SOC(安心)=${sch.soc_safety_mode ?? "-"} / SOC(経済・グリーン)=${sch.soc_economy_mode ?? "-"} / 充電時間帯SOC上限=${sch.soc_charge_mode ?? "-"}`;
      const completedStatus = String(sch.settings_completed_status || sch.status || "");
      const settingsOk = sch.settings_completed === true || completedStatus === "applied" || completedStatus === "skipped-no-change";
      const statusIcon = settingsOk
        ? `<span class="gantt-status-icon ok">✓ 設定正常完了</span>`
        : `<span class="gantt-status-icon warn">! 設定未確認</span>`;
      const completedAt = sch.settings_completed_at || sch.recorded_at || "-";
      const completedProfile = sch.settings_completed_profile || sch.profile || sch.mode || "-";
      const noteMeta = `${statusIcon}更新: ${sch.recorded_at || "-"} / 完了確認: ${completedAt} / スロット: ${sch.slot || "-"} / プロファイル: ${completedProfile}`;
      const notePlan = chargeStart == null
        ? "夜間充電の開始時刻は未記録のため、最新の夜間充電量からの推定または未表示になる場合があります。"
        : "";

      root.innerHTML = `
        <div class="gantt-axis"><div>時間（JST）</div><div class="gantt-hours">${hours.join("")}</div></div>
        ${renderRow("実行計画", planSegments)}
        ${renderRow("制約レイヤー", fixedSegments)}
        <div class="gantt-notes">${noteSoc}<br>${noteMeta}${fixedNotes ? `<br>${fixedNotes}` : ""}${notePlan ? `<br>${notePlan}` : ""}</div>
      `;
    }

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      }[ch]));
    }

    function renderDashboardWarnings() {
      const root = document.getElementById("dashboardWarnings");
      if (!root) return;
      const warnings = store.dashboardWarnings || [];
      if (!warnings.length) {
        root.classList.remove("active");
        root.innerHTML = "";
        return;
      }
      const items = warnings.slice(0, 6).map((w) => {
        const severity = String(w.severity || "warning");
        return `
          <li class="warning-item ${escapeHtml(severity)}">
            <span class="warning-title">${escapeHtml(w.title || w.code || "警告")}</span>
            <span class="warning-message">${escapeHtml(w.message || "")}</span>
          </li>
        `;
      }).join("");
      root.classList.add("active");
      root.innerHTML = `<ul class="warning-list">${items}</ul>`;
    }

    function latestAvailableDate() {
      const cutoff = displayCutoffDate();
      const newest = store.meta && store.meta.global_newest_date ? String(store.meta.global_newest_date) : null;
      if (newest && newest < cutoff) return newest;
      return cutoff;
    }

    function oldestAvailableDate() {
      return store.meta && store.meta.global_oldest_date ? String(store.meta.global_oldest_date) : (store.dates[0] || null);
    }

    function initializePeriodState() {
      if (periodState.initialized) return;
      const latest = latestAvailableDate();
      periodState.month = accountingMonthLabel(latest);
      periodState.year = Number((periodState.month || latest).slice(0, 4));
      periodState.initialized = true;
    }

    function currentPeriodRange() {
      initializePeriodState();
      const oldest = oldestAvailableDate();
      const latest = latestAvailableDate();
      if (!oldest || !latest) return null;
      if (periodState.mode === "all") {
        return { start: oldest, end: latest, label: "全期間" };
      }
      if (periodState.mode === "year") {
        const bounds = accountingYearBounds(periodState.year);
        if (!bounds) return null;
        const end = bounds.end > latest ? latest : bounds.end;
        return { start: bounds.start, end, label: `${periodState.year}年分` };
      }
      const month = periodState.month || accountingMonthLabel(latest);
      const bounds = accountingMonthBounds(month);
      if (!bounds) return null;
      const end = bounds.end > latest ? latest : bounds.end;
      return { start: bounds.start, end, label: `${month}分` };
    }

    function selectedWindowDates() {
      const range = currentPeriodRange();
      if (!range || !range.start || !range.end || range.start > range.end) return [];
      return buildDateRange(range.start, range.end);
    }

    function weekStartDate(dateStr) {
      const p = isoParts(dateStr);
      if (!p) return dateStr;
      const base = new Date(Date.UTC(p.year, p.month - 1, p.day));
      const mondayOffset = (base.getUTCDay() + 6) % 7;
      return isoDateAdd(dateStr, -mondayOffset);
    }

    function compactDate(dateStr) {
      const p = isoParts(dateStr);
      if (!p) return String(dateStr || "");
      return `${String(p.month).padStart(2, "0")}/${String(p.day).padStart(2, "0")}`;
    }

    function selectedWindowBuckets() {
      const dates = selectedWindowDates();
      if (periodState.mode !== "year") {
        return dates.map((date) => ({ label: date, start: date, end: date, dates: [date] }));
      }
      const byWeek = new Map();
      for (const date of dates) {
        const key = weekStartDate(date);
        const bucket = byWeek.get(key) || { start: date, end: date, dates: [] };
        bucket.start = bucket.start < date ? bucket.start : date;
        bucket.end = bucket.end > date ? bucket.end : date;
        bucket.dates.push(date);
        byWeek.set(key, bucket);
      }
      return Array.from(byWeek.values())
        .sort((a, b) => a.start.localeCompare(b.start))
        .map((bucket) => ({
          ...bucket,
          label: `${compactDate(bucket.start)}〜${compactDate(bucket.end)}`,
        }));
    }

    function updatePeriodControls() {
      initializePeriodState();
      const modeButtons = {
        month: document.getElementById("periodMonthBtn"),
        year: document.getElementById("periodYearBtn"),
        all: document.getElementById("periodAllBtn"),
      };
      for (const [mode, button] of Object.entries(modeButtons)) {
        if (!button) continue;
        button.classList.toggle("active", periodState.mode === mode);
      }

      const latest = latestAvailableDate();
      const oldest = oldestAvailableDate();
      const latestMonth = latest ? accountingMonthLabel(latest) : null;
      const oldestMonth = oldest ? accountingMonthLabel(oldest) : null;
      const latestYear = latestMonth ? Number(latestMonth.slice(0, 4)) : null;
      const oldestYear = oldestMonth ? Number(oldestMonth.slice(0, 4)) : null;

      const prev = document.getElementById("periodPrevBtn");
      const next = document.getElementById("periodNextBtn");
      let canPrev = false;
      let canNext = false;
      if (periodState.mode === "month") {
        canPrev = !!(oldestMonth && periodState.month && periodState.month > oldestMonth);
        canNext = !!(latestMonth && periodState.month && periodState.month < latestMonth);
      } else if (periodState.mode === "year") {
        canPrev = Number.isFinite(oldestYear) && periodState.year > oldestYear;
        canNext = Number.isFinite(latestYear) && periodState.year < latestYear;
      }
      if (prev) prev.disabled = !canPrev;
      if (next) next.disabled = !canNext;

      const range = currentPeriodRange();
      const label = document.getElementById("periodLabel");
      if (label && range) {
        const close = aggregationCloseDay();
        label.textContent = `${range.label}: ${range.start} 〜 ${range.end}（締め日 ${close}日）`;
      }
    }

    function moveAccountingMonth(monthLabel, delta) {
      const p = isoParts(`${monthLabel}-01`);
      if (!p) return monthLabel;
      const moved = addMonthsParts(p.year, p.month, delta);
      return `${String(moved.year).padStart(4, "0")}-${String(moved.month).padStart(2, "0")}`;
    }

    function clampAccountingMonth(monthLabel) {
      const latest = latestAvailableDate();
      const oldest = oldestAvailableDate();
      const latestMonth = latest ? accountingMonthLabel(latest) : null;
      const oldestMonth = oldest ? accountingMonthLabel(oldest) : null;
      let month = monthLabel;
      if (latestMonth && month > latestMonth) month = latestMonth;
      if (oldestMonth && month < oldestMonth) month = oldestMonth;
      return month;
    }

    async function ensureDataForRange(startDate) {
      while (
        !store.loadingOlder &&
        store.meta &&
        store.meta.has_more_before &&
        store.meta.oldest_loaded_date &&
        store.meta.oldest_loaded_date > startDate
      ) {
        store.loadingOlder = true;
        const prevOldest = store.meta.oldest_loaded_date;
        try {
          setStatus("必要な過去データを読み込んでいます...");
          const endDate = isoDateAdd(prevOldest, -1);
          const payload = await fetchSlice({ window_days: CHUNK_DAYS, end_date: endDate, include_static: false });
          absorbSlice(payload, false);
          if (!store.meta || !store.meta.oldest_loaded_date || store.meta.oldest_loaded_date >= prevOldest) break;
        } catch (_err) {
          setStatus("過去データの読込に失敗しました", "#e6504f");
          break;
        } finally {
          store.loadingOlder = false;
        }
      }
    }

    function fillParamsTable() {
      const tbody = document.querySelector("#paramsTable tbody");
      tbody.innerHTML = "";
      for (const p of store.params) {
        const tr = document.createElement("tr");
        const meta = paramAlias[String(p.name || "")] || { code: "-", label: "補助パラメータ" };
        const values = [
          String(meta.code),
          String(p.name ?? ""),
          String(meta.label),
          n(p.mean_value).toFixed(4),
          n(p.variance).toFixed(6),
          String(n(p.sample_count, 0)),
        ];
        for (const value of values) {
          const td = document.createElement("td");
          td.textContent = value;
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
    }

    function setText(id, value) {
      const el = document.getElementById(id);
      if (el) el.textContent = value == null || value === "" ? "-" : String(value);
    }

    function fillPvForecastDiagnostics() {
      const diag = store.pvForecastDiagnostics || {};
      const physical = diag.physical || {};
      const scales = physical.scales || {};
      const quality = physical.data_quality || {};
      const method = physical.selected_method || "not_generated";
      setText("pvDiagMethod", method);
      setText("pvDiagDate", diag.plan_date || "-");
      setText("pvDiagScale", scales.global_bias_scale == null ? "-" : Number(scales.global_bias_scale).toFixed(4));
      const globalDays = quality.global_days == null ? "-" : `${quality.global_days}/${quality.global_days_required || "-"}`;
      setText("pvDiagQuality", physical.enabled ? `valid ${globalDays}` : (physical.fallback_reason || "not generated"));

      const pathEl = document.getElementById("pvDiagPath");
      pathEl.innerHTML = "";
      const path = Array.isArray(physical.decision_path) ? physical.decision_path : [];
      for (const item of path.length ? path : ["diagnostics_not_available"]) {
        const span = document.createElement("span");
        span.className = "diag-chip";
        span.textContent = String(item);
        pathEl.appendChild(span);
      }

      const tbody = document.querySelector("#pvCandidateTable tbody");
      tbody.innerHTML = "";
      const candidates = physical.candidates || {};
      for (const [name, item] of Object.entries(candidates)) {
        const tr = document.createElement("tr");
        let status = item && item.status ? item.status : "";
        if (!status && name === "existing" && method === "existing") status = "selected";
        if (!status && name !== method) status = physical.fallback_reason ? "waiting" : "-";
        let note = "";
        if (name === method || (name === "existing" && method === "existing")) note = "selected";
        else if (status === "waiting") note = physical.fallback_reason || "threshold not met";
        const row = [
          name,
          item && item.total_kwh != null ? Number(item.total_kwh).toFixed(2) : "-",
          status || "-",
          note,
        ];
        for (const value of row) {
          const td = document.createElement("td");
          td.textContent = String(value);
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
      if (!tbody.children.length) {
        const tr = document.createElement("tr");
        for (const value of ["-", "-", "not generated", "night_charge_plan.json に診断がありません"]) {
          const td = document.createElement("td");
          td.textContent = value;
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }

      fillPvDeveloperTable(diag, physical, scales, quality);
    }

    function addDeveloperRow(tbody, group, name, value, meaning) {
      const tr = document.createElement("tr");
      for (const cell of [group, name, value, meaning]) {
        const td = document.createElement("td");
        td.textContent = String(cell == null || cell === "" ? "-" : cell);
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }

    function fillPvDeveloperTable(diag, physical, scales, quality) {
      const summary = document.getElementById("pvDeveloperSummary");
      const tbody = document.querySelector("#pvDeveloperTable tbody");
      if (!tbody) return;
      tbody.innerHTML = "";
      const input = physical.input || {};
      const retirement = physical.retirement_recommendation || {};
      const correction = diag.forecast_correction || {};
      if (summary) {
        const method = physical.selected_method || "not_generated";
        const reason = physical.fallback_reason ? ` / ${physical.fallback_reason}` : "";
        summary.innerHTML =
          `PV物理予測: <b>${escapeHtml(method)}</b>${escapeHtml(reason)}<br>` +
          `判断経路: <b>${escapeHtml((physical.decision_path || []).join(" → ") || "-")}</b>`;
      }
      addDeveloperRow(tbody, "入力", "lat/lon", `${input.lat ?? "-"} / ${input.lon ?? "-"}`, "太陽位置計算に使う設置地点");
      addDeveloperRow(tbody, "入力", "roof_pitch_deg", input.roof_pitch_deg ?? "-", "一般工事勾配を前提にしたパネル傾斜角");
      addDeveloperRow(tbody, "入力", "panel_weights", JSON.stringify(input.panel_weights || {}), "東/南/西の相対出力比");
      addDeveloperRow(tbody, "入力", "shortwave_hours", (input.shortwave_hours || []).join(", "), "短波放射が使えた時間");
      addDeveloperRow(tbody, "データ", "global_days", `${quality.global_days ?? 0}/${quality.global_days_required ?? "-"}`, "global scale採用に必要な有効日数");
      addDeveloperRow(tbody, "データ", "daypart_min_samples", quality.daypart_min_samples ?? "-", "朝/昼/夕スケールの自動発動閾値");
      addDeveloperRow(tbody, "データ", "bin_min_samples", quality.bin_min_samples ?? "-", "太陽高度×短波binスケールの自動発動閾値");
      addDeveloperRow(tbody, "係数", "radiation_scale", scales.radiation_scale ?? "-", "短波放射からPVへ変換する基礎係数");
      addDeveloperRow(tbody, "係数", "radiation_scale_source", scales.radiation_scale_source ?? "-", "基礎係数の取得元");
      addDeveloperRow(tbody, "係数", "global_bias_scale", scales.global_bias_scale ?? "-", "平均誤差を0へ寄せる全体補正");
      addDeveloperRow(tbody, "既存補正", "pv_ratio_ewma_skipped", correction.pv_ratio_ewma_skipped ?? "-", "物理PV採用時に既存PV EWMAを二重適用しないためのフラグ");
      addDeveloperRow(tbody, "整理候補", "existing_pv_ewma", JSON.stringify((retirement.existing_pv_ewma || {})), "データ蓄積後に既存PV EWMAを整理するかの提案");
    }

    function commonOptions() {
      return {
        responsive: true,
        maintainAspectRatio: false,
        animation: false,
        normalized: true,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { labels: { usePointStyle: true } },
          tooltip: {
            callbacks: {
              label: (ctx) => {
                const label = ctx.dataset && ctx.dataset.label ? ctx.dataset.label : "";
                const value = formatChartValue(ctx.parsed && ctx.parsed.y);
                return label ? `${label}: ${value}` : `${value}`;
              },
            },
          },
        },
      };
    }

    function buildCharts() {
      charts.hourlyForecast = new Chart(document.getElementById("hourlyForecastChart"), {
        type: "line",
        data: {
          labels: [],
          datasets: [
            { label: "予想発電量(kWh/h)", data: [], borderColor: "#147efb", backgroundColor: "#147efb", tension: 0.25, pointRadius: 3 },
            { label: "予想充電量(kWh/h)", data: [], borderColor: "#14b86f", backgroundColor: "#14b86f", tension: 0.25, pointRadius: 3 },
            { label: "予想消費電量(kWh/h)", data: [], borderColor: "#e6504f", backgroundColor: "#e6504f", tension: 0.25, pointRadius: 3 },
          ],
        },
        options: {
          ...commonOptions(),
          scales: { y: { min: 0, max: 1, title: { display: true, text: "kWh/h" }, grid: { color: "#d8e6f2" } } },
        },
      });

      charts.sun = new Chart(document.getElementById("sunChart"), {
        data: {
          labels: [],
          datasets: [
            { type: "line", label: "予測(時間)", data: [], borderColor: "#147efb", backgroundColor: "#147efb", tension: 0.25 },
            { type: "line", label: "実績(時間)", data: [], borderColor: "#14b86f", backgroundColor: "#14b86f", tension: 0.25 },
            { type: "bar", label: "差分(時間)", data: [], backgroundColor: "#ef8e1d66", borderColor: "#ef8e1d" },
          ],
        },
        options: {
          ...commonOptions(),
          scales: { y: { min: -1, max: 1, title: { display: true, text: "h" }, grid: { color: "#d8e6f2" } } },
        },
      });

      charts.pv = new Chart(document.getElementById("pvChart"), {
        data: {
          labels: [],
          datasets: [
            { type: "line", label: "予測(kWh)", data: [], borderColor: "#147efb", backgroundColor: "#147efb", tension: 0.25 },
            { type: "line", label: "実績(kWh)", data: [], borderColor: "#14b86f", backgroundColor: "#14b86f", tension: 0.25 },
            { type: "bar", label: "差分(kWh)", data: [], backgroundColor: "#ef8e1d66", borderColor: "#ef8e1d" },
          ],
        },
        options: {
          ...commonOptions(),
          scales: { y: { min: -1, max: 1, title: { display: true, text: "kWh" }, grid: { color: "#d8e6f2" } } },
        },
      });

      charts.load = new Chart(document.getElementById("loadChart"), {
        data: {
          labels: [],
          datasets: [
            { type: "line", label: "予測(kWh)", data: [], borderColor: "#147efb", backgroundColor: "#147efb", tension: 0.25 },
            { type: "line", label: "実績(kWh)", data: [], borderColor: "#14b86f", backgroundColor: "#14b86f", tension: 0.25 },
            { type: "bar", label: "差分(kWh)", data: [], backgroundColor: "#ef8e1d66", borderColor: "#ef8e1d" },
          ],
        },
        options: {
          ...commonOptions(),
          scales: { y: { min: -1, max: 1, title: { display: true, text: "kWh" }, grid: { color: "#d8e6f2" } } },
        },
      });

      charts.dailyKwh = new Chart(document.getElementById("dailyKwhChart"), {
        type: "bar",
        data: { labels: [], datasets: [
          { type: "bar", label: "日次 自家消費(kWh)", data: [], yAxisID: "y", backgroundColor: "#147efb66", borderColor: "#147efb", borderWidth: 1.2 },
          { type: "line", label: "累計 自家消費(kWh)", data: [], yAxisID: "y2", borderColor: "#14b86f", backgroundColor: "#14b86f", tension: 0.25 },
        ]},
        options: { ...commonOptions(), scales: { y: { min: 0, max: 1 }, y2: { min: 0, max: 1, position: "right", grid: { drawOnChartArea: false } } } },
      });

      charts.dailyYen = new Chart(document.getElementById("dailyYenChart"), {
        type: "bar",
        data: { labels: [], datasets: [
          { type: "bar", label: "日次 節約額(円)", data: [], yAxisID: "y", backgroundColor: "#ef8e1d66", borderColor: "#ef8e1d", borderWidth: 1.2 },
          { type: "line", label: "累計 節約額(円)", data: [], yAxisID: "y2", borderColor: "#e6504f", backgroundColor: "#e6504f", tension: 0.25 },
        ]},
        options: { ...commonOptions(), scales: { y: { min: 0, max: 1 }, y2: { min: 0, max: 1, position: "right", grid: { drawOnChartArea: false } } } },
      });

      charts.monthly = new Chart(document.getElementById("monthlyCostChart"), {
        type: "line",
        data: { labels: [], datasets: [
          { label: "月間 自家消費(kWh)", data: [], borderColor: "#147efb", yAxisID: "y", tension: 0.25 },
          { label: "月間 節約額(円)", data: [], borderColor: "#ef8e1d", yAxisID: "y2", tension: 0.25 },
        ]},
        options: { ...commonOptions(), scales: { y: { min: 0, max: 1 }, y2: { min: 0, max: 1, position: "right", grid: { drawOnChartArea: false } } } },
      });

      charts.battery = new Chart(document.getElementById("batteryChart"), {
        type: "line",
        data: { labels: [], datasets: [
          { label: "設定SOC(%)", data: [], yAxisID: "y2", borderColor: "#147efb", backgroundColor: "#147efb", tension: 0.25, pointRadius: 2, pointHoverRadius: 4, borderWidth: 2.5 },
          { label: "夜間充電計画(kWh/日)", data: [], yAxisID: "y", borderColor: "#ef8e1d", backgroundColor: "#ef8e1d", tension: 0.25 },
          { label: "太陽光蓄電余力予測(kWh/日)", data: [], yAxisID: "y", borderColor: "#14b86f", backgroundColor: "#14b86f", tension: 0.25 },
          { label: "太陽光充電終了時SOC(%)", data: [], yAxisID: "y2", borderColor: "#e6504f", backgroundColor: "#e6504f", tension: 0.25, pointRadius: 3, pointHoverRadius: 5, borderWidth: 3, spanGaps: true },
        ]},
        options: {
          ...commonOptions(),
          scales: {
            y: { min: 0, max: 1, title: { display: true, text: "kWh" }, grid: { color: "#d8e6f2" } },
            y2: { min: 0, max: 100, position: "right", title: { display: true, text: "SOC(%)" }, grid: { drawOnChartArea: false } },
          },
        },
      });

      charts.batteryLife = new Chart(document.getElementById("batteryLifeChart"), {
        type: "bar",
        data: { labels: [], datasets: [
          { label: "現在推定容量(kWh)", data: [], backgroundColor: "#147efb99", borderColor: "#147efb", borderWidth: 1, stack: "capacity" },
          { label: "サイクル劣化(kWh)", data: [], backgroundColor: "#ef8e1d99", borderColor: "#d17814", borderWidth: 1, stack: "capacity" },
          { label: "カレンダー劣化(kWh)", data: [], backgroundColor: "#9dafbf99", borderColor: "#7d8fa1", borderWidth: 1, stack: "capacity" },
          { label: "温度容量補正(kWh)", data: [], backgroundColor: "#ffe08a99", borderColor: "#d8a900", borderWidth: 1, stack: "capacity" },
        ]},
        options: {
          ...commonOptions(),
          plugins: {
            ...commonOptions().plugins,
            legend: { labels: { usePointStyle: true, boxWidth: 8, font: { size: 11 } } },
          },
          scales: {
            y: { min: 0, max: 1, stacked: true, title: { display: true, text: "初期容量内訳(kWh)", color: "#147efb" }, ticks: { color: "#147efb" }, border: { color: "#147efb" }, grid: { color: "#d8e6f2" } },
            x: { stacked: true },
          },
        },
      });
    }

    function rowByDate(map, day) {
      return map.get(day) || null;
    }

    function valueOrNull(value) {
      if (value === null || value === undefined || value === "") return null;
      const x = Number(value);
      return Number.isFinite(x) ? x : null;
    }

    function sumBucket(bucket, map, key) {
      let total = 0;
      let seen = false;
      for (const day of bucket.dates) {
        const value = valueOrNull(rowByDate(map, day)?.[key]);
        if (value === null) continue;
        total += value;
        seen = true;
      }
      return seen ? total : null;
    }

    function averageBucket(bucket, map, key) {
      let total = 0;
      let count = 0;
      for (const day of bucket.dates) {
        const value = valueOrNull(rowByDate(map, day)?.[key]);
        if (value === null) continue;
        total += value;
        count += 1;
      }
      return count ? total / count : null;
    }

    function buildBucketCumulativeSeries(buckets, valueByDay) {
      if (!store.dates.length || !buckets.length) {
        return buckets.map(() => 0);
      }
      const firstDate = buckets[0].start;
      let running = 0;
      for (const d of store.dates) {
        if (d >= firstDate) break;
        running += n(valueByDay.get(d));
      }
      const out = [];
      for (const bucket of buckets) {
        for (const d of bucket.dates) {
          running += n(valueByDay.get(d));
        }
        out.push(running);
      }
      return out;
    }

    function diffOrNull(actual, forecast) {
      if (actual == null || forecast == null) return null;
      return n(actual) - n(forecast);
    }

    function updateForecastActualChart(chart, labels, forecast, actual, diff, unit) {
      chart.data.labels = labels;
      chart.data.datasets[0].data = forecast;
      chart.data.datasets[1].data = actual;
      chart.data.datasets[2].data = diff;
      const values = [...forecast, ...actual, ...diff].filter((v) => v != null);
      const axisMax = niceCeil(Math.max(1, maxPos(values)));
      const axisMin = Math.min(-axisMax, Math.floor(Math.min(...diff.filter((v) => v != null), 0)));
      chart.options.scales.y.min = axisMin;
      chart.options.scales.y.max = axisMax;
      chart.options.scales.y.grid = {
        color: (ctx) => (ctx.tick && ctx.tick.value === 0 ? "#6d7f91" : "#d8e6f2"),
        lineWidth: (ctx) => (ctx.tick && ctx.tick.value === 0 ? 2.6 : 1),
      };
      chart.options.scales.y.ticks = { callback: (v) => `${v}${unit}` };
      chart.update("none");
    }

    function hourlyForecastDate() {
      const sch = store.latestSchedule || {};
      if (sch.plan_date && store.hourly.has(String(sch.plan_date))) return String(sch.plan_date);
      let newest = null;
      for (const date of store.hourly.keys()) {
        if (!newest || date > newest) newest = date;
      }
      return newest;
    }

    function renderHourlyForecast() {
      const chart = charts.hourlyForecast;
      if (!chart) return;
      const note = document.getElementById("hourlyForecastNote");
      const date = hourlyForecastDate();
      const rows = date ? (store.hourly.get(date) || []) : [];
      if (!rows.length) {
        chart.data.labels = [];
        chart.data.datasets[0].data = [];
        chart.data.datasets[1].data = [];
        chart.data.datasets[2].data = [];
        if (note) note.textContent = "時間別予測データがまだ保存されていません。23時ジョブ実行後に表示されます。";
        chart.update("none");
        return;
      }
      const labels = rows.map((row) => `${String(Number(row.hour)).padStart(2, "0")}:00`);
      const pv = rows.map((row) => row.forecast_pv_kwh == null ? null : n(row.forecast_pv_kwh));
      const charge = rows.map((row) => row.forecast_charge_kwh == null ? null : n(row.forecast_charge_kwh));
      const load = rows.map((row) => row.forecast_load_kwh == null ? null : n(row.forecast_load_kwh));
      chart.data.labels = labels;
      chart.data.datasets[0].data = pv;
      chart.data.datasets[1].data = charge;
      chart.data.datasets[2].data = load;
      const axisMax = niceCeil(Math.max(1, maxPos([...pv, ...charge, ...load].filter((v) => v != null))));
      chart.options.scales.y.min = 0;
      chart.options.scales.y.max = axisMax;
      chart.options.scales.y.ticks = { callback: (v) => `${v}kWh` };
      if (note) {
        const totalPv = pv.reduce((acc, v) => acc + n(v), 0);
        const totalCharge = charge.reduce((acc, v) => acc + n(v), 0);
        const totalLoad = load.reduce((acc, v) => acc + n(v), 0);
        note.textContent = `${date} の1時間予測。発電 ${totalPv.toFixed(2)}kWh / 充電 ${totalCharge.toFixed(2)}kWh / 消費 ${totalLoad.toFixed(2)}kWh`;
      }
      chart.update("none");
    }

    function modelParam(name, fallback) {
      for (const row of store.params || []) {
        if (String(row.name || "") !== name) continue;
        const value = Number(row.mean_value);
        if (Number.isFinite(value)) return value;
      }
      return fallback;
    }

    function setLifeText(id, text, unit = "") {
      const el = document.getElementById(id);
      if (!el) return;
      el.innerHTML = unit ? `${escapeHtml(text)}<small>${escapeHtml(unit)}</small>` : escapeHtml(text);
    }

    function monthSpan(startIso, endIso) {
      const start = isoParts(startIso);
      const end = isoParts(endIso);
      if (!start || !end) return 1;
      const months = (end.year - start.year) * 12 + (end.month - start.month) + (end.day >= start.day ? 1 : 0.5);
      return Math.max(1, months);
    }

    function renderBatteryLifeProjection(buckets) {
      const chart = charts.batteryLife;
      if (!chart || !buckets.length) return;
      const range = currentPeriodRange();
      const dates = selectedWindowDates();
      let totalCharge = 0;
      let totalDischarge = 0;
      for (const day of dates) {
        const row = store.batteryFlow.get(day);
        totalCharge += n(row && row.charge_kwh);
        totalDischarge += n(row && row.discharge_kwh);
      }

      const baseCapacity = Math.max(0.1, modelParam("battery_usable_capacity_kwh", 9.0));
      const cycleFadePerCycle = Math.max(0.00001, modelParam("battery_cycle_capacity_fade_per_cycle", 0.0003));
      const tempCoeffPerDeg = modelParam("battery_temp_coeff_per_deg", -0.005);
      const equivalentCycles = totalDischarge / baseCapacity;
      const currentCycleLoss = Math.min(baseCapacity * 0.8, baseCapacity * cycleFadePerCycle * equivalentCycles);
      const currentCapacity = Math.max(baseCapacity * 0.2, baseCapacity - currentCycleLoss);
      const currentSoh = currentCapacity / baseCapacity * 100;
      const months = range ? monthSpan(range.start, range.end) : 1;
      const monthlyEqCycles = equivalentCycles / months;
      const monthlyCycleLoss = baseCapacity * cycleFadePerCycle * monthlyEqCycles;
      const calendarLoss = baseCapacity * 0.00025;

      const monthKeys = Array.from(new Set(dates.map((day) => day.slice(0, 7)))).sort();
      const labels = [];
      const capacities = [];
      const cycleLosses = [];
      const tempLosses = [];
      const calendarLosses = [];
      let cumulativeDischarge = 0;
      let monthIndex = 0;
      for (const monthKey of monthKeys) {
        monthIndex += 1;
        let monthDischarge = 0;
        let tempSum = 0;
        let tempCount = 0;
        for (const day of dates) {
          if (!day.startsWith(monthKey)) continue;
          const flow = store.batteryFlow.get(day);
          monthDischarge += n(flow && flow.discharge_kwh);
          const weather = store.sunshine.get(day);
          const actualTemp = valueOrNull(weather && weather.actual_temp_c);
          if (actualTemp !== null) {
            tempSum += actualTemp;
            tempCount += 1;
          }
        }
        cumulativeDischarge += monthDischarge;
        const cumulativeCycles = cumulativeDischarge / baseCapacity;
        const cumulativeCycleLoss = Math.min(baseCapacity * 0.8, baseCapacity * cycleFadePerCycle * cumulativeCycles);
        const cumulativeCalendarLoss = Math.min(baseCapacity * 0.8 - cumulativeCycleLoss, calendarLoss * monthIndex);
        const agedCapacity = Math.max(baseCapacity * 0.2, baseCapacity - cumulativeCycleLoss - cumulativeCalendarLoss);
        const avgTemp = tempCount ? tempSum / tempCount : null;
        const tempFactor = avgTemp === null ? 1.0 : Math.max(0.7, 1 + tempCoeffPerDeg * (avgTemp - 25));
        const tempLoss = Math.max(0, agedCapacity * (1 - tempFactor));
        const projectedCapacity = Math.max(0, baseCapacity - cumulativeCycleLoss - cumulativeCalendarLoss - tempLoss);
        labels.push(monthKey);
        capacities.push(Number(projectedCapacity.toFixed(3)));
        cycleLosses.push(Number(cumulativeCycleLoss.toFixed(4)));
        tempLosses.push(Number(tempLoss.toFixed(4)));
        calendarLosses.push(Number(cumulativeCalendarLoss.toFixed(4)));
      }

      const lossPerMonth = monthlyCycleLoss + calendarLoss;
      const monthsTo80 = lossPerMonth > 0 ? Math.max(0, (currentCapacity - baseCapacity * 0.8) / lossPerMonth) : null;
      const latest = latestAvailableDate();
      const latestParts = isoParts(latest || todayIsoJst());
      const baseDate = latestParts
        ? new Date(Date.UTC(latestParts.year, latestParts.month - 1, 1))
        : new Date();
      const year80 = monthsTo80 == null
        ? "-"
        : String(new Date(Date.UTC(baseDate.getUTCFullYear(), baseDate.getUTCMonth() + Math.ceil(monthsTo80), 1)).getUTCFullYear());

      setLifeText("lifeTotalCharge", totalCharge.toFixed(1), "kWh");
      setLifeText("lifeTotalDischarge", totalDischarge.toFixed(1), "kWh");
      setLifeText("lifeEquivalentCycles", equivalentCycles.toFixed(1), "回");
      setLifeText("lifeEstimatedCapacity", `${currentCapacity.toFixed(2)} kWh / ${currentSoh.toFixed(1)}`, "%");
      setLifeText("lifeEightyPercentYear", year80 === "-" ? "-" : `${year80}年`, "ごろ");
      setLifeText("lifeMonthlyCycles", monthlyEqCycles.toFixed(1), "回/月");

      chart.data.labels = labels;
      chart.data.datasets[0].data = capacities;
      chart.data.datasets[1].data = cycleLosses;
      chart.data.datasets[2].data = calendarLosses;
      chart.data.datasets[3].data = tempLosses;
      chart.options.scales.y.min = 0;
      chart.options.scales.y.max = Math.ceil(baseCapacity * 10) / 10;
      chart.options.scales.y.ticks = { color: "#147efb", callback: (v) => `${Number(v).toFixed(1)}` };
      chart.update("none");
    }

    function renderWindow() {
      const buckets = selectedWindowBuckets();
      const labels = buckets.map((bucket) => bucket.label);
      if (!buckets.length) {
        setStatus("データがまだありません。23時ジョブ実行後に表示されます。", "#e6504f");
        return;
      }
      const isWeekly = periodState.mode === "year";
      const bucketLabel = isWeekly ? "週次" : "日次";
      const perUnit = isWeekly ? "週" : "日";
      const sunForecast = buckets.map((bucket) => sumBucket(bucket, store.sunshine, "forecast_hours"));
      const sunActual = buckets.map((bucket) => sumBucket(bucket, store.sunshine, "actual_hours"));
      const sunDiff = buckets.map((_bucket, i) => diffOrNull(sunActual[i], sunForecast[i]));

      updateForecastActualChart(charts.sun, labels, sunForecast, sunActual, sunDiff, "h");

      const pvForecast = buckets.map((bucket) => sumBucket(bucket, store.energy, "forecast_pv_kwh"));
      const pvActual = buckets.map((bucket) => sumBucket(bucket, store.energy, "actual_pv_kwh"));
      const pvDiff = labels.map((_d, i) => diffOrNull(pvActual[i], pvForecast[i]));
      updateForecastActualChart(charts.pv, labels, pvForecast, pvActual, pvDiff, "kWh");

      const loadForecast = buckets.map((bucket) => sumBucket(bucket, store.energy, "forecast_load_kwh"));
      const loadActual = buckets.map((bucket) => sumBucket(bucket, store.energy, "actual_load_kwh"));
      const loadDiff = labels.map((_d, i) => diffOrNull(loadActual[i], loadForecast[i]));
      updateForecastActualChart(charts.load, labels, loadForecast, loadActual, loadDiff, "kWh");

      const dailySelf = buckets.map((bucket) => n(sumBucket(bucket, store.cost, "self_consumption_kwh")));
      const dailyYen = buckets.map((bucket) => n(sumBucket(bucket, store.cost, "savings_yen")));
      const selfByDay = new Map();
      const yenByDay = new Map();
      for (const [d, row] of store.cost.entries()) {
        selfByDay.set(d, n(row && row.self_consumption_kwh));
        yenByDay.set(d, n(row && row.savings_yen));
      }
      const cumKwh = buildBucketCumulativeSeries(buckets, selfByDay);
      const cumYen = buildBucketCumulativeSeries(buckets, yenByDay);

      const dailyKwhTitle = document.getElementById("dailyKwhTitle");
      const dailyKwhDesc = document.getElementById("dailyKwhDesc");
      if (dailyKwhTitle) dailyKwhTitle.textContent = `2. 自家消費kWh（${isWeekly ? "週" : "日"}）`;
      if (dailyKwhDesc) dailyKwhDesc.textContent = `青の棒: ${bucketLabel}自家消費、緑の折れ線: 累計自家消費。左右で縦軸を分けます。`;

      charts.dailyKwh.data.labels = labels;
      charts.dailyKwh.data.datasets[0].label = `${bucketLabel} 自家消費(kWh)`;
      charts.dailyKwh.data.datasets[0].data = dailySelf;
      charts.dailyKwh.data.datasets[1].data = cumKwh;
      const dailyKwhDual = dualScales(dailySelf, cumKwh, { leftUnit: "kWh", rightUnit: "kWh" });
      charts.dailyKwh.options.scales.y = {
        ...dailyKwhDual.y,
        ticks: { ...dailyKwhDual.y.ticks, color: "#147efb" },
        border: { color: "#147efb" },
        title: { display: true, text: `${bucketLabel} kWh`, color: "#147efb" },
      };
      charts.dailyKwh.options.scales.y2 = {
        ...dailyKwhDual.y2,
        ticks: { ...dailyKwhDual.y2.ticks, color: "#14b86f" },
        border: { color: "#14b86f" },
        title: { display: true, text: "累計 kWh", color: "#14b86f" },
      };
      charts.dailyKwh.update("none");

      const dailyYenTitle = document.getElementById("dailyYenTitle");
      const dailyYenDesc = document.getElementById("dailyYenDesc");
      if (dailyYenTitle) dailyYenTitle.textContent = `3. 節約額（${isWeekly ? "週" : "日"}）`;
      if (dailyYenDesc) dailyYenDesc.textContent = `橙の棒: ${bucketLabel}節約額、赤の折れ線: 累計節約額。左右で縦軸を分けます。`;

      charts.dailyYen.data.labels = labels;
      charts.dailyYen.data.datasets[0].label = `${bucketLabel} 節約額(円)`;
      charts.dailyYen.data.datasets[0].data = dailyYen;
      charts.dailyYen.data.datasets[1].data = cumYen;
      const dailyYenDual = dualScales(dailyYen, cumYen, { leftUnit: "円", rightUnit: "円" });
      charts.dailyYen.options.scales.y = {
        ...dailyYenDual.y,
        ticks: { ...dailyYenDual.y.ticks, color: "#ef8e1d" },
        border: { color: "#ef8e1d" },
        title: { display: true, text: `${bucketLabel} 円`, color: "#ef8e1d" },
      };
      charts.dailyYen.options.scales.y2 = {
        ...dailyYenDual.y2,
        ticks: { ...dailyYenDual.y2.ticks, color: "#e6504f" },
        border: { color: "#e6504f" },
        title: { display: true, text: "累計 円", color: "#e6504f" },
      };
      charts.dailyYen.update("none");

      const batteryTarget = buckets.map((bucket) => averageBucket(bucket, store.battery, "setting_soc_target_percent"));
      const batteryNight = buckets.map((bucket) => sumBucket(bucket, store.battery, "night_charge_kwh"));
      const batteryPvMax = buckets.map((bucket) => sumBucket(bucket, store.battery, "pv_max_charge_kwh"));
      const batteryPvChargeEndSoc = buckets.map((bucket) => averageBucket(bucket, store.battery, "pv_charge_end_soc_percent"));

      const batteryTitle = document.getElementById("batteryTitle");
      const batteryDesc = document.getElementById("batteryDesc");
      if (batteryTitle) batteryTitle.textContent = `5. 蓄電池計画値と実績（${isWeekly ? "週次" : "日次"}）`;
      if (batteryDesc) {
        batteryDesc.textContent = `左軸はkWh/${perUnit}、右軸はSOC(%)。太陽光充電終了時SOCは、その日にPV発電中の充電が最後に発生した時点のSOCです。`;
      }

      charts.battery.data.labels = labels;
      charts.battery.data.datasets[0].label = isWeekly ? "平均設定SOC(%)" : "設定SOC(%)";
      charts.battery.data.datasets[1].label = `夜間充電計画(kWh/${perUnit})`;
      charts.battery.data.datasets[2].label = `太陽光蓄電余力予測(kWh/${perUnit})`;
      charts.battery.data.datasets[3].label = isWeekly ? "平均太陽光充電終了時SOC(%)" : "太陽光充電終了時SOC(%)";
      charts.battery.data.datasets[0].data = batteryTarget;
      charts.battery.data.datasets[1].data = batteryNight;
      charts.battery.data.datasets[2].data = batteryPvMax;
      charts.battery.data.datasets[3].data = batteryPvChargeEndSoc;
      charts.battery.data.datasets[3].hidden = !batteryPvChargeEndSoc.some((v) => v != null);
      const batterySoc = [
        ...batteryTarget.filter((v) => v != null),
        ...batteryPvChargeEndSoc.filter((v) => v != null),
      ];
      const batteryKwh = [
        ...batteryNight.filter((v) => v != null),
        ...batteryPvMax.filter((v) => v != null),
      ];
      const batteryDual = dualScales(batteryKwh, batterySoc, { leftUnit: "kWh", rightUnit: "%"});
      charts.battery.options.scales.y = {
        ...batteryDual.y,
        ticks: { ...batteryDual.y.ticks, color: "#14b86f" },
        border: { color: "#14b86f" },
        title: { display: true, text: "kWh", color: "#14b86f" },
      };
      charts.battery.options.scales.y2 = {
        ...batteryDual.y2,
        min: 0,
        max: 100,
        ticks: { ...batteryDual.y2.ticks, color: "#147efb", callback: (v) => `${Math.round(v)}%` },
        border: { color: "#147efb" },
        title: { display: true, text: "SOC(%)", color: "#147efb" },
      };
      charts.battery.update("none");

      renderBatteryLifeProjection(buckets);

      setStatus(`表示期間: ${buckets[0].start} 〜 ${buckets[buckets.length - 1].end}${isWeekly ? "（週単位）" : ""}`);
    }

    function renderMonthly() {
      const range = currentPeriodRange();
      const rows = range
        ? store.monthly.filter((x) => {
            const start = String(x.period_start || `${x.month || ""}-01`);
            const end = String(x.period_end || `${x.month || ""}-31`);
            return start <= range.end && end >= range.start;
          })
        : store.monthly;
      const monthLabels = rows.map((x) => x.month);
      const monthKwh = rows.map((x) => n(x.self_consumption_kwh));
      const monthYen = rows.map((x) => n(x.savings_yen));
      charts.monthly.data.labels = monthLabels;
      charts.monthly.data.datasets[0].data = monthKwh;
      charts.monthly.data.datasets[1].data = monthYen;
      const scales = dualScales(monthKwh, monthYen, { leftUnit: "kWh", rightUnit: "円" });
      charts.monthly.options.scales.y = {
        ...scales.y,
        ticks: { ...scales.y.ticks, color: "#147efb" },
        border: { color: "#147efb" },
        title: { display: true, text: "kWh", color: "#147efb" },
      };
      charts.monthly.options.scales.y2 = {
        ...scales.y2,
        ticks: { ...scales.y2.ticks, color: "#ef8e1d" },
        border: { color: "#ef8e1d" },
        title: { display: true, text: "円", color: "#ef8e1d" },
      };
      charts.monthly.update("none");
    }

    async function refreshDashboard() {
      initializePeriodState();
      const range = currentPeriodRange();
      if (range && range.start) {
        await ensureDataForRange(range.start);
      }
      updatePeriodControls();
      renderDashboardWarnings();
      renderConstraintGantt();
      renderHourlyForecast();
      renderWindow();
      renderMonthly();
    }

    function bindPeriodControls() {
      const setMode = async (mode) => {
        initializePeriodState();
        const previousMode = periodState.mode;
        if (mode === "year" && previousMode === "month" && periodState.month) {
          periodState.year = Number(periodState.month.slice(0, 4));
        }
        if (mode === "month" && previousMode === "year" && Number.isFinite(periodState.year)) {
          const latest = latestAvailableDate();
          const latestMonth = latest ? accountingMonthLabel(latest) : null;
          const candidate = `${String(periodState.year).padStart(4, "0")}-12`;
          periodState.month = clampAccountingMonth(latestMonth && candidate > latestMonth ? latestMonth : candidate);
        }
        periodState.mode = mode;
        await refreshDashboard();
      };
      const byId = (id) => document.getElementById(id);
      byId("periodMonthBtn")?.addEventListener("click", () => setMode("month"));
      byId("periodYearBtn")?.addEventListener("click", () => setMode("year"));
      byId("periodAllBtn")?.addEventListener("click", () => setMode("all"));
      byId("periodPrevBtn")?.addEventListener("click", async () => {
        initializePeriodState();
        if (periodState.mode === "month" && periodState.month) {
          periodState.month = moveAccountingMonth(periodState.month, -1);
        } else if (periodState.mode === "year") {
          periodState.year -= 1;
        }
        await refreshDashboard();
      });
      byId("periodNextBtn")?.addEventListener("click", async () => {
        initializePeriodState();
        const latest = latestAvailableDate();
        const latestMonth = latest ? accountingMonthLabel(latest) : null;
        const latestYear = latestMonth ? Number(latestMonth.slice(0, 4)) : null;
        if (periodState.mode === "month" && periodState.month && latestMonth && periodState.month < latestMonth) {
          periodState.month = moveAccountingMonth(periodState.month, 1);
        } else if (periodState.mode === "year" && Number.isFinite(latestYear) && periodState.year < latestYear) {
          periodState.year += 1;
        }
        await refreshDashboard();
      });
    }

    async function main() {
      buildCharts();
      bindPeriodControls();

      const initialPayload = window.__DASHBOARD_DATA__ || {};
      const hasInitialRows =
        (initialPayload.sunshine_daily && initialPayload.sunshine_daily.length) ||
        (initialPayload.energy_daily && initialPayload.energy_daily.length) ||
        (initialPayload.cost_daily && initialPayload.cost_daily.length) ||
        (initialPayload.battery_daily && initialPayload.battery_daily.length);
      if (hasInitialRows) {
        absorbSlice(initialPayload, true);
      }

      try {
        const bootstrap = await fetchSlice({ window_days: WINDOW_DAYS, include_static: true });
        absorbSlice(bootstrap, true);
      } catch (_err) {
        if (!store.dates.length) {
          setStatus("データ読込に失敗しました（認証の再確認をお願いします）", "#e6504f");
          return;
        }
        setStatus("最新データの再取得に失敗したため、取得済みデータで表示しています。", "#ef8e1d");
      }

      fillParamsTable();
      fillPvForecastDiagnostics();
      await refreshDashboard();

      const resizeAll = () => {
        renderConstraintGantt();
        renderDashboardWarnings();
        for (const c of Object.values(charts)) c.resize();
        renderHourlyForecast();
        renderWindow();
        renderMonthly();
        updatePeriodControls();
      };
      window.addEventListener("orientationchange", () => setTimeout(resizeAll, 120));
      window.addEventListener("resize", () => setTimeout(resizeAll, 80));
    }
    main();
  </script>
</body>
</html>
""".replace("__DASHBOARD_DATA_PLACEHOLDER__", payload_json).replace("__NONCE__", script_nonce)


class Handler(BaseHTTPRequestHandler):
    server_version = "SolarDashboard"
    sys_version = ""

    def _send_security_headers(self, script_nonce: str | None = None) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        script_src = "script-src 'self' https://cdn.jsdelivr.net"
        if script_nonce:
            script_src = f"{script_src} 'nonce-{script_nonce}'"
        self.send_header(
            "Content-Security-Policy",
            f"default-src 'self'; {script_src}; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none';",
        )
        self.send_header("Cache-Control", "no-store")

    def _cookie_secure_flag(self) -> bool:
        explicit = os.getenv("DASHBOARD_COOKIE_SECURE", "").strip().lower()
        if explicit in {"1", "true", "yes", "on"}:
            return True
        if explicit in {"0", "false", "no", "off"}:
            return False
        forwarded_proto = self.headers.get("X-Forwarded-Proto", "").lower()
        host = self.headers.get("Host", "").lower()
        return forwarded_proto == "https" or ("localhost" not in host and "127.0.0.1" not in host)

    def _extract_cookie(self, name: str) -> str | None:
        raw = self.headers.get("Cookie", "")
        if not raw:
            return None
        parts = [p.strip() for p in raw.split(";")]
        key = f"{name}="
        for part in parts:
            if part.startswith(key):
                return part[len(key) :]
        return None

    def _build_session_cookie(self) -> str:
        ttl = int(_env("DASHBOARD_SESSION_TTL_SECONDS", "604800"))
        exp = int(time.time()) + max(60, ttl)
        token = f"{exp}.{_sign_session(exp)}"
        bits = [
            f"sdash={token}",
            "Path=/",
            f"Max-Age={max(60, ttl)}",
            "HttpOnly",
            "SameSite=Strict",
        ]
        if self._cookie_secure_flag():
            bits.append("Secure")
        return "; ".join(bits)

    def _maybe_send_auth_cookie(self) -> None:
        if getattr(self, "_new_session_cookie", None):
            self.send_header("Set-Cookie", self._new_session_cookie)

    def _is_authorized(self, parsed) -> bool:
        if not _auth_enabled():
            return True

        session = self._extract_cookie("sdash")
        if session and _verify_session(session):
            return True

        qs = parse_qs(parsed.query or "")
        token_list = qs.get("auth", [])
        if token_list:
            creds = _decode_auth_token(token_list[0])
            if creds:
                expected_user = os.getenv("DASHBOARD_BASIC_USER", "")
                expected_passwd = os.getenv("DASHBOARD_BASIC_PASSWORD", "")
                user, passwd = creds
                if hmac.compare_digest(user, expected_user) and hmac.compare_digest(passwd, expected_passwd):
                    self._new_session_cookie = self._build_session_cookie()
                    return True

        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        encoded = auth[6:].strip()
        try:
            decoded = base64.b64decode(encoded).decode("utf-8")
        except Exception:
            return False
        if ":" not in decoded:
            return False
        user, passwd = decoded.split(":", 1)
        expected_user = os.getenv("DASHBOARD_BASIC_USER", "")
        expected_passwd = os.getenv("DASHBOARD_BASIC_PASSWORD", "")
        ok = hmac.compare_digest(user, expected_user) and hmac.compare_digest(passwd, expected_passwd)
        if ok:
            self._new_session_cookie = self._build_session_cookie()
        return ok

    def _query_int(self, parsed, *, key: str, default: int, min_value: int, max_value: int) -> int:
        qs = parse_qs(parsed.query or "")
        raw = (qs.get(key) or [""])[0].strip()
        if not raw:
            return default
        try:
            value = int(raw)
        except ValueError:
            return default
        return max(min_value, min(max_value, value))

    def _query_bool(self, parsed, *, key: str, default: bool) -> bool:
        qs = parse_qs(parsed.query or "")
        raw = (qs.get(key) or [""])[0].strip().lower()
        if not raw:
            return default
        return raw in {"1", "true", "yes", "on"}

    def _query_date(self, parsed, *, key: str) -> str | None:
        qs = parse_qs(parsed.query or "")
        raw = (qs.get(key) or [""])[0].strip()
        if not raw:
            return None
        try:
            _ = date.fromisoformat(raw)
        except ValueError:
            return None
        return raw

    def do_GET(self) -> None:  # noqa: N802
        self._new_session_cookie = None
        parsed = urlparse(self.path)
        if not self._is_authorized(parsed):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="solar-dashboard"')
            self._send_security_headers()
            self.end_headers()
            self.wfile.write(b'{"error":"unauthorized"}')
            return

        path = parsed.path
        if parsed.query and "auth=" in parsed.query and (path == "/" or path == "/index.html"):
            self.send_response(302)
            self.send_header("Location", "/")
            self._maybe_send_auth_cookie()
            self._send_security_headers()
            self.end_headers()
            return

        if path == "/" or path == "/index.html":
            try:
                db_path = Path(_env("DATA_DB_PATH", "artifacts/solar_monitor.db"))
                sliced = load_dashboard_slice(
                    db_path,
                    end_date=None,
                    window_days=31,
                    include_static=True,
                )
                payload = {
                    **sliced.data.__dict__,
                    "meta": sliced.meta,
                }
            except Exception:
                print("dashboard root render error")
                print(traceback.format_exc())
                payload = _empty_dashboard_payload()
            script_nonce = secrets.token_urlsafe(16)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self._maybe_send_auth_cookie()
            self._send_security_headers(script_nonce=script_nonce)
            self.end_headers()
            self.wfile.write(_html(payload, script_nonce=script_nonce).encode("utf-8"))
            return
        if path == "/api/dashboard":
            try:
                db_path = Path(_env("DATA_DB_PATH", "artifacts/solar_monitor.db"))
                window_days = self._query_int(
                    parsed,
                    key="window_days",
                    default=31,
                    min_value=1,
                    max_value=365,
                )
                include_static = self._query_bool(parsed, key="include_static", default=True)
                end_date = self._query_date(parsed, key="end_date")
                sliced: DashboardSlice = load_dashboard_slice(
                    db_path,
                    end_date=end_date,
                    window_days=window_days,
                    include_static=include_static,
                )
                body = json.dumps(
                    {
                        **sliced.data.__dict__,
                        "meta": sliced.meta,
                    },
                    ensure_ascii=False,
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._maybe_send_auth_cookie()
                self._send_security_headers()
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                print("dashboard api error")
                print(traceback.format_exc())
                self.send_response(500)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self._send_security_headers()
                self.end_headers()
                self.wfile.write(b'{"error":"internal_error"}')
            return

        self.send_response(404)
        self._send_security_headers()
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        _ = (fmt, args)


def main() -> int:
    host = _env("DASHBOARD_HOST", "127.0.0.1")
    port = int(_env("DASHBOARD_PORT", "8080"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Dashboard server running on http://{host}:{port}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
