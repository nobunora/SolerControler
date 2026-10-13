    // 画面の既定は1請求月、分割取得は応答量を抑えつつ長期間表示を可能にする単位。
    const WINDOW_DAYS = 31;
    const CHUNK_DAYS = 120;
    // バックエンドと同じ既定締め日を使い、集計月の境界が画面ごとにずれないようにする。
    const DEFAULT_AGGREGATION_CLOSE_DAY = 14;
    const { allocateNightGridCharge, plannedBatteryValues, forecastSocFromLatestActual, forecastNightSocAtHourStart } = window.DashboardCalculations;
    const dashboardDates = window.DashboardDates;
    const dashboardApi = window.DashboardApi;

    function n(v, d = 0) {
      const x = Number(v);
      if (!Number.isFinite(x)) return d;
      return x;
    }

    function niceCeil(v) {
      if (!Number.isFinite(v) || v <= 0) return 1;
      const p = Math.pow(10, Math.floor(Math.log10(v)));
      const m = v / p;
      let c = 10;
      if (m <= 1) c = 1;
      else if (m <= 2) c = 2;
      else if (m <= 2.5) c = 2.5;
      else if (m <= 5) c = 5;
      return c * p;
    }

    function cleanAxisNumber(value) {
      return Number(Number(value).toPrecision(12));
    }

    function minMaxBounds(values, options = {}) {
      const intervals = Math.max(2, Math.round(n(options.intervals, 5)));
      const paddingRatio = Math.max(0, n(options.paddingRatio, 0.1));
      const minZero = options.minZero === true;
      const finite = values.map(Number).filter(Number.isFinite);
      if (!finite.length) return { min: 0, max: 1, stepSize: 1 / intervals, intervals };

      const dataMin = minZero ? 0 : Math.min(...finite);
      const dataMax = Math.max(...finite);
      let paddedMin;
      let paddedMax;
      if (dataMin === dataMax) {
        const padding = Math.max(1, Math.abs(dataMax) * paddingRatio);
        paddedMin = minZero ? 0 : dataMin - padding;
        paddedMax = dataMax + padding;
      } else {
        const padding = (dataMax - dataMin) * paddingRatio;
        paddedMin = minZero ? 0 : dataMin - padding;
        paddedMax = dataMax + padding;
      }

      let stepSize = niceCeil((paddedMax - paddedMin) / intervals);
      let axisMin = minZero ? 0 : Math.floor(paddedMin / stepSize) * stepSize;
      let axisMax = axisMin + stepSize * intervals;
      while (axisMax < paddedMax) {
        stepSize = niceCeil(stepSize * (1 + 1e-9));
        axisMin = minZero ? 0 : Math.floor(paddedMin / stepSize) * stepSize;
        axisMax = axisMin + stepSize * intervals;
      }
      return {
        min: cleanAxisNumber(axisMin),
        max: cleanAxisNumber(axisMax),
        stepSize: cleanAxisNumber(stepSize),
        intervals,
      };
    }

    function formatChartValue(value) {
      const x = n(value);
      if (Math.abs(x) >= 100) return Math.round(x).toString();
      if (Math.abs(x) >= 10) return x.toFixed(1);
      return x.toFixed(2);
    }

    function dualScales(leftValues, rightValues, options = {}) {
      const leftUnit = options.leftUnit || "";
      const rightUnit = options.rightUnit || "";
      const intervals = Math.max(2, Math.round(n(options.intervals, 5)));
      const fixedBounds = (max) => ({ min: 0, max, stepSize: cleanAxisNumber(max / intervals), intervals });
      const leftBounds = options.leftMax == null
        ? minMaxBounds(leftValues, {
            intervals,
            minZero: options.minZero === true || options.leftMinZero === true,
            paddingRatio: options.paddingRatio,
          })
        : fixedBounds(n(options.leftMax));
      const rightBounds = options.rightMax == null
        ? minMaxBounds(rightValues, {
            intervals,
            minZero: options.minZero === true || options.rightMinZero === true,
            paddingRatio: options.paddingRatio,
          })
        : fixedBounds(n(options.rightMax));
      return {
        y: {
          min: leftBounds.min,
          max: leftBounds.max,
          ticks: { count: intervals + 1, stepSize: leftBounds.stepSize, callback: (v) => `${v}${leftUnit}` },
          title: { display: !!leftUnit, text: leftUnit.replace(/[()]/g, "") },
          grid: { color: "#d8e6f2" },
        },
        y2: {
          min: rightBounds.min,
          max: rightBounds.max,
          position: "right",
          ticks: { count: intervals + 1, stepSize: rightBounds.stepSize, callback: (v) => `${v}${rightUnit}` },
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

    function displayFinite(value) {
      return typeof value === "number" && Number.isFinite(value) ? value : null;
    }

    function ensureVisibleChartRanges(chart) {
      for (const dataset of chart.data.datasets) {
        dataset.spanGaps = false;
        dataset.tension = 0;
      }
      for (const [axisName, axis] of Object.entries(chart.options.scales || {})) {
        if (!axisName.startsWith("y")) continue;
        if (axisName === "y2" && (chart === charts.hourlyForecast || chart === charts.battery)) {
          axis.min = 0;
          axis.max = 100;
          continue;
        }
        const values = [];
        const stacks = new Map();
        for (const dataset of chart.data.datasets) {
          if ((dataset.yAxisID || "y") !== axisName) continue;
          const stack = dataset.stack || "default";
          const totals = stacks.get(stack) || [];
          dataset.data.forEach((point, index) => {
            const value = displayFinite(point && typeof point === "object" ? point.y : point);
            if (value === null) return;
            values.push(value);
            if (axis.stacked) {
              const total = totals[index] || { positive: 0, negative: 0 };
              if (value >= 0) total.positive += value;
              else total.negative += value;
              totals[index] = total;
            }
          });
          stacks.set(stack, totals);
        }
        if (axis.stacked) {
          for (const totals of stacks.values()) {
            for (const total of totals) {
              if (total) values.push(total.positive, total.negative);
            }
          }
        }
        if (!values.length) continue;
        const minimum = Math.min(...values);
        const maximum = Math.max(...values);
        if (Number.isFinite(axis.min) && Number.isFinite(axis.max) && axis.min <= minimum && axis.max >= maximum) continue;
        const intervals = Number.isInteger(axis.ticks?.count) && axis.ticks.count >= 3 ? axis.ticks.count - 1 : 5;
        const bounds = minMaxBounds([...values, ...(Number.isFinite(axis.min) ? [axis.min] : []),
          ...(Number.isFinite(axis.max) ? [axis.max] : [])], { intervals });
        axis.min = bounds.min;
        axis.max = bounds.max;
        axis.ticks = { ...axis.ticks, count: intervals + 1, stepSize: bounds.stepSize };
        if (!Number.isFinite(axis.min) || !Number.isFinite(axis.max) || !Number.isFinite(axis.ticks.stepSize)) {
          axis.min = minimum;
          axis.max = maximum;
          axis.ticks.stepSize = maximum / intervals - minimum / intervals;
        }
      }
    }

    function isoDateAdd(dateStr, deltaDays) {
      return dashboardDates.isoDateAdd(dateStr, deltaDays);
    }

    function buildDateRange(startDate, endDate) {
      const out = [];
      let cur = startDate;
      // 異常な日付入力でも無限ループにせず、通常の利用期間を十分に超える上限で打ち切る。
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

    function todayIsoJst() {
      return dashboardDates.todayIsoJst();
    }

    const store = window.DashboardStore.createStore();

    const periodState = window.DashboardStore.createPeriodState();

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

    function mergeRows(map, rows, overwrite = true) {
      for (const row of rows || []) {
        if (!row || !row.date) continue;
        const date = String(row.date);
        if (!overwrite && map.has(date)) continue;
        map.set(date, row);
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
      return await dashboardApi.fetchSlice({
        window_days: options.window_days ?? WINDOW_DAYS,
        end_date: options.end_date ?? "",
        include_static: !!options.include_static,
      });
    }

    function absorbSlice(payload, includeStatic) {
      const overwriteExisting = !!includeStatic;
      mergeRows(store.pvDaily, payload.pv_daily || [], overwriteExisting);
      mergeHourlyRows(payload.forecast_hourly || []);
      mergeRows(store.energy, payload.energy_daily || [], overwriteExisting);
      mergeRows(store.cost, payload.cost_daily || [], overwriteExisting);
      mergeRows(store.battery, payload.battery_daily || [], overwriteExisting);
      mergeRows(store.batteryFlow, payload.battery_flow_daily || [], overwriteExisting);
      if (includeStatic) {
        store.monthly = payload.cost_monthly || [];
        store.params = payload.model_parameters || [];
        store.latestSchedule = payload.latest_schedule || store.latestSchedule;
        store.dashboardWarnings = payload.dashboard_warnings || [];
        store.pvForecastDiagnostics = payload.pv_forecast_diagnostics || {};
        store.dailyReviews.clear();
        for (const review of payload.daily_reviews || []) {
          if (review && review.date) store.dailyReviews.set(String(review.date), review);
        }
        store.dailyReview = payload.daily_review || {};
        if (store.dailyReview.date && !store.dailyReviews.has(String(store.dailyReview.date))) {
          store.dailyReviews.set(String(store.dailyReview.date), store.dailyReview);
        }
        if (!store.latestReviewDate && store.dailyReview.date) {
          store.latestReviewDate = String(store.dailyReview.date);
        }
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
      // All fallback times mirror the backend's 23:00-07:00 night and 07:00-23:00 day windows.
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
      const dateLabel = document.getElementById("dailyReviewDate");
      const prevButton = document.getElementById("dailyReviewPrevBtn");
      const nextButton = document.getElementById("dailyReviewNextBtn");
      const grid = document.getElementById("dailyReviewGrid");
      const findings = document.getElementById("dailyReviewFindings");
      if (!note || !grid || !findings) return;
      if (dateLabel) dateLabel.textContent = review.date || "データなし";
      const reviewDates = Array.from(store.dailyReviews.keys()).sort();
      const reviewIndex = review.date ? reviewDates.indexOf(String(review.date)) : -1;
      if (prevButton) prevButton.disabled = reviewIndex <= 0;
      if (nextButton) {
        nextButton.disabled = reviewIndex < 0 || reviewIndex >= reviewDates.length - 1;
      }
      if (!review.date) {
        note.textContent = "24時間分が揃った予実レビュー対象日がまだありません。";
        grid.innerHTML = "";
        findings.innerHTML = "";
        return;
      }
      const kwh = (value) => displayFinite(value) === null ? "-" : `${value.toFixed(2)} kWh`;
      const pct = (value) => displayFinite(value) === null ? "-" : `${value.toFixed(0)}%`;
      const yen = (value) => displayFinite(value) === null ? "-" : `${Math.round(value).toLocaleString("ja-JP")} 円`;
      const signedKwh = (value) => `${n(value) >= 0 ? "+" : ""}${n(value).toFixed(2)} kWh`;
      const pvGap = displayFinite(review.forecast_pv_kwh) === null || displayFinite(review.actual_pv_kwh) === null
        || review.forecast_available_hour_count !== 24 || review.complete_day !== true ? null : review.actual_pv_kwh - review.forecast_pv_kwh;
      const chargeGap = displayFinite(review.forecast_night_charge_kwh) === null || displayFinite(review.actual_night_charge_kwh) === null ? null : review.actual_night_charge_kwh - review.forecast_night_charge_kwh;
      const arrival = (item) => {
        if (!item || !item.date) return "期間内到達なし";
        return `${item.date}${item.status === "forecast" ? "（予想）" : "（到達）"}`;
      };
      const analogTemps = review.analog_max_temp_c == null && review.analog_min_temp_c == null
        ? "気温 -"
        : `最高 ${review.analog_max_temp_c == null ? "-" : `${n(review.analog_max_temp_c).toFixed(1)}℃`} / 最低 ${review.analog_min_temp_c == null ? "-" : `${n(review.analog_min_temp_c).toFixed(1)}℃`}`;
      const analogSub = review.analog_date
        ? `類似度 ${review.analog_similarity == null ? "-" : `${(n(review.analog_similarity) * 100).toFixed(1)}%`}・${review.analog_weather || "-"}・${analogTemps}`
        : "類似日データなし";
      const billingPeriod = review.billing_period_start && review.billing_period_end
        ? `${review.billing_period_start}〜${review.billing_period_end}`
        : "";
      const items = [
        { label: "設定SOC（予想）", value: pct(review.target_soc_percent) },
        { label: "朝7時SOC（実績）", value: pct(review.actual_morning_soc_percent) },
        { label: "夜間充電", value: `${kwh(review.forecast_night_charge_kwh)} / 実績 ${kwh(review.actual_night_charge_kwh)}`, sub: chargeGap == null ? "" : `差分 ${signedKwh(chargeGap)}` },
        { label: "PV発電", value: `${kwh(review.forecast_pv_kwh)} / 実績 ${kwh(review.actual_pv_kwh)}`, sub: pvGap == null ? "" : `差分 ${signedKwh(pvGap)}` },
        { label: "家の消費", value: `${kwh(review.forecast_load_kwh)} / 実績 ${kwh(review.actual_load_kwh)}` },
        { label: "日中買電", value: `${kwh(review.forecast_day_buy_kwh)} / 実績 ${kwh(review.actual_day_buy_kwh)}` },
        { label: "売電", value: `${kwh(review.forecast_sell_kwh)} / 実績 ${kwh(review.actual_sell_kwh)}` },
        { label: "SOC範囲", value: `${pct(review.actual_soc_min_percent)} - ${pct(review.actual_soc_max_percent)}` },
        { label: "夜間電力累計", value: kwh(review.cumulative_night_buy_kwh), sub: billingPeriod },
        { label: "昼間電力累計", value: kwh(review.cumulative_day_buy_kwh), sub: billingPeriod },
        { label: "電力量料金予想", value: yen(review.projected_energy_cost_yen), sub: `昼 ${yen(review.projected_day_cost_yen)} / 夜 ${yen(review.projected_night_cost_yen)}` },
        { label: "段階到達日", value: `第2 ${arrival(review.tier2_arrival)} / 第3 ${arrival(review.tier3_arrival)}` },
        { label: "最も近い類似日", value: review.analog_date || "-", sub: analogSub, wide: true },
      ];
      grid.innerHTML = items.map((item) => `<div class="diag-kpi${item.wide ? " diag-kpi-wide" : ""}"><span class="diag-label">${escapeHtml(item.label)}</span><span class="diag-value">${escapeHtml(item.value)}</span>${item.sub ? `<small class="diag-subvalue">${escapeHtml(item.sub)}</small>` : ""}</div>`).join("");
      const coverage = Number.isInteger(review.forecast_available_hour_count) && review.forecast_available_hour_count >= 0
        && review.forecast_available_hour_count <= 24 ? review.forecast_available_hour_count : null;
      const coverageText = coverage === null ? "予測区間数 未確認（旧資料）"
        : coverage === 24 ? "予測24/24区間" : `予測は部分値 ${coverage}/24区間`;
      const bindingText = review.plan_forecast_binding === "matched" ? "計画と予測の原本一致"
        : review.plan_forecast_binding === "mismatched" ? "計画と予測の原本不一致（該当する計画予想値は未確認）"
          : "計画と予測の由来未確認（旧資料）";
      findings.textContent = `${bindingText}。${coverageText}。PV/loadは全日24区間、日中買電・売電予想は07〜22時の計画です。`;
      const actualText = review.complete_day === true ? "00:00〜23:30の48件が揃った保存済みKP-NETデータです。"
        : "実績は保存済み取得分です。48件の完全日は未確認です。";
      note.textContent = `${review.date} の予測 / 実績。${actualText}${coverageText}。料金予想は基本料金・燃料費調整額・再エネ賦課金を除く電力量料金です。`;
    }

    function moveDailyReview(deltaDays) {
      const current = store.dailyReview && store.dailyReview.date ? String(store.dailyReview.date) : "";
      if (!current) return;
      const reviewDates = Array.from(store.dailyReviews.keys()).sort();
      const currentIndex = reviewDates.indexOf(current);
      const nextIndex = currentIndex + (deltaDays < 0 ? -1 : 1);
      if (currentIndex < 0 || nextIndex < 0 || nextIndex >= reviewDates.length) return;
      store.dailyReview = store.dailyReviews.get(reviewDates[nextIndex]) || store.dailyReview;
      renderDailyReview();
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
      const needsOlder =
        store.meta &&
        store.meta.oldest_loaded_date &&
        store.meta.oldest_loaded_date > startDate;

      if (needsOlder && !store.historyLoaded && !store.loadingOlder) {
        store.loadingOlder = true;
        try {
          setStatus("履歴データを読み込んでいます...");
          const payload = await dashboardApi.fetchHistory();
          absorbSlice(payload, false);
          store.historyLoaded = true;
        } catch {
          setStatus("履歴スナップショットの読込に失敗したため、従来方式で取得します。", "#ef8e1d");
        } finally {
          store.loadingOlder = false;
        }
      }

      // Rollout/failure fallback: keep the legacy chunk API available until a
      // precomputed history snapshot has been generated successfully.
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
        } catch {
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

    function isForecastRow(row) {
      return (typeof row.forecast_run_id === "string" && !!row.forecast_run_id.trim())
        || [row.forecast_pv_kwh, row.forecast_load_kwh, row.forecast_charge_kwh, row.forecast_soc_percent]
          .some((value) => displayFinite(value) !== null);
    }

    function bindPvModelToForecast(summary, rows, date) {
      if (!summary || !["valid", "legacy", "invalid", "unsupported"].includes(summary.status)) {
        return { status: "unverified", reason: "モデル要約未連携" };
      }
      if (summary.plan_date !== date) return { status: "unverified", reason: "対象計画日が一致しません" };
      if (summary.status !== "valid") return { status: summary.status, reason: summary.reason_code || "" };
      if (summary.model_id !== "physical_clear_ghi_annual1" || !Array.isArray(summary.theta4)
          || summary.theta4.length !== 4 || summary.theta4.some((value) => displayFinite(value) === null)
          || !Array.isArray(summary.terms) || summary.terms.length !== 4
          || summary.terms.some((term, index) => term !== ["1", "x", "cos_phi", "sin_phi"][index])
          || !Number.isInteger(summary.training_day_count) || summary.training_day_count < 1
          || summary.training_sample_count !== 13 * summary.training_day_count
          || summary.rank !== 4 || displayFinite(summary.condition_number) === null
          || !Array.isArray(summary.repaired_hours) || summary.repaired_hours.length > 24
          || summary.repaired_hours.some((hour, index) => !Number.isInteger(hour) || hour < 0 || hour > 23
            || (index > 0 && hour <= summary.repaired_hours[index - 1]))) {
        return { status: "invalid", reason: "モデル要約の値が不正です" };
      }
      const forecastRows = rows.filter(isForecastRow);
      if (forecastRows.length !== 24 || forecastRows.some((row) => row.date !== date || !Number.isInteger(row.hour)
          || row.hour < 0 || row.hour > 23) || new Set(forecastRows.map((row) => row.hour)).size !== 24) {
        return { status: "unverified", reason: "対象日の24区間が揃っていません" };
      }
      if (typeof summary.forecast_run_id !== "string" || !summary.forecast_run_id.trim()
          || forecastRows.some((row) => typeof row.forecast_run_id !== "string"
            || !row.forecast_run_id.trim() || row.forecast_run_id !== summary.forecast_run_id)) {
        return { status: "unverified", reason: "予測の生成元が未確認または混在しています" };
      }
      return { status: "valid", reason: "" };
    }

    function renderPvModelSummary(summary, binding) {
      const setText = (id, text) => {
        const element = document.getElementById(id);
        if (element) element.textContent = text;
      };
      const valid = binding.status === "valid";
      const legacy = binding.status === "legacy";
      setText("pvModelName", valid ? "PVモデル: NEW100（晴天・年周期モデル）"
        : legacy ? "PVモデル: 旧方式（保存モデル）" : `PVモデル: 未確認（${binding.reason || binding.status}）`);
      setText("pvModelProvenance", summary ? `対象計画日: ${summary.plan_date || "不明"} / 計画生成: ${summary.generated_at || "不明"}${valid ? ` / モデル版: ${summary.basis_version} / 係数版: ${summary.calibration_sha256}` : ""}` : "モデル要約が未連携です。");
      setText("pvModelLearningPeriod", valid ? `認定晴天 ${summary.training_day_count}日 / 訓練 ${summary.training_sample_count}時間区間 / 学習期間 ${summary.training_start_date}〜${summary.training_end_date}` : "NEW100の学習原本は未確認です。旧パラメータの保存回数とは異なります。");
      setText("pvModelTiming", valid ? `学習cutoff: ${summary.training_as_of} / 係数版生成: ${summary.created_at} / 利用開始: ${summary.usable_from} / 予報取得完了: ${summary.retrieved_at} / run cutoff: ${summary.as_of} / 提供者発行: ${summary.provider_issued_at || "不明"}（原本時刻・末尾offset表記。画面の再描画時刻とは別）` : "学習・予報取得時刻は未確認です。");
      setText("pvModelRepair", valid ? `修復区間: ${summary.repaired_hours.length ? summary.repaired_hours.map((hour) => `${String(hour).padStart(2, "0")}:00〜`).join("、") : "なし"}` : "修復区間は未確認です。");
      setText("pvModelExplanation", valid
        ? "PV(h)=max(0,C(d,h))*min(F(h),Q(d,h))。C=theta0+theta1*x+theta2*cos(phi)+theta3*sin(phi)、x=(h+0.5-12)/6、phi=2π*(年内通算日-1)/(365または366)。Qは晴天水平日射、Fは直前1時間の予報日射。旧GTI/PR/PV温度補正は掛けません。"
        : legacy ? "旧方式のPV予測: Σ array(capacity × GTI(t)/1000 × PR × 補正係数 × 温度補正)。保存モデルの説明であり、NEW100の式ではありません。"
          : "保存モデルと対象計画の対応が未確認のため、PV式を断定しません。発電・負荷・実績の正常な保存値は表示します。");
    }

    function fillLearningParamsTable(diag = {}, summary = null, binding = { status: "unverified" }) {
      const tbody = document.querySelector("#learningParamsTable tbody");
      if (!tbody) return;
      tbody.innerHTML = "";
      const newModel = binding.status === "valid";
      const legacy = binding.status === "legacy";
      const shownNumber = (value) => displayFinite(value) === null ? "-" : String(value);
      if (newModel) {
        addLearningParameterRow(tbody, "NEW100", "認定晴天日数", summary.training_day_count, `${summary.training_day_count} 日`, "校正原本の日数。計画保存回数・誤差学習件数とは別");
        addLearningParameterRow(tbody, "NEW100", "訓練区間数", summary.training_sample_count, `${summary.training_sample_count} 時間区間`, "完全な13時間区間 × 認定日数");
        summary.theta4.forEach((value, index) => addLearningParameterRow(tbody, "NEW100係数", `theta${index} (${summary.terms[index]})`, shownNumber(value), `${summary.training_sample_count} 時間区間`, "同計画の係数原本。符号を保持"));
        addLearningParameterRow(tbody, "NEW100", "rank / condition", `${summary.rank} / ${summary.condition_number}`, `${summary.training_day_count} 日`, "保存された回帰診断。画面で再計算しない");
      }
      for (const p of store.params) {
        const name = String(p.name || "");
        if (staticParameterNames.has(name)) continue;
        const meta = paramAlias[name] || { label: "計画ごとに更新される補助係数" };
        addLearningParameterRow(
          tbody,
          name.startsWith("pv_forecast_error_ratio_") && newModel ? "不確実性の保存設定参照／実績学習とは別"
            : name.startsWith("pv_") || name.startsWith("physical_pv_") ? "旧方式履歴" : "蓄電池・補助係数",
          meta.label,
          shownNumber(p.mean_value),
          displayFinite(p.sample_count) === null ? "-" : `${p.sample_count} 計画保存回`,
          `${name}${name === "pv_forecast_error_ratio_sample_count" ? "（誤差sample 0は晴天学習0・完全精度を意味しない）" : ""}`,
        );
      }
      const physical = diag.physical || {};
      const scales = physical.scales || {};
      const quality = physical.data_quality || {};
      const correction = diag.forecast_correction || {};
      const overnight = diag.overnight_load_forecast || {};
      if (legacy) {
        addLearningParameterRow(tbody, "旧物理PV", "短波放射変換係数", shownNumber(scales.radiation_scale), `${scales.radiation_scale_fit?.sample_count ?? "-"} 有効区間`, "短波放射からPVへの変換");
        addLearningParameterRow(tbody, "旧物理PV", "全体バイアス補正", shownNumber(scales.global_bias_scale), `${quality.global_days ?? "-"} 有効日`, "旧物理PVの実績比による全体補正");
        for (const [period, item] of Object.entries(scales.daypart || {})) {
          addLearningParameterRow(tbody, "旧物理PV", `時間帯補正 (${period})`, shownNumber(item?.scale), `${item?.count ?? "-"} 有効日`, "旧朝・昼・夕の実績比補正");
        }
        addLearningParameterRow(tbody, "旧PV補正", "PV比率EWMA", shownNumber(correction.pv_ratio_ewma_raw), `${correction.pv_sample_count ?? "-"} 有効日`, "旧PV候補の実績比。NEW100へ適用しない");
      }
      addLearningParameterRow(tbody, "負荷補正", "負荷比率EWMA", shownNumber(correction.load_ratio_ewma_applied), `${correction.load_sample_count ?? "-"} 有効日`, "日別の消費予測に掛ける実績比補正");
      const evening = correction.evening_load_temperature || {};
      addLearningParameterRow(tbody, "負荷補正", "夜間気温補正倍率", shownNumber(evening.multiplier), `${evening.sample_count ?? "-"} 有効日`, "気温と夜間負荷残差から求める補正");
      addLearningParameterRow(tbody, "夜間負荷", "翌朝までの予測消費", displayFinite(overnight.expected_kwh) === null ? "-" : `${overnight.expected_kwh.toFixed(2)} kWh`, `${overnight.sample_count ?? "-"} 有効日`, "23:00-07:00の実績から推定");
    }

    function fillLearningParams() {
      const diag = store.pvForecastDiagnostics || {};
      const date = hourlyForecastDate();
      const rows = date ? store.hourly.get(date) || [] : [];
      const summary = diag.pv_model || null;
      const binding = bindPvModelToForecast(summary, rows, date);
      renderPvModelSummary(summary, binding);
      fillLearningParamsTable(diag, summary, binding);
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
                const number = displayFinite(ctx.parsed && ctx.parsed.y);
                const value = number === null ? "未確認" : formatChartValue(number);
                const coverage = ctx.chart?.forecastCoverage?.[ctx.dataIndex];
                return `${label ? `${label}: ` : ""}${value}${coverage ? ` / ${coverage}` : ""}`;
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

    function updateForecastActualChart(chart, labels, forecast, actual, diff, unit, options = {}) {
      chart.data.labels = labels;
      chart.data.datasets[0].data = forecast;
      chart.data.datasets[1].data = actual;
      chart.data.datasets[2].data = diff;
      const values = [...forecast, ...actual, ...diff].filter((v) => v != null);
      const bounds = minMaxBounds(values, { paddingRatio: options.paddingRatio });
      const axisMax = options.axisMax;
      const stepSize = axisMax == null
        ? bounds.stepSize
        : cleanAxisNumber(axisMax / bounds.intervals);
      const roundedMin = Math.floor(bounds.min / stepSize) * stepSize;
      const axisMin = axisMax == null ? bounds.min : (roundedMin < 0 ? roundedMin : 0);
      const chartMax = axisMax == null ? bounds.max : axisMax;
      const tickCount = Math.round((chartMax - axisMin) / stepSize) + 1;
      chart.options.scales.y.min = axisMin;
      chart.options.scales.y.max = chartMax;
      chart.options.scales.y.grid = {
        color: (ctx) => (ctx.tick && ctx.tick.value === 0 ? "#6d7f91" : "#d8e6f2"),
        lineWidth: (ctx) => (ctx.tick && ctx.tick.value === 0 ? 2.6 : 1),
      };
      chart.options.scales.y.ticks = {
        count: tickCount,
        stepSize,
        callback: (v) => `${v}${unit}`,
      };
      ensureVisibleChartRanges(chart);
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

    function hourlyDisplayState(rows, date, schedule) {
      const energy = (value) => displayFinite(value) !== null && value >= 0 ? value : null;
      const summarize = (key, forecastOnly) => {
        const byHour = new Map();
        for (const row of rows) {
          if (row.date !== date || !Number.isInteger(row.hour) || row.hour < 0 || row.hour > 23
              || (forecastOnly && !isForecastRow(row))) continue;
          const value = energy(row[key]);
          if (value !== null) byHour.set(row.hour, value);
        }
        return { count: byHour.size, total: byHour.size ? [...byHour.values()].reduce((a, b) => a + b, 0) : null };
      };
      const pv = summarize("forecast_pv_kwh", true);
      const load = summarize("forecast_load_kwh", true);
      const charge = summarize("forecast_charge_kwh", true);
      const actualPv = summarize("actual_pv_kwh", false);
      const actualLoad = summarize("actual_load_kwh", false);
      const knownRuns = new Set(rows.filter(isForecastRow).map((row) => row.forecast_run_id)
        .filter((run) => typeof run === "string" && run.trim()));
      const mixedRuns = knownRuns.size > 1;
      const saved = rows.map((row) => row.date === date && Number.isInteger(row.hour) && row.hour >= 7 && row.hour <= 23
        && displayFinite(row.forecast_soc_percent) !== null && row.forecast_soc_percent >= 0
        && row.forecast_soc_percent <= 100 ? row.forecast_soc_percent : null);
      const hasSaved = saved.some((point) => point !== null);
      const invalidSaved = rows.some((row) => row.forecast_soc_percent != null
        && (displayFinite(row.forecast_soc_percent) === null || row.forecast_soc_percent < 0 || row.forecast_soc_percent > 100));
      const points = mixedRuns ? rows.map(() => null) : saved;
      const pointSources = rows.map((row, index) => points[index] !== null ? "saved_plan"
        : displayFinite(row.actual_soc_percent) !== null && row.actual_soc_percent >= 0
          && row.actual_soc_percent <= 100 ? "observed" : "unavailable");
      return {
        points, pointSources, hasSaved, invalidSaved, mixedRuns,
        savedCount: new Set(rows.filter((_row, index) => points[index] !== null).map((row) => row.hour)).size,
        forecastComplete: pv.count === 24 && load.count === 24,
        forecastCount: Math.min(pv.count, load.count), pvCount: pv.count, loadCount: load.count,
        actualCount: actualLoad.count, actualPvCount: actualPv.count,
        forecastPvTotal: pv.total, forecastLoadTotal: load.total, forecastChargeTotal: charge.total, chargeCount: charge.count,
        actualPvSubtotal: actualPv.total, actualLoadSubtotal: actualLoad.total,
        boundSchedule: schedule && schedule.plan_date === date ? schedule : null,
      };
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
        ensureVisibleChartRanges(chart);
        chart.update("none");
        return;
      }
      const state = hourlyDisplayState(rows, date, store.latestSchedule);
      const labels = rows.map((row) => `${String(Number(row.hour)).padStart(2, "0")}:00`);
      const energy = (value) => displayFinite(value) !== null && value >= 0 ? value : null;
      const pv = rows.map((row) => energy(row.forecast_pv_kwh));
      const charge = rows.map((row) => energy(row.forecast_charge_kwh));
      const load = rows.map((row) => {
        const actual = energy(row.actual_load_kwh);
        return actual !== null ? actual : energy(row.forecast_load_kwh);
      });
      const batteryRow = store.battery.get(date);
      const scheduleNight = energy(state.boundSchedule?.planned_night_charge_kwh);
      const nightAmount = scheduleNight !== null ? scheduleNight : energy(batteryRow?.night_charge_kwh);
      const nightGridCharge = state.boundSchedule && nightAmount !== null
        ? estimateHourlyNightGridCharge(rows, date) : rows.map(() => null);
      let soc = state.points;
      let socKind = state.hasSaved && !state.mixedRuns ? "計画SOC" : "SOC未連携";
      let estimated = null;
      let actualAnchoredEstimate = false;
      if (!state.hasSaved && !state.invalidSaved && !state.mixedRuns) {
        if (state.boundSchedule) {
          estimated = estimateHourlyForecastSoc(rows, date);
          socKind = "参考SOC（保存計画未連携）";
        } else {
          const capacities = new Set(rows.map((row) => row.date === date ? displayFinite(row.forecast_capacity_kwh) : null)
            .filter((value) => value !== null && value > 0));
          if (capacities.size === 1 && state.pointSources.includes("observed")) {
            const efficiency = Math.sqrt(Math.max(0.5, Math.min(1.0, modelParam("battery_round_trip_efficiency", 0.9))));
            estimated = forecastSocFromLatestActual(rows, [...capacities][0], efficiency, efficiency);
            actualAnchoredEstimate = true;
            socKind = "最新実測起点の参考SOC（保存計画未連携）";
          }
        }
      }
      if (estimated !== null) {
        let dependencyMissing = false;
        soc = estimated.map((point, index) => {
          const row = rows[index];
          const observed = state.pointSources[index] === "observed";
          if (actualAnchoredEstimate && observed) {
            dependencyMissing = false;
            return null;
          }
          const value = !observed && !dependencyMissing && displayFinite(point) !== null && point >= 0 && point <= 100
            && isForecastRow(row) ? point : null;
          const completeInterval = (energy(row.forecast_charge_kwh) !== null && row.forecast_charge_kwh > 0)
            || (energy(row.forecast_pv_kwh) !== null && energy(row.forecast_load_kwh) !== null);
          if ((actualAnchoredEstimate || row.hour >= 7) && !completeInterval) {
            dependencyMissing = true;
          }
          return value;
        });
      }
      // HISTORICAL_FAILURE_LOCK (2026-10-14): 1dd21ae restored pre-07 SOC;
      // 848c3cf restored it again after the saved 07-23 plan suppressed it.
      // Always compose 00-06 SOC, even when a daytime optimizer trace exists.
      // Valid observations (including 0) take priority independently of plan availability.
      // Missing observations use only a valid bound night plan; preserve every 07-23 point.
      // Change only with explicit user approval and test_dashboard_hourly_display.js regression coverage.
      const capacities = new Set(rows.filter((row) => row.date === date)
        .map((row) => displayFinite(row.forecast_capacity_kwh)).filter((value) => value !== null && value > 0));
      const morningIndex = rows.findIndex((row) => row.date === date && row.hour === 7);
      const nightTarget = state.hasSaved ? state.points[morningIndex]
        : plannedBatteryValues(batteryRow, state.boundSchedule).targetSocPercent;
      const chargeEfficiency = Math.sqrt(Math.max(0.5, Math.min(1.0, modelParam("battery_round_trip_efficiency", 0.9))));
      const nightEstimate = !state.invalidSaved && !state.mixedRuns && state.boundSchedule && capacities.size === 1
        ? forecastNightSocAtHourStart(rows, nightGridCharge, nightTarget, [...capacities][0], chargeEfficiency) : null;
      soc = soc.map((point, index) => {
        const row = rows[index];
        if (row.date !== date || !Number.isInteger(row.hour) || row.hour < 0 || row.hour >= 7) return point;
        const actual = displayFinite(row.actual_soc_percent);
        if (actual !== null && actual >= 0 && actual <= 100) return actual;
        const reference = nightEstimate?.[index];
        return !state.invalidSaved && !state.mixedRuns && state.boundSchedule && isForecastRow(row)
          && displayFinite(reference) !== null && reference >= 0 && reference <= 100 ? reference : null;
      });
      // End of protected 00-06 SOC composition.
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
      chart.data.datasets[4].label = `${socKind === "SOC未連携" ? "計画SOC（未連携）" : socKind} / 夜間SOC（実測・参考）(%)`;
      chart.data.datasets[4].spanGaps = false;
      chart.options.scales.y = {
        min: 0,
        max: 5,
        ticks: { count: 6, stepSize: 1, callback: (v) => `${v}kWh/h` },
        title: { display: true, text: "kWh/h" },
        grid: { color: "#d8e6f2" },
      };
      chart.options.scales.y2 = {
        min: 0,
        max: 100,
        position: "right",
        ticks: { count: 6, stepSize: 20, color: "#9a7a00", callback: (v) => `${v}%` },
        title: { display: true, text: "予想SOC(%)", color: "#9a7a00" },
        grid: { drawOnChartArea: false },
        border: { color: "#9a7a00" },
      };
      if (note) {
        const sch = state.boundSchedule || {};
        const socPeak = maxForecastSocPoint(labels, socKind === "計画SOC" ? state.points
          : soc.map((point, index) => state.pointSources[index] === "observed" ? null : point));
        const actualText = `実績取得済み分 PV ${state.actualPvSubtotal === null ? "-" : state.actualPvSubtotal.toFixed(2)}kWh（${state.actualPvCount}/24） / 消費 ${state.actualLoadSubtotal === null ? "-" : state.actualLoadSubtotal.toFixed(2)}kWh（${state.actualCount}/24）`;
        const latestText = latestActual ? ` / 最新実績 ${latestActual.slice(11, 16)}` : "";
        const planUpdated = sch.plan_updated_at ? new Date(sch.plan_updated_at) : null;
        const planUpdatedText = planUpdated && !Number.isNaN(planUpdated.getTime())
          ? ` / 計画更新 ${planUpdated.toLocaleString("ja-JP", { timeZone: "Asia/Tokyo", hour12: false })}`
          : "";
        const socText = socPeak ? ` / ${socKind === "計画SOC" ? state.savedCount === 17 ? "計画SOC最大" : `取得済み計画SOC最大 ${state.savedCount}/17` : "参考SOC最大"} ${socPeak.label} ${socPeak.value.toFixed(0)}%` : ` / ${socKind}`;
        const totalText = (value, count) => count === 24 && value !== null ? `${value.toFixed(2)}kWh` : `未完 ${count}/24`;
        const nightText = state.boundSchedule && nightAmount !== null ? ` / 夜間系統充電 ${nightAmount.toFixed(2)}kWh` : " / 夜間系統充電 時間帯未連携";
        note.textContent = `${date} の時間別表示。全日発電予測 ${totalText(state.forecastPvTotal, state.pvCount)} / PV余剰充電 ${totalText(state.forecastChargeTotal, state.chargeCount)}${nightText} / 全日消費予測 ${totalText(state.forecastLoadTotal, state.loadCount)}${socText}。${actualText}${latestText}${planUpdatedText}。線の消費は実績優先。PV/loadは00〜23時の24区間、計画SOCは07開始〜23終了の17点（23時は22区間終了）。00〜06時のSOCは実測優先、未取得分は夜間充電計画からの参考値です。夜間系統充電は家の消費とは別の請求対象買電です。${state.mixedRuns ? " 予測生成元が混在しています。" : ""}`;
      }
      ensureVisibleChartRanges(chart);
      chart.update("none");
    }

    function estimateHourlyNightGridCharge(rows, date) {
      const batteryRow = date ? store.battery.get(date) : null;
      const sch = store.latestSchedule || {};
      const planned = plannedBatteryValues(batteryRow, sch);
      const totalKwh = Math.max(0, planned.nightChargeKwh);
      return allocateNightGridCharge(rows, totalKwh, sch.charge_start_time, sch.charge_end_time);
    }

    function estimateHourlyForecastSoc(rows, date) {
      const sch = store.latestSchedule || {};
      const batteryRow = date ? store.battery.get(date) : null;
      const targetSocRaw = plannedBatteryValues(batteryRow, sch).targetSocPercent;
      // Use the capacity frozen with this forecast, including its aging reductions.
      const forecastCapacities = Array.from(new Set(rows
        .map((row) => Number(row.forecast_capacity_kwh))
        .filter((value) => Number.isFinite(value) && value > 0)));
      const capacityKwh = Math.max(0.1, forecastCapacities.length === 1
        ? forecastCapacities[0] : modelParam("battery_usable_capacity_kwh", 9.0));
      const roundTripEff = Math.max(0.5, Math.min(1.0, modelParam("battery_round_trip_efficiency", 0.9)));
      const chargeEff = Math.sqrt(roundTripEff);
      const dischargeEff = Math.sqrt(roundTripEff);
      // HISTORICAL_FAILURE_LOCK (2026-09-05): a missing persisted plan must not
      // make the predicted-SOC series disappear when hourly actual SOC exists.
      if (!Number.isFinite(targetSocRaw)) {
        return forecastSocFromLatestActual(rows, capacityKwh, chargeEff, dischargeEff);
      }
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
          if (row.forecast_soc_percent != null) {
            const plannedSoc = Number(row.forecast_soc_percent);
            if (Number.isFinite(plannedSoc) && plannedSoc >= 0 && plannedSoc <= 100) {
              return Math.round(plannedSoc * 10) / 10;
            }
          }
          return Math.round((socAtHourStart / capacityKwh) * 1000) / 10;
        }
        if (hour < 7 && row.actual_soc_percent != null) {
          const actualSoc = Number(row.actual_soc_percent);
          if (Number.isFinite(actualSoc) && actualSoc >= 0 && actualSoc <= 100) return actualSoc;
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

      const chargeDays = dates.filter((day) => displayFinite(store.batteryFlow.get(day)?.charge_kwh) !== null).length;
      const dischargeDays = dates.filter((day) => displayFinite(store.batteryFlow.get(day)?.discharge_kwh) !== null).length;
      setLifeText("lifeTotalCharge", chargeDays ? totalCharge.toFixed(1) : "-", `kWh（取得済み${chargeDays}/${dates.length}日）`);
      setLifeText("lifeTotalDischarge", dischargeDays ? totalDischarge.toFixed(1) : "-", `kWh（取得済み${dischargeDays}/${dates.length}日）`);
      if (!dischargeDays) {
        for (const id of ["lifeEquivalentCycles", "lifeEstimatedCapacity", "lifeEightyPercentYear", "lifeMonthlyCycles"]) setLifeText(id, "未確認");
      }
      const monthObserved = monthKeys.map((month) => dates.some((day) => day.startsWith(month)
        && displayFinite(store.batteryFlow.get(day)?.discharge_kwh) !== null));

      chart.data.labels = labels;
      chart.data.datasets[0].data = capacities.map((value, index) => monthObserved[index] ? value : null);
      chart.data.datasets[1].data = cycleLosses.map((value, index) => monthObserved[index] ? value : null);
      chart.data.datasets[2].data = calendarLosses.map((value, index) => monthObserved[index] ? value : null);
      chart.data.datasets[3].data = tempLosses.map((value, index) => monthObserved[index] ? value : null);
      chart.options.scales.y.min = 0;
      chart.options.scales.y.max = Math.ceil(baseCapacity * 10) / 10;
      chart.options.scales.y.ticks = { color: "#147efb", callback: (v) => `${Number(v).toFixed(1)}` };
      ensureVisibleChartRanges(chart);
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
      const dynamicWeeklyAxis = isWeekly ? { paddingRatio: 0.15 } : { axisMax: 30 };
      const coverageNotes = buckets.map((bucket) => {
        const counts = bucket.dates.map((day) => store.energy.get(day)?.forecast_available_hour_count);
        const partial = counts.filter((count) => Number.isInteger(count) && count >= 0 && count < 24);
        const unknown = counts.filter((count) => !Number.isInteger(count) || count < 0 || count > 24).length;
        return `${partial.length ? `予測部分値 ${partial.join(",")}/24区間` : ""}${unknown ? `${partial.length ? " / " : ""}予測区間数不明 ${unknown}日` : ""}`;
      });
      charts.pv.forecastCoverage = coverageNotes;
      charts.load.forecastCoverage = coverageNotes;
      updateForecastActualChart(charts.pv, labels, pvForecast, pvActual, pvDiff, "kWh", dynamicWeeklyAxis);

      const loadForecast = buckets.map((bucket) => sumBucket(bucket, store.energy, "forecast_load_kwh"));
      const loadActual = buckets.map((bucket) => sumBucket(bucket, store.energy, "actual_load_kwh"));
      const loadDiff = labels.map((_d, i) => diffOrNull(loadActual[i], loadForecast[i]));
      updateForecastActualChart(
        charts.load,
        labels,
        loadForecast,
        loadActual,
        loadDiff,
        "kWh",
        isWeekly ? { paddingRatio: 0.15 } : { axisMax: 100 },
      );

      const dailySelf = buckets.map((bucket) => sumBucket(bucket, store.cost, "self_consumption_kwh"));
      const dailyYen = buckets.map((bucket) => sumBucket(bucket, store.cost, "savings_yen"));
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
      charts.dailyKwh.data.datasets[1].data = cumKwh.map((value, index) => dailySelf[index] === null ? null : value);
      charts.dailyKwh.data.datasets[1].label = "取得済み累計 自家消費(kWh)";
      const dailyKwhDual = dualScales(dailySelf, cumKwh, {
        leftUnit: "kWh",
        rightUnit: "kWh",
        leftMax: isWeekly ? null : 30,
        rightMinZero: !isWeekly,
        intervals: 6,
        paddingRatio: isWeekly ? 0.15 : undefined,
      });
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
      ensureVisibleChartRanges(charts.dailyKwh);
      charts.dailyKwh.update("none");

      const dailyYenTitle = document.getElementById("dailyYenTitle");
      const dailyYenDesc = document.getElementById("dailyYenDesc");
      if (dailyYenTitle) dailyYenTitle.textContent = `3. 節約額（${isWeekly ? "週" : "日"}）`;
      if (dailyYenDesc) dailyYenDesc.textContent = `橙の棒: ${bucketLabel}節約額、赤の折れ線: 累計節約額。左右で縦軸を分けます。`;

      charts.dailyYen.data.labels = labels;
      charts.dailyYen.data.datasets[0].label = `${bucketLabel} 節約額(円)`;
      charts.dailyYen.data.datasets[0].data = dailyYen;
      charts.dailyYen.data.datasets[1].data = cumYen.map((value, index) => dailyYen[index] === null ? null : value);
      charts.dailyYen.data.datasets[1].label = "取得済み累計 節約額(円)";
      const dailyYenDual = dualScales(dailyYen, cumYen, {
        leftUnit: "円",
        rightUnit: "円",
        leftMax: isWeekly ? null : 1500,
        rightMinZero: !isWeekly,
        intervals: 6,
        paddingRatio: isWeekly ? 0.15 : undefined,
      });
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
      ensureVisibleChartRanges(charts.dailyYen);
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
      const batteryKwh = batteryNight.filter((v) => v != null);
      const batteryDual = dualScales(batteryKwh, batterySoc, {
        leftUnit: "kWh",
        rightUnit: "%",
        leftMax: isWeekly ? null : 15,
        rightMax: isWeekly ? null : 100,
        intervals: 5,
        paddingRatio: isWeekly ? 0.15 : undefined,
      });
      charts.battery.options.scales.y = {
        ...batteryDual.y,
        ticks: { ...batteryDual.y.ticks, color: "#14b86f" },
        border: { color: "#14b86f" },
        title: { display: true, text: "kWh", color: "#14b86f" },
      };
      charts.battery.options.scales.y2 = {
        ...batteryDual.y2,
        ticks: { ...batteryDual.y2.ticks, color: "#147efb", callback: (v) => `${Math.round(v)}%` },
        border: { color: "#147efb" },
        title: { display: true, text: "SOC(%)", color: "#147efb" },
      };
      ensureVisibleChartRanges(charts.battery);
      charts.battery.update("none");

      renderBatteryLifeProjection(buckets);

      const coverageWarning = coverageNotes.some(Boolean) ? " / 予測区間に未完・旧資料の未確認があります（tooltip参照）" : "";
      setStatus(`表示期間: ${buckets[0].start} 〜 ${buckets[buckets.length - 1].end}${isWeekly ? "（週単位）" : ""}${coverageWarning}`);
    }

    function renderMonthly() {
      const isWeekly = periodState.mode === "year";
      const range = currentPeriodRange();
      const rows = range
        ? store.monthly.filter((x) => {
            const start = String(x.period_start || `${x.month || ""}-01`);
            const end = String(x.period_end || `${x.month || ""}-31`);
            return start <= range.end && end >= range.start;
          })
        : store.monthly;
      const monthLabels = rows.map((x) => x.month);
      const monthKwh = rows.map((x) => displayFinite(x.self_consumption_kwh));
      const monthYen = rows.map((x) => displayFinite(x.savings_yen));
      charts.monthly.data.labels = monthLabels;
      charts.monthly.data.datasets[0].data = monthKwh;
      charts.monthly.data.datasets[1].data = monthYen;
      const scales = dualScales(monthKwh, monthYen, {
        leftUnit: "kWh",
        rightUnit: "円",
        leftMax: isWeekly ? null : 800,
        rightMax: isWeekly ? null : 40000,
        intervals: 4,
        paddingRatio: isWeekly ? 0.15 : undefined,
      });
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
      ensureVisibleChartRanges(charts.monthly);
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
      fillLearningParams();
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

    function bindDailyReviewControls() {
      document.getElementById("dailyReviewPrevBtn")?.addEventListener("click", () => moveDailyReview(-1));
      document.getElementById("dailyReviewNextBtn")?.addEventListener("click", () => moveDailyReview(1));
    }

    async function main() {
      buildCharts();
      bindPeriodControls();
      bindDailyReviewControls();

      const initialPayload = window.__DASHBOARD_DATA__ || {};
      const hasInitialRows =
        (initialPayload.pv_daily && initialPayload.pv_daily.length) ||
        (initialPayload.energy_daily && initialPayload.energy_daily.length) ||
        (initialPayload.cost_daily && initialPayload.cost_daily.length) ||
        (initialPayload.battery_daily && initialPayload.battery_daily.length);
      if (hasInitialRows) {
        absorbSlice(initialPayload, true);
      }

      if (!hasInitialRows) {
        try {
          const bootstrap = await dashboardApi.fetchBootstrap();
          absorbSlice(bootstrap, true);
        } catch {
          if (!store.dates.length) {
            setStatus("データ読込に失敗しました（認証の再確認をお願いします）", "#e6504f");
            return;
          }
          setStatus("最新データの取得に失敗したため、取得済みデータで表示しています。", "#ef8e1d");
        }
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
