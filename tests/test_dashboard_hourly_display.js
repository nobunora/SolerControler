const assert = require("node:assert/strict");
const { harness, fixture } = require("./test_dashboard_model_display.js");

function summaryText(view) {
  return view.elements.get("hourlyForecastGrid").innerHTML.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
}

async function tests() {
  const h = await harness();
  const chart = h.chart("hourlyForecastChart");
  const soc = chart.data.datasets[4];
  assert.equal(chart.data.datasets.length, 11);
  assert.ok(chart.data.datasets[10].data.every((value) => value === null));
  chart.data.datasets.slice(0, 5).forEach((dataset) => assert.deepEqual(Array.from(dataset.borderDash), [2, 4]));
  assert.equal(chart.data.datasets[5].borderDash, undefined);
  for (const index of [0, 1, 2, 3, 4, 6, 7, 8, 9, 10]) {
    assert.equal(chart.data.datasets[index].pointRadius, 0);
    assert.equal(chart.data.datasets[index].pointHoverRadius, 0);
  }
  assert.equal(chart.data.datasets[5].pointRadius, 3, "instantaneous SOC keeps its observation points");
  for (const index of [4, 6, 10]) {
    assert.deepEqual(Array.from(chart.data.datasets[index].borderDash), [2, 4], "SOC forecasts remain dotted without markers");
  }
  const visible = Array(11).fill(true);
  chart.isDatasetVisible = (index) => visible[index];
  chart.setDatasetVisibility = (index, value) => { visible[index] = value; };
  const legend = chart.options.plugins.legend;
  const items = legend.labels.generateLabels(chart);
  assert.deepEqual(Array.from(items, (item) => item.text), [
    "発電 予測(kWh/h)", "発電 実績(kWh/h)", "家の消費 予測(kWh/h)", "家の消費 実績(kWh/h)",
    "SOC 計画(%)", "SOC 実績(%)", "PV余剰充電予測(kWh/h)", "夜間系統充電予測(kWh/h)",
  ]);
  assert.deepEqual(Array.from(items[0].lineDash), [2, 4]);
  assert.deepEqual(Array.from(items[1].lineDash), []);
  legend.onClick(null, items[4], { chart });
  assert.deepEqual(visible, [true, true, true, true, false, true, false, true, true, true, false]);
  legend.onClick(null, legend.labels.generateLabels(chart)[4], { chart });
  assert.ok(visible.every(Boolean), "SOC plan legend controls retained night and connector without hiding actuals");
  assert.deepEqual(Array.from(items[4].lineDash), [2, 4]);
  assert.deepEqual(Array.from(items[5].lineDash), []);
  legend.onClick(null, items[5], { chart });
  assert.equal(visible[5], false);
  assert.ok(visible.every((value, index) => index === 5 || value));
  legend.onClick(null, legend.labels.generateLabels(chart)[5], { chart });
  legend.onClick(null, items[7], { chart });
  assert.equal(visible[3], false);
  assert.equal(visible[7], false);
  legend.onClick(null, legend.labels.generateLabels(chart)[7], { chart });
  assert.equal(chart.data.labels.length, 24);
  assert.deepEqual(Array.from(soc.data.slice(0, 7)), Array(7).fill(null));
  assert.deepEqual(Array.from(chart.data.datasets[5].data.slice(0, 7)), Array(7).fill(40));
  assert.equal(soc.data[23], 99);
  assert.match(soc.label, /計画SOC/);
  assert.equal(soc.spanGaps, false);
  assert.match(summaryText(h), /計画SOC最大 99% 23:00/);
  assert.match(summaryText(h), /全日発電予測 0\.20 kWh/);
  assert.match(summaryText(h), /夜間系統充電予測 2\.00 kWh/);
  assert.equal((h.elements.get("hourlyForecastGrid").innerHTML.match(/class="diag-kpi"/g) || []).length, 9);
  assert.equal(h.elements.get("hourlyForecastNote").textContent, "2026-07-20 の時間別表示。");
  assert.doesNotMatch(h.template, /予測は点線、実績は実線/);
  const retained = fixture();
  retained.forecast_hourly.forEach((row) => {
    if (row.hour < 7) {
      row.retained_forecast_soc_percent = row.hour === 0 ? 0 : 35;
      row.retained_forecast_grid_charge_kwh = row.hour === 3 ? 1.2 : 0;
      row.retained_forecast_issued_at = "2026-07-19T23:00:00+09:00";
    }
  });
  await h.replace(retained);
  assert.equal(chart.data.datasets[6].data[0], 0);
  assert.equal(chart.data.datasets[7].data[3], 1.2);
  assert.deepEqual(Array.from(chart.data.datasets[6].borderDash), [2, 4]);
  assert.deepEqual(Array.from(soc.data.slice(7)), retained.forecast_hourly.slice(7).map((row) => row.forecast_soc_percent));
  const boundary = chart.data.datasets[10];
  assert.deepEqual(Array.from(boundary.borderDash), [2, 4]);
  assert.deepEqual(Array.from(boundary.data), retained.forecast_hourly.map((row) => row.hour === 6 ? 35 : row.hour === 7 ? row.forecast_soc_percent : null));
  assert.equal(chart.data.datasets[6].data[7], null, "the connector must not rewrite the retained source series");
  assert.doesNotMatch(h.elements.get("hourlyForecastNote").textContent, /6→7時.*別計画/);
  retained.forecast_hourly[6].retained_forecast_soc_percent = 0;
  await h.replace(retained);
  assert.equal(boundary.data[6], 0, "a saved zero is a valid endpoint");
  retained.forecast_hourly[6].retained_forecast_soc_percent = null;
  await h.replace(retained);
  assert.ok(boundary.data.every((value) => value === null), "missing night forecast must not use actual SOC");
  retained.forecast_hourly[6].retained_forecast_soc_percent = 35;
  const savedSeven = retained.forecast_hourly[7].forecast_soc_percent;
  retained.forecast_hourly[7].forecast_soc_percent = null;
  await h.replace(retained);
  assert.ok(boundary.data.every((value) => value === null), "missing daytime plan must not be interpolated");
  retained.forecast_hourly[7].forecast_soc_percent = savedSeven;
  retained.forecast_hourly[3].actual_soc_percent = 80;
  retained.forecast_hourly[3].actual_load_kwh = 0.7;
  await h.replace(retained);
  assert.equal(chart.data.datasets[6].data[3], 35, "imported actuals must not hide saved night SOC");
  assert.equal(boundary.data[6], 35);
  assert.equal(boundary.data[7], savedSeven);
  assert.equal(chart.data.datasets[7].data[3], 1.2, "imported actuals must not hide saved night grid charging");
  assert.equal(chart.data.datasets[2].data[3], 0.1, "load forecast remains visible beside actual load");
  assert.equal(chart.data.datasets[9].data[3], 0.7);
  assert.doesNotMatch(h.elements.get("hourlyForecastNote").textContent, /保存済み夜間予測の発行時刻/);

  // HISTORICAL_FAILURE_LOCK (2026-10-17): preserve night observations separately
  // from immutable saved plans, including zero and successive snapshots.
  const night = fixture();
  const nightPlan = [null, null, null, 0, 39, 39, 39];
  night.latest_schedule.charge_start_time = null;
  night.latest_schedule.charge_end_time = "06:45";
  night.forecast_hourly.forEach((row) => {
    row.actual_soc_percent = null;
    if (row.hour < 7) row.forecast_soc_percent = nightPlan[row.hour];
  });
  await h.replace(night);
  assert.deepEqual(Array.from(soc.data.slice(0, 7)), nightPlan);
  assert.ok(chart.data.datasets[3].data.every((value) => value === null),
    "missing start must not invent 04-07 charging");
  assert.deepEqual(Array.from(soc.data.slice(7)),
    night.forecast_hourly.slice(7).map((row) => row.forecast_soc_percent));
  night.forecast_hourly.forEach((row) => {
    row.forecast_grid_charge_kwh = row.hour < 3 ? null : row.hour === 3 ? 3.684 : 0;
  });
  await h.replace(night);
  assert.equal(chart.data.datasets[3].data[3], 3.684);
  assert.equal(chart.data.datasets[3].data[4], 0);
  assert.equal(chart.data.datasets[3].data[0], null);
  const beforeActual = Array.from(soc.data);
  night.forecast_hourly[0].actual_soc_percent = 0;
  night.forecast_hourly[4].actual_soc_percent = 40;
  night.forecast_hourly[7].actual_soc_percent = 38;
  await h.replace(night);
  assert.deepEqual(Array.from(soc.data), beforeActual, "actual imports must not rewrite the plan");
  assert.equal(chart.data.datasets[5].data[0], 0);
  assert.equal(chart.data.datasets[5].data[4], 40);
  assert.equal(chart.data.datasets[5].data[7], 38);
  assert.match(summaryText(h), /計画SOC最大 99% 23:00/);
  for (const bad of [true, "17", NaN, -1, 101]) {
    night.forecast_hourly[4].actual_soc_percent = bad;
    await h.replace(night);
    assert.equal(chart.data.datasets[5].data[4], null);
    assert.equal(soc.data[4], 39);
    night.forecast_hourly[3].forecast_soc_percent = bad;
    await h.replace(night);
    assert.equal(soc.data[3], null);
  }
  const observationsOnly = fixture();
  observationsOnly.latest_schedule = null;
  observationsOnly.battery_daily = [];
  observationsOnly.forecast_hourly.forEach((row) => {
    row.forecast_soc_percent = null;
    row.forecast_capacity_kwh = null;
    row.forecast_run_id = null;
    row.forecast_pv_kwh = null;
    row.forecast_load_kwh = null;
    row.forecast_charge_kwh = null;
    row.actual_soc_percent = row.hour < 7 ? [0, 5, null, 25, 50, null, 100][row.hour] : null;
  });
  h.run("store.latestSchedule=null");
  await h.replace(observationsOnly);
  assert.deepEqual(Array.from(chart.data.datasets[5].data.slice(0, 7)), [0, 5, null, 25, 50, null, 100]);
  assert.ok(soc.data.every((value) => value === null));
  await h.replace(fixture());
  assert.deepEqual(Array.from(chart.data.datasets[5].data.slice(0, 7)), Array(7).fill(40));

  const partial = fixture();
  partial.forecast_hourly[8].forecast_pv_kwh = null;
  await h.replace(partial);
  assert.equal(chart.data.datasets[0].data[8], null);
  assert.match(summaryText(h), /全日発電予測 未完 23\/24/);
  assert.equal(summaryText(h).includes("全日発電予測 0.20 kWh"), false);

  for (const bad of [true, "99", NaN, -1, 101]) {
    const p = fixture();
    p.forecast_hourly[23].forecast_soc_percent = bad;
    await h.replace(p);
    assert.equal(chart.data.datasets[4].data[23], null);
  }
  const zero = fixture();
  zero.forecast_hourly[7].forecast_soc_percent = 0;
  await h.replace(zero);
  assert.equal(chart.data.datasets[4].data[7], 0);

  const cross = fixture();
  cross.latest_schedule.plan_date = "2026-07-21";
  cross.latest_schedule.planned_night_charge_kwh = 9;
  await h.replace(cross);
  assert.ok(chart.data.datasets[3].data.every((value) => value === null));
  assert.equal(summaryText(h).includes("9.00 kWh"), false);
  assert.equal(chart.data.datasets[4].data[23], 99);
  assert.deepEqual(Array.from(chart.data.datasets[5].data.slice(0, 7)), Array(7).fill(40));
  cross.forecast_hourly.forEach((row) => { row.actual_soc_percent = null; });
  await h.replace(cross);
  assert.ok(chart.data.datasets[4].data.slice(0, 7).every((value) => value === null));

  const legacy = fixture();
  legacy.forecast_hourly.forEach((row) => { row.forecast_soc_percent = null; });
  await h.replace(legacy);
  assert.match(chart.data.datasets[4].label, /参考SOC/);
  assert.deepEqual(Array.from(chart.data.datasets[5].data.slice(0, 7)), Array(7).fill(40));
  assert.match(summaryText(h), /参考SOC最大/);
  legacy.forecast_hourly[0].actual_soc_percent = 100;
  await h.replace(legacy);
  assert.equal(chart.data.datasets[5].data[0], 100);
  assert.equal(summaryText(h).includes("100% 00:00"), false);
  legacy.latest_schedule = null;
  h.run("store.latestSchedule=null");
  await h.replace(legacy);
  assert.match(chart.data.datasets[4].label, /最新実測起点の参考SOC/);
  assert.ok(chart.data.datasets[4].data.slice(7).some((value) => value !== null));
  assert.ok(chart.data.datasets[3].data.every((value) => value === null));

  const unknown = fixture();
  unknown.forecast_hourly.forEach((row) => { row.forecast_soc_percent = null; row.forecast_capacity_kwh = null; });
  unknown.latest_schedule = null;
  h.run("store.latestSchedule=null");
  await h.replace(unknown);
  assert.deepEqual(Array.from(chart.data.datasets[5].data.slice(0, 7)), Array(7).fill(40));
  assert.ok(chart.data.datasets[4].data.slice(7).every((value) => value === null));

  const mixed = fixture();
  mixed.forecast_hourly[8].forecast_run_id = "another-run";
  await h.replace(mixed);
  assert.match(h.elements.get("hourlyForecastNote").textContent, /予測生成元が混在/);
  assert.deepEqual(Array.from(chart.data.datasets[5].data.slice(0, 7)), Array(7).fill(40));
  assert.ok(chart.data.datasets[4].data.slice(7).every((value) => value === null));

  const empty = fixture();
  empty.forecast_hourly.forEach((row) => { row.forecast_pv_kwh = null; row.forecast_load_kwh = null; row.forecast_charge_kwh = null; row.forecast_soc_percent = null; row.forecast_run_id = null; });
  await h.replace(empty);
  assert.match(summaryText(h), /全日発電予測 未完 0\/24/);
  assert.ok(chart.data.datasets[0].data.every((value) => value === null));
  assert.equal(chart.data.datasets[9].data[0], 0, "valid observed zero is preserved on the actual series");
  const escapedSummary = fixture();
  escapedSummary.forecast_hourly[23].latest_sample_at = '<img src=x onerror="alert(1)">';
  await h.replace(escapedSummary);
  assert.equal(h.elements.get("hourlyForecastGrid").innerHTML.includes("<img"), false);
  assert.match(h.elements.get("hourlyForecastGrid").innerHTML, /&lt;img/);
  const noRows = fixture();
  noRows.forecast_hourly = [];
  h.run("store.hourly.clear()");
  await h.replace(noRows);
  assert.equal(h.elements.get("hourlyForecastGrid").innerHTML, "", "missing hourly data clears stale summary cards");

  const review = fixture();
  review.daily_review = { date: "2026-07-20", complete_day: false, forecast_available_hour_count: 0,
    plan_forecast_binding: "mismatched", target_soc_percent: null, forecast_day_buy_kwh: null,
    actual_day_buy_kwh: 0, actual_morning_soc_percent: 40 };
  await h.replace(review);
  assert.match(h.elements.get("dailyReviewFindings").textContent, /原本不一致.*部分値 0\/24/);
  assert.match(h.elements.get("dailyReviewNote").textContent, /完全日は未確認/);
  assert.equal(h.elements.get("dailyReviewNote").textContent.includes("48件が揃った"), false);
  assert.match(h.elements.get("dailyReviewGrid").innerHTML, /実績 0.00 kWh/);
  const midnight = fixture();
  midnight.meta = { ...midnight.meta, snapshot_updated_dates: ["2026-07-21"] };
  midnight.forecast_hourly.push({ date: "2026-07-21", hour: 0, actual_soc_at_hour_start_percent: 55 });
  await h.replace(midnight);
  assert.equal(chart.data.datasets[5].data[0], 55, "after midnight show the explicitly imported date");
  assert.ok(chart.data.datasets[4].data.every((value) => value === null), "never reuse yesterday's forecast on the new day");
  assert.equal(h.requests.length, 0);
  const aligned = fixture();
  aligned.forecast_hourly.forEach((row) => {
    const next = String(row.hour + 1).padStart(2, "0");
    row.actual_soc_points = [
      { at: `${row.date}T${String(row.hour).padStart(2, "0")}:30:00+09:00`, percent: row.hour === 2 ? 0 : row.hour === 3 ? 20 : null },
      { at: `${row.date}T${next}:00:00+09:00`, percent: row.hour === 2 ? 0 : row.hour === 3 ? 42 : null },
    ];
  });
  aligned.forecast_hourly.forEach((row) => { row.actual_load_kwh = row.hour === 18 ? 1.183 : null; });
  const alignedView = await harness(aligned);
  const alignedChart = alignedView.chart("hourlyForecastChart");
  assert.equal(alignedChart.data.labels.length, 25);
  assert.equal(alignedChart.options.normalized, false);
  assert.equal(alignedChart.data.datasets[5].data.find((p) => p.x === 3).y, 0);
  assert.equal(alignedChart.data.datasets[5].data.find((p) => p.x === 3.5).y, 20);
  assert.equal(alignedChart.data.datasets[5].data.find((p) => p.x === 4).y, 42);
  assert.equal(alignedChart.data.datasets[5].parsing, false);
  for (const index of [0, 1, 2, 3, 7, 8, 9]) {
    assert.equal(alignedChart.data.datasets[index].stepped, "after");
    assert.equal(alignedChart.data.datasets[index].data.length, 48);
  }
  const measuredLoad = alignedChart.data.datasets[9].data;
  assert.deepEqual(JSON.parse(JSON.stringify(measuredLoad.slice(36, 40))), [
    { x: 18, y: 1.183 }, { x: 19, y: 1.183 }, { x: 19, y: null }, { x: 20, y: null },
  ], "the complete 18-19 interval ends at 19; the missing 19-20 interval stays missing");
  assert.match(alignedChart.options.plugins.tooltip.callbacks.label({
    dataset: alignedChart.data.datasets[9], dataIndex: 37, parsed: { y: 1.183 }, chart: alignedChart,
  }), /18:00〜19:00/, "the ending edge identifies its actual interval, rather than inventing a 19:00 value");
  assert.doesNotMatch(alignedView.elements.get("hourlyForecastNote").textContent, /正時・半時の合計|共通時刻契約|メーカー仕様|請求対象買電/);
  await alignedView.replace(fixture());
  assert.equal(alignedChart.data.datasets[5].parsing, true);
  assert.equal(alignedChart.data.datasets[9].parsing, true);
  for (let lastHour = 0; lastHour < 24; lastHour += 1) {
    for (const observed of [0, 1.183]) {
      const terminal = fixture();
      terminal.forecast_hourly.forEach((row) => {
        row.actual_soc_points = [];
        row.actual_load_kwh = row.hour === lastHour ? observed : null;
        row.actual_pv_kwh = row.hour === lastHour ? observed : null;
      });
      const terminalView = await harness(terminal);
      const terminalChart = terminalView.chart("hourlyForecastChart");
      for (const index of [8, 9]) {
        const series = terminalChart.data.datasets[index];
        assert.equal(series.spanGaps, false);
        for (let hour = 0; hour < 24; hour += 1) {
          const expected = hour === lastHour ? observed : null;
          assert.deepEqual(JSON.parse(JSON.stringify(series.data.slice(hour * 2, hour * 2 + 2))), [
            { x: hour, y: expected }, { x: hour + 1, y: expected },
          ], `interval ${hour}-${hour + 1}; final hour ${lastHour}; observed ${observed}`);
        }
      }
      assert.equal(terminalView.requests.length, 0);
    }
  }
  for (const explicit of [false, true]) {
    const importView = await harness();
    const posts = [];
    importView.context.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
    importView.context.fetch = async (url, options) => {
      posts.push({ url, body: JSON.parse(options.body) });
      return { ok: false }; // Stop after the one submission; never run a real job.
    };
    const input = importView.elements.get("importActualsDate");
    if (explicit) { input.value = "2026-07-19"; input.listeners.change(); }
    importView.run("todayIsoJst = () => '2026-07-21'"); // Page remained open across JST midnight.
    const button = importView.elements.get("importActualsBtn");
    button.listeners.click();
    await new Promise(setImmediate);
    button.listeners.click();
    assert.equal(posts.length, 1);
    assert.deepEqual(posts[0].body, explicit ? { target_date: "2026-07-19" } : {},
      "default date must be chosen by the server at submission; only a deliberate past selection is sent");
    assert.equal(input.value, explicit ? "2026-07-19" : "2026-07-21");
    assert.equal(input.max, "2026-07-21");
  }
  for (const responseStatus of [400, 401, 403, 502, 503]) {
    const importView = await harness();
    const stored = new Map();
    importView.context.sessionStorage = {
      getItem: (key) => stored.get(key) || null,
      setItem: (key, value) => stored.set(key, value),
      removeItem: (key) => stored.delete(key),
    };
    let posts = 0;
    importView.context.fetch = async () => { posts += 1; return { ok: false, status: responseStatus }; };
    const button = importView.elements.get("importActualsBtn");
    button.listeners.click();
    await new Promise(setImmediate);
    assert.equal(posts, 1, "rejection must never automatically resubmit");
    const rejectedBeforeSubmission = [400, 401, 403].includes(responseStatus);
    assert.equal(button.disabled, !rejectedBeforeSubmission);
    assert.equal(stored.get("dashboardActualImport"), rejectedBeforeSubmission ? undefined : "unknown");
    assert.match(importView.elements.get("importActualsStatus").textContent,
      rejectedBeforeSubmission ? /開始されていません/ : /重複実行/);
  }
  await reviewNavigationTests();
  console.log("dashboard hourly display: PASS");
}
async function reviewNavigationTests() {
  const payload = fixture();
  const oldDate = "2026-07-19";
  const rowsFor = (date, pv) => fixture().forecast_hourly.map((row) => ({
    ...row, date, forecast_run_id: `saved-${date}`, forecast_pv_kwh: row.hour === 9 ? pv : 0,
  }));
  payload.daily_review = { date: oldDate, complete_day: true };
  payload.daily_reviews = [{ date: oldDate, complete_day: true }, { date: "2026-07-18", complete_day: true }];
  payload.forecast_hourly.push(...rowsFor(oldDate, 0.75));
  const h = await harness(payload);
  const input = h.elements.get("dailyReviewDate");
  const note = h.elements.get("hourlyForecastNote");
  const title = h.elements.get("hourlyForecastTitle");
  const latest = h.elements.get("dailyReviewLatestBtn");
  const select = (date) => input.listeners.change({ target: { value: date } });
  const chart = h.chart("hourlyForecastChart");
  assert.equal(h.requests.length, 0, "date navigation must not add an initial request");
  assert.equal(input.value, oldDate);
  assert.equal(input.max, "2026-07-19");
  assert.match(note.textContent, /2026-07-20/);
  assert.match(title.textContent, /最新計画/);
  await select(oldDate);
  assert.equal(h.requests.length, 0, "a cached day requires no request");
  assert.equal(note.textContent, "2026-07-19 の時間別表示。");
  assert.ok(chart.data.datasets[0].data.some((point) => (typeof point === "number" ? point : point?.y) === 0.75));
  assert.match(title.textContent, /2026-07-19/);
  const frontier = h.run("store.meta.oldest_loaded_date");
  const deferred = new Map();
  let failOnce = true;
  h.context.fetch = async (url) => {
    h.requests.push(String(url));
    const date = new URL(String(url), "http://localhost").searchParams.get("end_date");
    if (date === "2026-07-15" && failOnce) { failOnce = false; throw new Error("read failed"); }
    if (["2026-07-17", "2026-07-16", "2026-07-12"].includes(date)) {
      return await new Promise((resolve) => deferred.set(date, resolve));
    }
    return { ok: true, json: async () => ({
      forecast_hourly: date === "2026-07-10" ? [] : [...rowsFor(date, 1.25), ...rowsFor("2026-07-20", 999)],
      latest_schedule: { plan_date: date }, meta: { oldest_loaded_date: date, global_newest_date: date },
      daily_reviews: [],
    }) };
  };
  await select("2026-07-18");
  assert.equal(h.requests.length, 1);
  assert.match(h.requests[0], /window_days=1&end_date=2026-07-18&include_static=0/);
  assert.equal(note.textContent, "2026-07-18 の時間別表示。");
  assert.equal(h.run("store.latestSchedule.plan_date"), "2026-07-20");
  assert.equal(h.run("store.meta.oldest_loaded_date"), frontier, "single-day loading must not move the history frontier");
  assert.equal(h.run("store.hourly.get('2026-07-20')[9].forecast_pv_kwh"), 0.2, "ignore unrelated response dates");
  await select(oldDate);
  await select("2026-07-18");
  assert.equal(h.requests.length, 1, "revisiting a loaded day does not transfer it again");

  const first = select("2026-07-17");
  const same = select("2026-07-17");
  const last = select("2026-07-16");
  assert.equal(h.requests.length, 3, "in-flight duplicate selections share one request");
  const responseFor = (date) => ({ ok: true, json: async () => ({
    forecast_hourly: rowsFor(date, 2), daily_review: { date, complete_day: true },
  }) });
  deferred.get("2026-07-16")(responseFor("2026-07-16"));
  await last;
  deferred.get("2026-07-17")(responseFor("2026-07-17"));
  await Promise.all([first, same]);
  assert.equal(input.value, "2026-07-16");
  assert.match(note.textContent, /2026-07-16/, "a late response cannot change the selected day");

  await select("2026-07-10");
  assert.match(note.textContent, /2026-07-10.*保存されていません/);
  assert.match(h.elements.get("dailyReviewNote").textContent, /予実レビューは保存されていません/);
  assert.equal(chart.data.labels.length, 0, "empty historical data must not fall back to the current plan");
  const requestsAfterEmpty = h.requests.length;
  await select("2026-07-10");
  await select("2026-07-20");
  await select("2026-06-30");
  await select("2026-02-31");
  assert.equal(h.requests.length, requestsAfterEmpty, "empty days are cached and invalid selections make no requests");
  assert.equal(input.value, "2026-07-10");

  await select("2026-07-15");
  assert.match(note.textContent, /2026-07-15.*読込に失敗/);
  await select("2026-07-15");
  assert.match(note.textContent, /2026-07-15 の時間別表示/);
  const pending = select("2026-07-12");
  latest.listeners.click();
  assert.equal(latest.disabled, true);
  assert.match(title.textContent, /最新計画/);
  deferred.get("2026-07-12")(responseFor("2026-07-12"));
  await pending;
  assert.match(note.textContent, /2026-07-20/, "returning to latest cancels the old display selection");
  assert.equal(input.value, oldDate);
  assert.equal(h.requests.some((url) => url.includes("/history")), false, "date selection must not fetch bulk history");
}

tests().catch((error) => { console.error(error); process.exitCode = 1; });
