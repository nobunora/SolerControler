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
        "energy_daily": [],
        "cost_daily": [],
        "cost_monthly": [],
        "battery_daily": [],
        "model_parameters": [],
        "latest_schedule": {},
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
    .timeline-scroll {
      overflow-x: auto;
      overflow-y: hidden;
      height: 20px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: #f8fbff;
    }
    .timeline-track {
      height: 1px;
      min-width: 100%;
    }
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
    @media (max-width: 900px) {
      .grid { grid-template-columns: 1fr; }
      .full { grid-column: auto; }
      .chart-box { height: 240px; }
      .hero h1 { font-size: 19px; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>おうち発電ダッシュボード</h1>
      <p>初期表示は直近1か月。横スクロールで過去データを必要な分だけ読み込みます。</p>
      <p id="statusMsg" class="desc"></p>
    </section>

    <section class="grid">
      <article class="card full">
        <h2>0. 制約管理ガントチャート（最新設定）</h2>
        <p class="desc">固定制約と最新設定の時間帯を重ねて表示します。既存グラフはこの下にそのまま表示します。</p>
        <div class="gantt-wrap"><div id="constraintGantt" class="gantt-board"></div></div>
      </article>

      <article class="card full">
        <h2>表示期間スクロール（直近1か月表示）</h2>
        <p class="desc">左へスクロールすると過去データを自動取得します。</p>
        <div id="timelineScroll" class="timeline-scroll"><div id="timelineTrack" class="timeline-track"></div></div>
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
        <h2>2. 自家消費kWh（日）</h2>
        <p class="desc">青の棒: 日次自家消費、緑の折れ線: 累計自家消費。左右で縦軸を分けます。</p>
        <div class="chart-box"><canvas id="dailyKwhChart"></canvas></div>
      </article>

      <article class="card">
        <h2>3. 節約額（日）</h2>
        <p class="desc">橙の棒: 日次節約額、赤の折れ線: 累計節約額。左右で縦軸を分けます。</p>
        <div class="chart-box"><canvas id="dailyYenChart"></canvas></div>
      </article>

      <article class="card">
        <h2>4. 自家消費と節約額（月）</h2>
        <p class="desc">月単位の合計。左軸(kWh)、右軸(円)。</p>
        <div class="chart-box"><canvas id="monthlyCostChart"></canvas></div>
      </article>

      <article class="card">
        <h2>5. 蓄電池設定値と実績</h2>
        <p class="desc">左軸はkWh、右軸はSOC(%)。夜間充電量・PV蓄電余力と、目標SOC・日終SOCを分けて表示します。</p>
        <div class="chart-box"><canvas id="batteryChart"></canvas></div>
      </article>

      <article class="card full">
        <h2>6. 蓄電池方程式とパラメータ</h2>
        <div class="equation">
          変数: <b>SH</b>=日照時間[h], <b>TP</b>=気温[℃], <b>LD</b>=日中負荷[kWh], <b>RM</b>=朝負荷[kWh], <b>RS</b>=朝SOC[%], <b>RC</b>=目標SOC[%], <b>NC</b>=夜間充電[kWh], <b>PS</b>=日中余剰PV[kWh], <b>HT</b>=的中率[%]<br>
          (1) PV予測: <b>PV = SH × Kp × Kt</b><br>
          (2) 朝不足: <b>DF = max(0, RM - PV × Kr)</b><br>
          (3) 日中余剰: <b>PS = max(0, (PV - LD) × Ks)</b><br>
          (4) 7時目標SOC: <b>RC = clip(Rsv + (DF - PS) / Cp × 100, 0, 100)</b><br>
          (5) 夜間充電量: <b>NC = max(0, ((RC - RS)/100 × Cp) / Ef)</b><br>
          条件A: 23-07は放電禁止、07-23は放電許可。 条件B: 23時設定は06:00終了固定で逆算。 条件C: 充電開始は00:00未満にしない。<br>
          条件管理: <b>config/operation_conditions.json</b>（fixed=固定条件、variable=変動条件、priority=優先順位）<br>
          最優先固定条件: <b>0時跨ぎ禁止</b> / <b>開始=終了禁止</b><br>
          的中率: <b>HT = max(0, 1 - sMAPE(SH実績, SH予測) / 2) × 100</b>
        </div>
        <table id="paramsTable">
          <thead>
            <tr><th>短縮</th><th>パラメータ</th><th>意味</th><th>中心値</th><th>分散</th><th>サンプル数</th><th>的中率</th></tr>
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
    const PX_PER_DAY = 20;

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
      for (let i = 0; i < 380; i += 1) {
        out.push(cur);
        if (cur >= endDate) break;
        cur = isoDateAdd(cur, 1);
      }
      return out;
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
      energy: new Map(),
      cost: new Map(),
      battery: new Map(),
      monthly: [],
      params: [],
      latestSchedule: null,
      dates: [],
      loadingOlder: false,
    };

    const timeline = {
      scroll: null,
      track: null,
      syncing: false,
      raf: 0,
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
      battery_temp_coeff_per_deg: { code: "Bt", label: "気温1℃あたり蓄電容量補正係数" },
      battery_cycle_capacity_fade_per_cycle: { code: "Cf", label: "1サイクルあたり容量劣化率" },
    };

    function mergeRows(map, rows) {
      for (const row of rows || []) {
        if (!row || !row.date) continue;
        map.set(String(row.date), row);
      }
    }

    function rebuildDateIndex() {
      const all = new Set();
      for (const k of store.sunshine.keys()) all.add(k);
      for (const k of store.energy.keys()) all.add(k);
      for (const k of store.cost.keys()) all.add(k);
      for (const k of store.battery.keys()) all.add(k);
      const today = todayIsoJst();
      store.dates = Array.from(all).filter((d) => d <= today).sort();
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
      mergeRows(store.energy, payload.energy_daily || []);
      mergeRows(store.cost, payload.cost_daily || []);
      mergeRows(store.battery, payload.battery_daily || []);
      if (includeStatic) {
        store.monthly = payload.cost_monthly || [];
        store.params = payload.model_parameters || [];
        store.latestSchedule = payload.latest_schedule || store.latestSchedule;
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
      const chargeEnd = minuteOf(sch.charge_end_time, minuteOf("06:00", 360));
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

      const pickForecastDate = () => {
        if (sch.plan_date && store.sunshine.has(sch.plan_date)) return sch.plan_date;
        let newest = null;
        for (const d of store.sunshine.keys()) {
          if (!newest || d > newest) newest = d;
        }
        return newest;
      };
      const forecastDate = pickForecastDate();
      const forecastRow = forecastDate ? (store.sunshine.get(forecastDate) || {}) : {};
      const forecastHoursRaw = forecastRow && forecastRow.forecast_hours != null ? Number(forecastRow.forecast_hours) : null;
      const forecastHours = Number.isFinite(forecastHoursRaw) ? Math.max(0, forecastHoursRaw) : null;
      const forecastSegments = [];
      if (forecastHours != null) {
        const daySpan = dayEnd > dayStart ? (dayEnd - dayStart) : (1440 - dayStart + dayEnd);
        const forecastMinutes = Math.round(Math.min(daySpan, forecastHours * 60.0));
        if (forecastMinutes > 0) {
          const forecastEnd = (dayStart + forecastMinutes) % 1440;
          pushRange(
            forecastSegments,
            dayStart,
            forecastEnd,
            "bar-forecast",
            `予測日照 ${forecastHours.toFixed(1)}h (${forecastDate})`
          );
        }
      }

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
      const noteMeta = `更新: ${sch.recorded_at || "-"} / スロット: ${sch.slot || "-"} / プロファイル: ${sch.profile || sch.mode || "-"}`;
      const notePlan = chargeStart == null
        ? "夜間充電の開始時刻は未記録のため、最新の夜間充電量からの推定または未表示になる場合があります。"
        : "";
      const noteForecast = forecastHours == null
        ? "日照予測データが未取得のため、予測レイヤーは未表示です。"
        : "";

      root.innerHTML = `
        <div class="gantt-axis"><div>時間（JST）</div><div class="gantt-hours">${hours.join("")}</div></div>
        ${renderRow("実行計画", planSegments)}
        ${renderRow("日照予測", forecastSegments)}
        ${renderRow("制約レイヤー", fixedSegments)}
        <div class="gantt-notes">${noteSoc}<br>${noteMeta}${fixedNotes ? `<br>${fixedNotes}` : ""}${notePlan ? `<br>${notePlan}` : ""}${noteForecast ? `<br>${noteForecast}` : ""}</div>
      `;
    }

    function ensureTimelineWidth(keepRight = false) {
      const scroll = timeline.scroll;
      const track = timeline.track;
      if (!scroll || !track) return;
      const oldRightGap = keepRight ? (scroll.scrollWidth - scroll.clientWidth - scroll.scrollLeft) : 0;
      const logicalDays = Math.max(WINDOW_DAYS + 1, store.dates.length);
      track.style.width = `${Math.max(scroll.clientWidth + 2, logicalDays * PX_PER_DAY)}px`;
      if (keepRight) {
        const maxLeft = Math.max(0, scroll.scrollWidth - scroll.clientWidth);
        scroll.scrollLeft = Math.max(0, maxLeft - oldRightGap);
      }
    }

    function fillParamsTable() {
      const tbody = document.querySelector("#paramsTable tbody");
      tbody.innerHTML = "";
      for (const p of store.params) {
        const tr = document.createElement("tr");
        const hit = p.hit_rate == null ? "未算出" : `${(n(p.hit_rate) * 100).toFixed(1)}%`;
        const meta = paramAlias[String(p.name || "")] || { code: "-", label: "補助パラメータ" };
        const values = [
          String(meta.code),
          String(p.name ?? ""),
          String(meta.label),
          n(p.mean_value).toFixed(4),
          n(p.variance).toFixed(6),
          String(n(p.sample_count, 0)),
          hit,
        ];
        for (const value of values) {
          const td = document.createElement("td");
          td.textContent = value;
          tr.appendChild(td);
        }
        tbody.appendChild(tr);
      }
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
          { label: "夜間充電量(kWh)", data: [], yAxisID: "y", borderColor: "#ef8e1d", backgroundColor: "#ef8e1d", tension: 0.25 },
          { label: "太陽光 最大蓄電量(kWh)", data: [], yAxisID: "y", borderColor: "#14b86f", backgroundColor: "#14b86f", tension: 0.25 },
          { label: "日終SOC(%)", data: [], yAxisID: "y2", borderColor: "#e6504f", backgroundColor: "#e6504f", tension: 0.25, pointRadius: 3, pointHoverRadius: 5, borderWidth: 3, spanGaps: true },
        ]},
        options: {
          ...commonOptions(),
          scales: {
            y: { min: 0, max: 1, title: { display: true, text: "kWh" }, grid: { color: "#d8e6f2" } },
            y2: { min: 0, max: 100, position: "right", title: { display: true, text: "SOC(%)" }, grid: { drawOnChartArea: false } },
          },
        },
      });
    }

    function selectedWindowDates() {
      const dates = store.dates;
      if (!dates.length) return [];
      const scroll = timeline.scroll;
      const maxStart = Math.max(0, dates.length - WINDOW_DAYS);
      const maxLeft = Math.max(1, scroll.scrollWidth - scroll.clientWidth);
      const ratio = maxLeft > 0 ? (scroll.scrollLeft / maxLeft) : 1;
      const startIdx = Math.max(0, Math.min(maxStart, Math.round(ratio * maxStart)));
      const endAnchor = dates[Math.min(dates.length - 1, startIdx + WINDOW_DAYS - 1)];
      const startDate = isoDateAdd(endAnchor, -(WINDOW_DAYS - 1));
      const today = todayIsoJst();
      return buildDateRange(startDate, endAnchor).filter((d) => d <= today);
    }

    function rowByDate(map, day) {
      return map.get(day) || null;
    }

    function buildContinuousCumulativeSeries(labels, valueByDay) {
      const allDates = store.dates;
      if (!allDates.length || !labels.length) {
        return labels.map(() => 0);
      }
      const firstLabel = labels[0];
      let running = 0;
      for (const d of allDates) {
        if (d >= firstLabel) break;
        running += n(valueByDay.get(d));
      }
      const out = [];
      for (const d of labels) {
        running += n(valueByDay.get(d));
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

    function renderWindow() {
      const labels = selectedWindowDates();
      if (!labels.length) {
        setStatus("データがまだありません。23時ジョブ実行後に表示されます。", "#e6504f");
        return;
      }
      const sunForecast = labels.map((d) => {
        const r = rowByDate(store.sunshine, d);
        return r && r.forecast_hours != null ? n(r.forecast_hours) : null;
      });
      const sunActual = labels.map((d) => {
        const r = rowByDate(store.sunshine, d);
        return r && r.actual_hours != null ? n(r.actual_hours) : null;
      });
      const sunDiff = labels.map((d) => {
        const r = rowByDate(store.sunshine, d);
        return n(r && r.actual_hours) - n(r && r.forecast_hours);
      });

      updateForecastActualChart(charts.sun, labels, sunForecast, sunActual, sunDiff, "h");

      const pvForecast = labels.map((d) => {
        const r = rowByDate(store.energy, d);
        return r && r.forecast_pv_kwh != null ? n(r.forecast_pv_kwh) : null;
      });
      const pvActual = labels.map((d) => {
        const r = rowByDate(store.energy, d);
        return r && r.actual_pv_kwh != null ? n(r.actual_pv_kwh) : null;
      });
      const pvDiff = labels.map((_d, i) => diffOrNull(pvActual[i], pvForecast[i]));
      updateForecastActualChart(charts.pv, labels, pvForecast, pvActual, pvDiff, "kWh");

      const loadForecast = labels.map((d) => {
        const r = rowByDate(store.energy, d);
        return r && r.forecast_load_kwh != null ? n(r.forecast_load_kwh) : null;
      });
      const loadActual = labels.map((d) => {
        const r = rowByDate(store.energy, d);
        return r && r.actual_load_kwh != null ? n(r.actual_load_kwh) : null;
      });
      const loadDiff = labels.map((_d, i) => diffOrNull(loadActual[i], loadForecast[i]));
      updateForecastActualChart(charts.load, labels, loadForecast, loadActual, loadDiff, "kWh");

      const dailySelf = labels.map((d) => n(rowByDate(store.cost, d)?.self_consumption_kwh));
      const dailyYen = labels.map((d) => n(rowByDate(store.cost, d)?.savings_yen));
      const selfByDay = new Map();
      const yenByDay = new Map();
      for (const [d, row] of store.cost.entries()) {
        selfByDay.set(d, n(row && row.self_consumption_kwh));
        yenByDay.set(d, n(row && row.savings_yen));
      }
      const cumKwh = buildContinuousCumulativeSeries(labels, selfByDay);
      const cumYen = buildContinuousCumulativeSeries(labels, yenByDay);

      charts.dailyKwh.data.labels = labels;
      charts.dailyKwh.data.datasets[0].data = dailySelf;
      charts.dailyKwh.data.datasets[1].data = cumKwh;
      const dailyKwhDual = dualScales(dailySelf, cumKwh, { leftUnit: "kWh", rightUnit: "kWh" });
      charts.dailyKwh.options.scales.y = {
        ...dailyKwhDual.y,
        ticks: { ...dailyKwhDual.y.ticks, color: "#147efb" },
        border: { color: "#147efb" },
        title: { display: true, text: "日次 kWh", color: "#147efb" },
      };
      charts.dailyKwh.options.scales.y2 = {
        ...dailyKwhDual.y2,
        ticks: { ...dailyKwhDual.y2.ticks, color: "#14b86f" },
        border: { color: "#14b86f" },
        title: { display: true, text: "累計 kWh", color: "#14b86f" },
      };
      charts.dailyKwh.update("none");

      charts.dailyYen.data.labels = labels;
      charts.dailyYen.data.datasets[0].data = dailyYen;
      charts.dailyYen.data.datasets[1].data = cumYen;
      const dailyYenDual = dualScales(dailyYen, cumYen, { leftUnit: "円", rightUnit: "円" });
      charts.dailyYen.options.scales.y = {
        ...dailyYenDual.y,
        ticks: { ...dailyYenDual.y.ticks, color: "#ef8e1d" },
        border: { color: "#ef8e1d" },
        title: { display: true, text: "日次 円", color: "#ef8e1d" },
      };
      charts.dailyYen.options.scales.y2 = {
        ...dailyYenDual.y2,
        ticks: { ...dailyYenDual.y2.ticks, color: "#e6504f" },
        border: { color: "#e6504f" },
        title: { display: true, text: "累計 円", color: "#e6504f" },
      };
      charts.dailyYen.update("none");

      const toNumOrNull = (v) => (v == null || v === "" ? null : n(v));
      const batteryTarget = labels.map((d) => {
        const r = rowByDate(store.battery, d);
        return r ? toNumOrNull(r.setting_soc_target_percent) : null;
      });
      const batteryNight = labels.map((d) => {
        const r = rowByDate(store.battery, d);
        return r ? toNumOrNull(r.night_charge_kwh) : null;
      });
      const batteryPvMax = labels.map((d) => {
        const r = rowByDate(store.battery, d);
        return r ? toNumOrNull(r.pv_max_charge_kwh) : null;
      });
      const batteryEndSoc = labels.map((d) => {
        const r = rowByDate(store.battery, d);
        if (!r) return null;
        return toNumOrNull(r.end_of_day_soc_percent);
      });

      charts.battery.data.labels = labels;
      charts.battery.data.datasets[0].data = batteryTarget;
      charts.battery.data.datasets[1].data = batteryNight;
      charts.battery.data.datasets[2].data = batteryPvMax;
      charts.battery.data.datasets[3].data = batteryEndSoc;
      charts.battery.data.datasets[3].hidden = !batteryEndSoc.some((v) => v != null);
      const batterySoc = [
        ...batteryTarget.filter((v) => v != null),
        ...batteryEndSoc.filter((v) => v != null),
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

      setStatus(`表示期間: ${labels[0]} 〜 ${labels[labels.length - 1]}`);
    }

    function renderMonthly() {
      const monthLabels = store.monthly.map((x) => x.month);
      const monthKwh = store.monthly.map((x) => n(x.self_consumption_kwh));
      const monthYen = store.monthly.map((x) => n(x.savings_yen));
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

    async function loadOlderIfNeeded() {
      if (store.loadingOlder || !store.meta || !store.meta.has_more_before || !store.meta.oldest_loaded_date) {
        return;
      }
      const left = timeline.scroll.scrollLeft;
      if (left > 24) return;
      store.loadingOlder = true;
      const prevOldest = store.meta.oldest_loaded_date;
      try {
        const endDate = isoDateAdd(prevOldest, -1);
        const payload = await fetchSlice({ window_days: CHUNK_DAYS, end_date: endDate, include_static: false });
        absorbSlice(payload, false);
        ensureTimelineWidth(true);
        renderWindow();
      } catch (_err) {
        setStatus("過去データの読込に失敗しました", "#e6504f");
      } finally {
        store.loadingOlder = false;
      }
    }

    function onTimelineScroll() {
      if (timeline.syncing) return;
      if (timeline.raf) {
        cancelAnimationFrame(timeline.raf);
      }
      timeline.raf = requestAnimationFrame(async () => {
        renderWindow();
        await loadOlderIfNeeded();
      });
    }

    async function main() {
      timeline.scroll = document.getElementById("timelineScroll");
      timeline.track = document.getElementById("timelineTrack");
      buildCharts();

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
      renderConstraintGantt();
      renderMonthly();
      ensureTimelineWidth(false);
      timeline.syncing = true;
      timeline.scroll.scrollLeft = Math.max(0, timeline.scroll.scrollWidth - timeline.scroll.clientWidth);
      timeline.syncing = false;
      renderWindow();
      timeline.scroll.addEventListener("scroll", onTimelineScroll, { passive: true });

      const resizeAll = () => {
        ensureTimelineWidth(true);
        renderConstraintGantt();
        for (const c of Object.values(charts)) c.resize();
        renderWindow();
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
