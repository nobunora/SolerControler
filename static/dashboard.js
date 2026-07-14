    const WINDOW_DAYS = 31;
    const CHUNK_DAYS = 120;
    const DEFAULT_AGGREGATION_CLOSE_DAY = 14;
    const { allocateNightGridCharge } = window.DashboardCalculations;

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
      pvDaily: new Map(),
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
      dailyReview: {},
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
      pv_direct_use_ratio: { code: "Kr", label: "朝のPV直接利用率" },
      pv_to_battery_ratio: { code: "Ks", label: "余剰PVの蓄電寄与率" },
      pv_self_consumption_ratio: { code: "Sc", label: "PV自家消費率" },
      pv_array_calibration_factor: { code: "Pa", label: "面別PV予測の実績補正係数" },
      pv_forecast_error_ratio_mean: { code: "Pem", label: "PV予測倍率の中心値（確率ではない）" },
      pv_forecast_error_ratio_std: { code: "Pes", label: "PV予測倍率のばらつき（確率ではない）" },
      pv_forecast_error_ratio_variance: { code: "Pev", label: "PV予測倍率の分散" },
      pv_forecast_error_ratio_sample_count: { code: "Pen", label: "PV予測誤差サンプル数" },
      physical_pv_radiation_scale: { code: "Prs", label: "短波放射からPVへ変換する物理モデル係数" },
      physical_pv_global_bias_scale: { code: "Pbs", label: "物理PV予測の全体バイアス補正係数" },
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
        (store.pvDaily.has(scheduleDate) ||
          store.hourly.has(scheduleDate) ||
          store.energy.has(scheduleDate) ||
          store.battery.has(scheduleDate));
      return hasScheduleForecast && scheduleDate > today ? scheduleDate : today;
    }

    function rebuildDateIndex() {
      const all = new Set();
      for (const k of store.pvDaily.keys()) all.add(k);
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
      mergeRows(store.pvDaily, payload.pv_daily || []);
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
        store.dailyReview = payload.daily_review || {};
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

    function renderDailyReview() {
      const review = store.dailyReview || {};
      const note = document.getElementById("dailyReviewNote");
      const grid = document.getElementById("dailyReviewGrid");
      const findings = document.getElementById("dailyReviewFindings");
      if (!note || !grid || !findings) return;
      if (!review.date) {
        note.textContent = "予実レビューに必要な実績データがまだありません。";
        grid.innerHTML = "";
        findings.innerHTML = "";
        return;
      }
      const kwh = (value) => value == null ? "-" : `${n(value).toFixed(2)} kWh`;
      const pct = (value) => value == null ? "-" : `${n(value).toFixed(0)}%`;
      const items = [
        ["設定SOC", pct(review.target_soc_percent)],
        ["夜間充電", `${kwh(review.forecast_night_charge_kwh)} / 実績 ${kwh(review.actual_night_charge_kwh)}`],
        ["PV発電", `${kwh(review.forecast_pv_kwh)} / 実績 ${kwh(review.actual_pv_kwh)}`],
        ["家の消費", `${kwh(review.forecast_load_kwh)} / 実績 ${kwh(review.actual_load_kwh)}`],
        ["日中買電", `${kwh(review.forecast_day_buy_kwh)} / 実績 ${kwh(review.actual_day_buy_kwh)}`],
        ["売電", `${kwh(review.forecast_sell_kwh)} / 実績 ${kwh(review.actual_sell_kwh)}`],
        ["SOC範囲", `${pct(review.actual_soc_min_percent)} - ${pct(review.actual_soc_max_percent)}`],
        ["PV予測モデル", review.forecast_source || "-"],
      ];
      grid.innerHTML = items.map(([label, value]) => `<div class="diag-kpi"><span class="diag-label">${escapeHtml(label)}</span><span class="diag-value">${escapeHtml(value)}</span></div>`).join("");
      const pvGap = review.forecast_pv_kwh == null || review.actual_pv_kwh == null ? null : n(review.actual_pv_kwh) - n(review.forecast_pv_kwh);
      const chargeGap = review.forecast_night_charge_kwh == null || review.actual_night_charge_kwh == null ? null : n(review.actual_night_charge_kwh) - n(review.forecast_night_charge_kwh);
      const chips = [];
      if (pvGap != null) chips.push(`PV差分 ${pvGap >= 0 ? "+" : ""}${pvGap.toFixed(2)} kWh`);
      if (chargeGap != null) chips.push(`夜間充電差分 ${chargeGap >= 0 ? "+" : ""}${chargeGap.toFixed(2)} kWh`);
      if (review.actual_soc_max_percent != null) chips.push(`日中最大SOC ${pct(review.actual_soc_max_percent)}`);
      findings.innerHTML = chips.map((item) => `<span class="diag-chip">${escapeHtml(item)}</span>`).join("");
      const range = review.data_last_at ? `${String(review.data_last_at).slice(11, 16)}まで` : "終日";
      note.textContent = `${review.date} の予測 / 実績。実績は ${range} の保存済みKP-NETデータです。`;
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

    const staticParameterNames = new Set([
      "battery_cycle_capacity_fade_per_cycle",
      "battery_temp_coeff_per_deg",
      "pv_array_total_capacity_kw",
      "pv_kwh_per_sunhour",
      "pv_temp_coeff_per_deg",
    ]);

    function addLearningParameterRow(tbody, category, name, value, sampleCount, meaning) {
      const tr = document.createElement("tr");
      for (const cell of [category, name, value, sampleCount, meaning]) {
        const td = document.createElement("td");
        td.textContent = String(cell == null || cell === "" ? "-" : cell);
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }

    function fillLearningParamsTable(diag = {}) {
      const tbody = document.querySelector("#learningParamsTable tbody");
      if (!tbody) return;
      tbody.innerHTML = "";
      for (const p of store.params) {
        const name = String(p.name || "");
        if (staticParameterNames.has(name)) continue;
        const meta = paramAlias[name] || { label: "計画ごとに更新される補助係数" };
        addLearningParameterRow(
          tbody,
          "モデル係数",
          meta.label,
          n(p.mean_value).toFixed(4),
          n(p.sample_count, 0),
          name,
        );
      }
      const physical = diag.physical || {};
      const scales = physical.scales || {};
      const quality = physical.data_quality || {};
      const correction = diag.forecast_correction || {};
      const overnight = diag.overnight_load_forecast || {};
      addLearningParameterRow(tbody, "物理PV", "短波放射変換係数", scales.radiation_scale == null ? "-" : n(scales.radiation_scale).toFixed(4), scales.radiation_scale_fit?.sample_count, "短波放射からPVへの変換");
      addLearningParameterRow(tbody, "物理PV", "全体バイアス補正", scales.global_bias_scale == null ? "-" : n(scales.global_bias_scale).toFixed(4), quality.global_days, "物理PVの実績比による全体補正");
      for (const [period, item] of Object.entries(scales.daypart || {})) {
        addLearningParameterRow(tbody, "物理PV", `時間帯補正 (${period})`, item?.scale == null ? "-" : n(item.scale).toFixed(4), item?.count, "朝・昼・夕の実績比補正");
      }
      addLearningParameterRow(tbody, "PV補正", "PV比率EWMA", correction.pv_ratio_ewma_raw == null ? "-" : n(correction.pv_ratio_ewma_raw).toFixed(4), correction.pv_sample_count, "従来PV候補の実績比。物理PV採用時は二重適用しない");
      addLearningParameterRow(tbody, "負荷補正", "負荷比率EWMA", correction.load_ratio_ewma_applied == null ? "-" : n(correction.load_ratio_ewma_applied).toFixed(4), correction.load_sample_count, "日別の消費予測に掛ける実績比補正");
      const evening = correction.evening_load_temperature || {};
      addLearningParameterRow(tbody, "負荷補正", "夜間気温補正倍率", evening.multiplier == null ? "-" : n(evening.multiplier).toFixed(4), evening.sample_count, "気温と夜間負荷残差から求める補正");
      addLearningParameterRow(tbody, "夜間負荷", "翌朝までの予測消費", overnight.expected_kwh == null ? "-" : `${n(overnight.expected_kwh).toFixed(2)} kWh`, overnight.sample_count, "23:00-07:00の実績から推定");
    }

    function fillLearningParams() {
      fillLearningParamsTable(store.pvForecastDiagnostics || {});
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
            { label: "PV余剰充電予測(kWh/h)", data: [], borderColor: "#14b86f", backgroundColor: "#14b86f", tension: 0.25, pointRadius: 3 },
            { label: "家の消費 実績/予測(kWh/h)", data: [], borderColor: "#e6504f", backgroundColor: "#e6504f", tension: 0.25, pointRadius: 3 },
            { type: "bar", label: "夜間系統充電予測(kWh/h)", data: [], borderColor: "#6f7782", backgroundColor: "#6f778288", borderWidth: 1 },
            { label: "予想SOC(%)", data: [], yAxisID: "y2", borderColor: "#9a7a00", backgroundColor: "#9a7a00", tension: 0.25, pointRadius: 3, borderWidth: 2.5, spanGaps: true },
          ],
        },
        options: {
          ...commonOptions(),
          scales: {
            y: { min: 0, max: 1, title: { display: true, text: "kWh/h" }, grid: { color: "#d8e6f2" } },
            y2: {
              min: 0,
              max: 100,
              position: "right",
              title: { display: true, text: "予想SOC(%)", color: "#9a7a00" },
              ticks: { color: "#9a7a00", callback: (v) => `${v}%` },
              grid: { drawOnChartArea: false },
              border: { color: "#9a7a00" },
            },
          },
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
        chart.data.datasets[3].data = [];
        chart.data.datasets[4].data = [];
        if (note) note.textContent = "時間別予測データがまだ保存されていません。23時ジョブ実行後に表示されます。";
        chart.update("none");
        return;
      }
      const labels = rows.map((row) => `${String(Number(row.hour)).padStart(2, "0")}:00`);
      const pv = rows.map((row) => row.forecast_pv_kwh == null ? null : n(row.forecast_pv_kwh));
      const charge = rows.map((row) => row.forecast_charge_kwh == null ? null : n(row.forecast_charge_kwh));
      const load = rows.map((row) => row.actual_load_kwh == null ? (row.forecast_load_kwh == null ? null : n(row.forecast_load_kwh)) : n(row.actual_load_kwh));
      const nightGridCharge = estimateHourlyNightGridCharge(rows, date);
      const soc = estimateHourlyForecastSoc(rows, date);
      const actualHours = rows.filter((row) => row.actual_load_kwh != null).map((row) => Number(row.hour));
      const latestActual = rows
        .map((row) => row.latest_sample_at || "")
        .filter(Boolean)
        .sort()
        .pop();
      chart.data.labels = labels;
      chart.data.datasets[0].data = pv;
      chart.data.datasets[1].data = charge;
      chart.data.datasets[2].data = load;
      chart.data.datasets[3].data = nightGridCharge;
      chart.data.datasets[4].data = soc;
      const axisMax = niceCeil(Math.max(1, maxPos([...pv, ...charge, ...load, ...nightGridCharge].filter((v) => v != null))));
      chart.options.scales.y.min = 0;
      chart.options.scales.y.max = axisMax;
      chart.options.scales.y.ticks = { callback: (v) => `${v}kWh` };
      chart.options.scales.y2.min = 0;
      chart.options.scales.y2.max = 100;
      chart.options.scales.y2.ticks = { color: "#9a7a00", callback: (v) => `${v}%` };
      chart.options.scales.y2.title = { display: true, text: "予想SOC(%)", color: "#9a7a00" };
      if (note) {
        const totalPv = pv.reduce((acc, v) => acc + n(v), 0);
        const totalCharge = charge.reduce((acc, v) => acc + n(v), 0);
        const totalLoad = load.reduce((acc, v) => acc + n(v), 0);
        const batteryRow = date ? store.battery.get(date) : null;
        const nightCharge = batteryRow && batteryRow.night_charge_kwh != null ? n(batteryRow.night_charge_kwh) : null;
        const socPeak = maxForecastSocPoint(labels, soc);
        const actualText = actualHours.length
          ? `消費実績 ${String(Math.min(...actualHours)).padStart(2, "0")}:00-${String(Math.max(...actualHours)).padStart(2, "0")}:59`
          : "消費実績なし";
        const latestText = latestActual ? ` / 最新実績 ${latestActual.slice(11, 16)}` : "";
        const socText = socPeak ? ` / 予想SOCピーク ${socPeak.label}ごろ ${socPeak.value.toFixed(0)}%` : "";
        const nightText = nightCharge != null && nightCharge > 0 ? ` / 夜間系統充電 ${nightCharge.toFixed(2)}kWh` : "";
        note.textContent = `${date} の時間別表示。発電予測 ${totalPv.toFixed(2)}kWh / PV余剰充電 ${totalCharge.toFixed(2)}kWh${nightText} / 家の消費 ${totalLoad.toFixed(2)}kWh${socText}（${actualText}${latestText}、以降は予測。夜間系統充電は家の消費とは別の請求対象買電です）`;
      }
      chart.update("none");
    }

    function estimateHourlyNightGridCharge(rows, date) {
      const batteryRow = date ? store.battery.get(date) : null;
      const totalKwh = Math.max(0, Number(batteryRow && batteryRow.night_charge_kwh != null ? batteryRow.night_charge_kwh : 0) || 0);
      const sch = store.latestSchedule || {};
      return allocateNightGridCharge(rows, totalKwh, sch.charge_start_time, sch.charge_end_time);
    }

    function estimateHourlyForecastSoc(rows, date) {
      const sch = store.latestSchedule || {};
      const batteryRow = date ? store.battery.get(date) : null;
      const targetSocRaw = Number(
        (batteryRow && batteryRow.setting_soc_target_percent != null)
          ? batteryRow.setting_soc_target_percent
          : sch.soc_charge_mode
      );
      if (!Number.isFinite(targetSocRaw)) return rows.map(() => null);
      const capacityKwh = Math.max(0.1, modelParam("battery_usable_capacity_kwh", 9.0));
      const roundTripEff = Math.max(0.5, Math.min(1.0, modelParam("battery_round_trip_efficiency", 0.9)));
      const chargeEff = Math.sqrt(roundTripEff);
      const dischargeEff = Math.sqrt(roundTripEff);
      const targetSoc = Math.max(0, Math.min(100, targetSocRaw));
      const targetEnergyKwh = targetSoc / 100 * capacityKwh;
      const hourlyNightGridCharge = estimateHourlyNightGridCharge(rows, date);
      const nightChargeKwh = hourlyNightGridCharge.reduce((total, value) => total + n(value), 0);
      // setting_soc_target_percent is the 07:00 target, so estimate the pre-charge SOC by backing out night charging.
      const startEnergyKwh = Math.max(0, Math.min(capacityKwh, targetEnergyKwh - nightChargeKwh * chargeEff));
      let energyKwh = startEnergyKwh;
      return rows.map((row, index) => {
        const hour = Number(row.hour);
        const pvKwh = row.forecast_pv_kwh == null ? null : n(row.forecast_pv_kwh);
        const loadKwh = row.forecast_load_kwh == null ? null : n(row.forecast_load_kwh);
        const chargeKwh = row.forecast_charge_kwh == null ? null : n(row.forecast_charge_kwh);
        if (!Number.isFinite(hour)) return null;
        if (hourlyNightGridCharge[index] > 0) {
          energyKwh += hourlyNightGridCharge[index] * chargeEff;
        } else if (hour >= 7) {
          const socAtHourStart = Math.max(0, Math.min(capacityKwh, energyKwh));
          if (chargeKwh != null && chargeKwh > 0) {
            energyKwh += chargeKwh * chargeEff;
          } else if (pvKwh != null && loadKwh != null) {
            const net = pvKwh - loadKwh;
            if (net >= 0) energyKwh += net * chargeEff;
            else energyKwh += net / dischargeEff;
          }
          energyKwh = Math.max(0, Math.min(capacityKwh, energyKwh));
          return Math.round((socAtHourStart / capacityKwh) * 1000) / 10;
        }
        energyKwh = Math.max(0, Math.min(capacityKwh, energyKwh));
        return Math.round((energyKwh / capacityKwh) * 1000) / 10;
      });
    }

    function maxForecastSocPoint(labels, values) {
      let best = null;
      values.forEach((value, idx) => {
        if (value == null || !Number.isFinite(Number(value))) return;
        if (!best || Number(value) > best.value) best = { label: labels[idx], value: Number(value) };
      });
      return best;
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
          const pv = store.pvDaily.get(day);
          const actualTemp = valueOrNull(pv && pv.actual_temp_c);
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
      const batteryPvChargeEndSoc = buckets.map((bucket) => averageBucket(bucket, store.battery, "pv_charge_end_soc_percent"));

      const batteryTitle = document.getElementById("batteryTitle");
      const batteryDesc = document.getElementById("batteryDesc");
      if (batteryTitle) batteryTitle.textContent = `7. 蓄電池計画値と実績（${isWeekly ? "週次" : "日次"}）`;
      if (batteryDesc) {
        batteryDesc.textContent = `左軸はkWh/${perUnit}、右軸はSOC(%)。太陽光充電終了時SOCは、その日にPV発電中の充電が最後に発生した時点のSOCです。`;
      }

      charts.battery.data.labels = labels;
      charts.battery.data.datasets[0].label = isWeekly ? "平均設定SOC(%)" : "設定SOC(%)";
      charts.battery.data.datasets[1].label = `夜間充電計画(kWh/${perUnit})`;
      charts.battery.data.datasets[2].label = isWeekly ? "平均太陽光充電終了時SOC(%)" : "太陽光充電終了時SOC(%)";
      charts.battery.data.datasets[0].data = batteryTarget;
      charts.battery.data.datasets[1].data = batteryNight;
      charts.battery.data.datasets[2].data = batteryPvChargeEndSoc;
      charts.battery.data.datasets[2].hidden = !batteryPvChargeEndSoc.some((v) => v != null);
      const batterySoc = [
        ...batteryTarget.filter((v) => v != null),
        ...batteryPvChargeEndSoc.filter((v) => v != null),
      ];
      const batteryKwh = [
        ...batteryNight.filter((v) => v != null),
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
      renderDailyReview();
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
        (initialPayload.pv_daily && initialPayload.pv_daily.length) ||
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

      fillLearningParams();
      await refreshDashboard();

      const resizeAll = () => {
        renderConstraintGantt();
        renderDashboardWarnings();
        renderDailyReview();
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
