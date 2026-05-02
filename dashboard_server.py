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
        "cost_daily": [],
        "cost_monthly": [],
        "battery_daily": [],
        "model_parameters": [],
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
        <h2>表示期間スクロール（直近1か月表示）</h2>
        <p class="desc">左へスクロールすると過去データを自動取得します。</p>
        <div id="timelineScroll" class="timeline-scroll"><div id="timelineTrack" class="timeline-track"></div></div>
      </article>

      <article class="card">
        <h2>1. 日照時間（予測と実績）</h2>
        <p class="desc">青: 予測、緑: 実績、橙: 差分。差分は実績 - 予測です。</p>
        <div class="chart-box"><canvas id="sunChart"></canvas></div>
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
        <p class="desc">目標SOC、夜間充電量、昼のPV蓄電余力、日終SOCを並べて表示します。</p>
        <div class="chart-box"><canvas id="batteryChart"></canvas></div>
      </article>

      <article class="card full">
        <h2>6. 蓄電池方程式とパラメータ</h2>
        <div class="equation">
          目標充電量 = 「朝に足りない分」と「日中に余る分」を見て決める<br>
          夜間充電量(kWh) = max(0, (目標エネルギー - 現在エネルギー) / 充電効率)<br>
          太陽光発電(kWh) = 日照時間 × 発電係数 × 温度係数
        </div>
        <table id="paramsTable">
          <thead>
            <tr><th>パラメータ</th><th>中心値</th><th>分散</th><th>サンプル数</th><th>的中率</th></tr>
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
      cost: new Map(),
      battery: new Map(),
      monthly: [],
      params: [],
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

    function mergeRows(map, rows) {
      for (const row of rows || []) {
        if (!row || !row.date) continue;
        map.set(String(row.date), row);
      }
    }

    function rebuildDateIndex() {
      const all = new Set();
      for (const k of store.sunshine.keys()) all.add(k);
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
      mergeRows(store.cost, payload.cost_daily || []);
      mergeRows(store.battery, payload.battery_daily || []);
      if (includeStatic) {
        store.monthly = payload.cost_monthly || [];
        store.params = payload.model_parameters || [];
      }
      store.meta = payload.meta || store.meta;
      rebuildDateIndex();
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
        const hit = p.hit_rate == null ? "-" : `${(n(p.hit_rate) * 100).toFixed(1)}%`;
        const values = [
          String(p.name ?? ""),
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
                const value = Math.round(n(ctx.parsed && ctx.parsed.y));
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
          { label: "設定SOC(%)", data: [], borderColor: "#147efb", tension: 0.25 },
          { label: "夜間充電量(kWh)", data: [], borderColor: "#ef8e1d", tension: 0.25 },
          { label: "太陽光 最大蓄電量(kWh)", data: [], borderColor: "#14b86f", tension: 0.25 },
          { label: "日終SOC(%)", data: [], borderColor: "#e6504f", tension: 0.25 },
        ]},
        options: { ...commonOptions(), scales: { y: { beginAtZero: true, title: { display: true, text: "kWh / %" }, grid: { color: "#d8e6f2" } } } },
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

      charts.sun.data.labels = labels;
      charts.sun.data.datasets[0].data = sunForecast;
      charts.sun.data.datasets[1].data = sunActual;
      charts.sun.data.datasets[2].data = sunDiff;
      const sunAxisMax = niceCeil(Math.max(1, maxPos([...sunForecast.filter((v) => v != null), ...sunActual.filter((v) => v != null), ...sunDiff])));
      const sunAxisMin = Math.min(-sunAxisMax, Math.floor(Math.min(...sunDiff, 0)));
      charts.sun.options.scales.y.min = sunAxisMin;
      charts.sun.options.scales.y.max = sunAxisMax;
      charts.sun.options.scales.y.grid = {
        color: (ctx) => (ctx.tick && ctx.tick.value === 0 ? "#6d7f91" : "#d8e6f2"),
        lineWidth: (ctx) => (ctx.tick && ctx.tick.value === 0 ? 2.6 : 1),
      };
      charts.sun.options.scales.y.ticks = { callback: (v) => `${v}h` };
      charts.sun.update("none");

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

      charts.battery.data.labels = labels;
      charts.battery.data.datasets[0].data = labels.map((d) => n(rowByDate(store.battery, d)?.setting_soc_target_percent));
      charts.battery.data.datasets[1].data = labels.map((d) => n(rowByDate(store.battery, d)?.night_charge_kwh));
      charts.battery.data.datasets[2].data = labels.map((d) => n(rowByDate(store.battery, d)?.pv_max_charge_kwh));
      charts.battery.data.datasets[3].data = labels.map((d) => n(rowByDate(store.battery, d)?.end_of_day_soc_percent));
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
      renderMonthly();
      ensureTimelineWidth(false);
      timeline.syncing = true;
      timeline.scroll.scrollLeft = Math.max(0, timeline.scroll.scrollWidth - timeline.scroll.clientWidth);
      timeline.syncing = false;
      renderWindow();
      timeline.scroll.addEventListener("scroll", onTimelineScroll, { passive: true });

      const resizeAll = () => {
        ensureTimelineWidth(true);
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
