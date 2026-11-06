const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const elements = new Map();
let fetchCount = 0;
const fetchRequests = [];
function element(id = "") {
  if (elements.has(id)) return elements.get(id);
  const value = {
    id,
    style: {},
    dataset: {},
    listeners: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener(type, handler) { this.listeners[type] = handler; },
    appendChild() {},
    replaceChildren() {},
    querySelectorAll() { return []; },
    getContext() { return {}; },
    textContent: "",
    innerHTML: "",
    value: "",
    disabled: false,
  };
  elements.set(id, value);
  return value;
}

class ChartStub {
  constructor(_target, config) {
    this.data = config.data || { labels: [], datasets: [] };
    this.options = config.options || {};
    ChartStub.instances.push(this);
  }
  update() {}
  resize() {}
  destroy() {}
}
ChartStub.instances = [];

function assertCompactAxis(chart, axisName, indexes, zeroBaseline = false, includeZero = false) {
  const values = indexes.flatMap((index) => chart.data.datasets[index].data)
    .filter((value) => typeof value === "number" && Number.isFinite(value));
  if (zeroBaseline || includeZero) values.push(0);
  assert.ok(values.length > 0);
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const padding = (maximum === minimum ? Math.abs(maximum) || 1 : maximum - minimum) * 0.1;
  const axis = chart.options.scales[axisName];
  const step = axis.ticks.stepSize;
  const tolerance = step * 1e-8;
  assert.ok(Number.isFinite(step) && step > 0);
  if (zeroBaseline && minimum >= 0) assert.equal(axis.min, 0);
  else {
    assert.ok(axis.min <= minimum - padding + tolerance, "lower 10% margin");
    assert.ok(axis.min > minimum - padding - step - tolerance, "lower rounding stays within one tick");
  }
  assert.ok(axis.max >= maximum + padding - tolerance, "upper 10% margin");
  assert.ok(axis.max < maximum + padding + step + tolerance, "upper rounding stays within one tick");
  for (const bound of [axis.min, axis.max]) {
    assert.ok(Math.abs(bound / step - Math.round(bound / step)) < 1e-8, "bounds use neat tick multiples");
  }
  assert.equal(axis.ticks.count, undefined, "do not respace neat ticks by forcing a count");
  assert.equal(axis.ticks.maxTicksLimit, undefined, "leave density reduction to automatic tick skipping");
}

const context = {
  console,
  URLSearchParams,
  Intl,
  Date,
  Map,
  Set,
  Math,
  Number,
  Object,
  Array,
  String,
  Promise,
  Chart: ChartStub,
  setTimeout: () => 0,
  clearTimeout() {},
  fetch: async (url) => {
    fetchCount += 1;
    fetchRequests.push(String(url));
    const isHistory = String(url).startsWith("/api/dashboard/history");
    return {
      ok: true,
      status: 200,
      json: async () => ({
        pv_daily: [],
        forecast_hourly: [
          { date: "2026-07-20", hour: 0, forecast_pv_kwh: 0, forecast_load_kwh: 0.2, forecast_charge_kwh: 0, actual_soc_percent: 42 },
          { date: "2026-07-20", hour: 6, forecast_pv_kwh: 0, forecast_load_kwh: 0.2, forecast_charge_kwh: 0, actual_soc_percent: 48 },
          { date: "2026-07-20", hour: 7, forecast_pv_kwh: 0.4937, forecast_load_kwh: 1.4005, forecast_charge_kwh: 0, forecast_soc_percent: 77 },
          { date: "2026-07-20", hour: 8, forecast_pv_kwh: 1, forecast_load_kwh: 0, forecast_charge_kwh: 1, forecast_soc_percent: 63.2 },
          { date: "2026-07-20", hour: 9, forecast_pv_kwh: 0, forecast_load_kwh: 0, forecast_charge_kwh: 0, forecast_soc_percent: 70.4 },
        ].map((row) => ({ ...row, forecast_capacity_kwh: 5 })),
        energy_daily: isHistory
          ? [
              { date: "2026-07-17", forecast_pv_kwh: 999, actual_pv_kwh: 999, forecast_load_kwh: 999, actual_load_kwh: 999 },
              { date: "2026-07-18", forecast_pv_kwh: 999, actual_pv_kwh: 999, forecast_load_kwh: 999, actual_load_kwh: 999 },
            ]
          : [
              { date: "2026-07-17", forecast_pv_kwh: 5, actual_pv_kwh: 6, forecast_load_kwh: 3, actual_load_kwh: 1 },
              { date: "2026-07-18", forecast_pv_kwh: 7, actual_pv_kwh: 9, forecast_load_kwh: 4, actual_load_kwh: 6 },
            ],
        cost_daily: [
          { date: "2026-07-17", self_consumption_kwh: 2, savings_yen: 100 },
          { date: "2026-07-18", self_consumption_kwh: 4, savings_yen: 300 },
        ],
        cost_monthly: [
          { month: "2026-06", self_consumption_kwh: 40, savings_yen: 1000 },
          { month: "2026-07", self_consumption_kwh: 60, savings_yen: 1800 },
        ],
        battery_daily: [
          { date: "2026-07-17", setting_soc_target_percent: 55, night_charge_kwh: 2, pv_charge_end_soc_percent: 60 },
          { date: "2026-07-18", setting_soc_target_percent: 75, night_charge_kwh: 4, pv_charge_end_soc_percent: 80 },
        ], battery_flow_daily: [], model_parameters: [],
        latest_schedule: {
          plan_date: "2026-07-20",
          soc_charge_mode: "0",
          planned_target_soc_percent: 77,
          planned_night_charge_kwh: 3.1403,
          plan_updated_at: "2026-07-19T23:31:16Z",
        },
        dashboard_warnings: [], pv_forecast_diagnostics: {},
        daily_review: { date: "2026-07-17", complete_day: true },
        daily_reviews: [
          { date: "2026-07-16", complete_day: true },
          { date: "2026-07-17", complete_day: true },
        ],
        meta: isHistory
          ? {
              window_days: 201,
              aggregation_close_day: 31,
              oldest_loaded_date: "2026-01-01",
              newest_loaded_date: "2026-07-14",
              global_oldest_date: "2026-01-01",
              global_newest_date: "2026-07-14",
              has_more_before: false,
            }
          : {
              window_days: 31,
              aggregation_close_day: 14,
              oldest_loaded_date: "2026-06-15",
              newest_loaded_date: "2026-07-20",
              global_oldest_date: "2026-01-01",
              global_newest_date: "2026-07-20",
              has_more_before: true,
            },
      }),
    };
  },
  document: {
    getElementById: (id) => element(id),
    createElement: (tag) => element(`created-${tag}-${elements.size}`),
    querySelector: (selector) => element(selector),
    querySelectorAll: () => [],
  },
};
context.window = context;
context.globalThis = context;
context.window.__DASHBOARD_DATA__ = {};
context.window.addEventListener = () => {};

vm.createContext(context);
for (const filename of [
  "dashboard_calculations.js",
  "dashboard_dates.js",
  "dashboard_api.js",
  "dashboard_store.js",
  "dashboard.js",
]) {
  const source = fs.readFileSync(path.join(__dirname, "..", "static", filename), "utf8");
  vm.runInContext(source, context, { filename });
}

setImmediate(async () => {
  assert.ok(context.DashboardCalculations);
  assert.ok(context.DashboardDates);
  assert.ok(context.DashboardApi);
  assert.ok(context.DashboardStore);
  assert.ok(elements.has("statusMsg"));
  assert.ok(elements.has("dailyReviewPrevBtn"));
  assert.ok(elements.has("dailyReviewNextBtn"));
  assert.equal(typeof elements.get("dailyReviewPrevBtn").listeners.click, "function");
  assert.equal(typeof elements.get("dailyReviewNextBtn").listeners.click, "function");
  assert.deepEqual(fetchRequests, ["/api/dashboard/bootstrap"]);
  assert.equal(fetchRequests.some((url) => url.startsWith("/api/dashboard/history")), false);
  assert.equal(fetchRequests.some((url) => url.startsWith("/api/dashboard?")), false);
  const hourlySummary = elements.get("hourlyForecastGrid").innerHTML.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ");
  assert.match(hourlySummary, /夜間系統充電予測 3\.14 kWh 時間別計画未確定/);
  assert.match(hourlySummary, /取得済み計画SOC最大 3\/17 77% 07:00/);
  assert.match(hourlySummary, /計画更新/);
  assert.equal(elements.get("hourlyForecastNote").textContent, "2026-07-20 の時間別表示。");
  const hourlyChart = ChartStub.instances.find((chart) => chart.data.datasets.some((dataset) => dataset.label === "計画SOC(%)"));
  assert.deepEqual(Array.from(hourlyChart.data.datasets[4].data), [null, null, 77, 63.2, 70.4]);
  assert.deepEqual(Array.from(hourlyChart.data.datasets[5].data), [42, 48, null, null, null]);
  assert.deepEqual({ min: hourlyChart.options.scales.y.min, max: hourlyChart.options.scales.y.max }, { min: 0, max: 5 });
  assert.deepEqual({ min: hourlyChart.options.scales.y2.min, max: hourlyChart.options.scales.y2.max }, { min: 0, max: 100 });
  assert.equal(hourlyChart.options.scales.y.ticks.count, hourlyChart.options.scales.y2.ticks.count);
  const pvChart = ChartStub.instances[1];
  const initialPeriodLabel = elements.get("periodLabel").textContent;
  const initialDailyLabels = Array.from(pvChart.data.labels);
  // A separately generated history snapshot may end before the current billing month.
  await elements.get("periodAllBtn").listeners.click();
  await elements.get("periodMonthBtn").listeners.click();
  assert.equal(elements.get("periodLabel").textContent, initialPeriodLabel);
  assert.deepEqual(Array.from(pvChart.data.labels), initialDailyLabels);
  assert.equal(fetchRequests.filter((url) => url.startsWith("/api/dashboard/history")).length, 1);
  assert.equal(fetchRequests.some((url) => url.startsWith("/api/dashboard?")), false);
  const loadChart = ChartStub.instances[2];
  const dailyKwhChart = ChartStub.instances[3];
  const dailyYenChart = ChartStub.instances[4];
  const monthlyChart = ChartStub.instances[5];
  const batteryChart = ChartStub.instances.find((chart) => chart.data.datasets.some((dataset) => dataset.label.includes("夜間充電計画")));
  const checkDisplayAxes = () => {
    assertCompactAxis(pvChart, "y", [0, 1, 2], false, true);
    assertCompactAxis(loadChart, "y", [0, 1, 2], false, true);
    assertCompactAxis(dailyKwhChart, "y", [0], true);
    assertCompactAxis(dailyKwhChart, "y2", [1]);
    assertCompactAxis(dailyYenChart, "y", [0], true);
    assertCompactAxis(dailyYenChart, "y2", [1]);
    assertCompactAxis(monthlyChart, "y", [0]);
    assertCompactAxis(monthlyChart, "y2", [1]);
    assertCompactAxis(batteryChart, "y", [0]);
    assert.deepEqual({ min: batteryChart.options.scales.y2.min, max: batteryChart.options.scales.y2.max }, { min: 0, max: 100 });
    for (const dataset of batteryChart.data.datasets) {
      assert.equal(dataset.borderColor, batteryChart.options.scales[dataset.yAxisID].ticks.color);
    }
    assert.equal(batteryChart.data.datasets.length, 2);
    assert.ok(batteryChart.data.datasets.every((dataset) => !dataset.label.includes("設定SOC")));
    assert.equal(batteryChart.data.datasets[0].yAxisID, "y");
    assert.equal(batteryChart.data.datasets[1].pointStyle, "triangle");
    assert.equal(batteryChart.options.plugins.legend.labels.usePointStyle, true);
  };
  checkDisplayAxes();
  assert.ok(loadChart.options.scales.y.max < 100, "small loads no longer retain the fixed 100 kWh ceiling");
  const initialPvRange = pvChart.options.scales.y.max - pvChart.options.scales.y.min;

  await elements.get("periodYearBtn").listeners.click();
  assert.equal(fetchRequests.filter((url) => url.startsWith("/api/dashboard/history")).length, 1);
  assert.equal(fetchRequests.some((url) => url.startsWith("/api/dashboard?")), false);
  assert.ok(
    pvChart.data.datasets[0].data.every((value) => value == null || Number(value) < 100),
    "stale overlapping history must not overwrite fresh bootstrap rows",
  );
  checkDisplayAxes();
  const weeklyPvRange = pvChart.options.scales.y.max - pvChart.options.scales.y.min;
  assert.ok(weeklyPvRange > initialPvRange, "weekly sums expand the range");

  await elements.get("periodAllBtn").listeners.click();
  assert.equal(fetchRequests.filter((url) => url.startsWith("/api/dashboard/history")).length, 1);
  assert.equal(fetchRequests.some((url) => url.startsWith("/api/dashboard?")), false);
  checkDisplayAxes();
  assert.equal(pvChart.options.scales.y.max - pvChart.options.scales.y.min, initialPvRange, "returning to daily view shrinks the range again");

  const countBeforeNavigation = fetchCount;
  elements.get("dailyReviewPrevBtn").listeners.click();
  assert.equal(elements.get("dailyReviewDate").textContent, "2026-07-16");
  assert.equal(fetchCount, countBeforeNavigation);
  await require("./test_dashboard_actual_import.js")();
});
