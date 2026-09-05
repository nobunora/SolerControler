const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const elements = new Map();
let fetchCount = 0;
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
  fetch: async () => {
    fetchCount += 1;
    return {
      ok: true,
      status: 200,
      json: async () => ({
        pv_daily: [],
        forecast_hourly: [
          { date: "2026-07-20", hour: 0, forecast_pv_kwh: 0, forecast_load_kwh: 0.2, forecast_charge_kwh: 0, actual_soc_percent: 42 },
          { date: "2026-07-20", hour: 6, forecast_pv_kwh: 0, forecast_load_kwh: 0.2, forecast_charge_kwh: 0, actual_soc_percent: 48 },
          { date: "2026-07-20", hour: 7, forecast_pv_kwh: 0.4937, forecast_load_kwh: 1.4005, forecast_charge_kwh: 0 },
        ],
        energy_daily: [
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
        meta: {},
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

setImmediate(() => {
  assert.ok(context.DashboardCalculations);
  assert.ok(context.DashboardDates);
  assert.ok(context.DashboardApi);
  assert.ok(context.DashboardStore);
  assert.ok(elements.has("statusMsg"));
  assert.ok(elements.has("dailyReviewPrevBtn"));
  assert.ok(elements.has("dailyReviewNextBtn"));
  assert.equal(typeof elements.get("dailyReviewPrevBtn").listeners.click, "function");
  assert.equal(typeof elements.get("dailyReviewNextBtn").listeners.click, "function");
  assert.match(elements.get("hourlyForecastNote").textContent, /夜間系統充電 3\.14kWh/);
  assert.match(elements.get("hourlyForecastNote").textContent, /予想SOCピーク 07:00ごろ 77%/);
  assert.match(elements.get("hourlyForecastNote").textContent, /計画更新/);
  const hourlyChart = ChartStub.instances.find((chart) => chart.data.datasets.some((dataset) => dataset.label === "予想SOC(%)"));
  assert.deepEqual(Array.from(hourlyChart.data.datasets[4].data), [42, 48, 77]);
  const pvChart = ChartStub.instances[1];
  assert.deepEqual({ min: pvChart.options.scales.y.min, max: pvChart.options.scales.y.max }, { min: 1, max: 9 });
  const loadChart = ChartStub.instances[2];
  assert.deepEqual({ min: loadChart.options.scales.y.min, max: loadChart.options.scales.y.max }, { min: -2, max: 6 });
  const dailyKwhChart = ChartStub.instances[3];
  assert.deepEqual({ min: dailyKwhChart.options.scales.y.min, max: dailyKwhChart.options.scales.y.max }, { min: 0, max: 4 });
  assert.deepEqual({ min: dailyKwhChart.options.scales.y2.min, max: dailyKwhChart.options.scales.y2.max }, { min: 2, max: 6 });
  const dailyYenChart = ChartStub.instances[4];
  assert.deepEqual({ min: dailyYenChart.options.scales.y.min, max: dailyYenChart.options.scales.y.max }, { min: 0, max: 300 });
  assert.deepEqual({ min: dailyYenChart.options.scales.y2.min, max: dailyYenChart.options.scales.y2.max }, { min: 100, max: 400 });
  const monthlyChart = ChartStub.instances[5];
  assert.deepEqual({ min: monthlyChart.options.scales.y.min, max: monthlyChart.options.scales.y.max }, { min: 54, max: 66 });
  assert.deepEqual({ min: monthlyChart.options.scales.y2.min, max: monthlyChart.options.scales.y2.max }, { min: 1620, max: 1980 });
  const batteryChart = ChartStub.instances.find((chart) => chart.data.datasets.some((dataset) => dataset.label.includes("夜間充電計画")));
  assert.deepEqual({ min: batteryChart.options.scales.y.min, max: batteryChart.options.scales.y.max }, { min: 2, max: 4 });
  assert.deepEqual({ min: batteryChart.options.scales.y2.min, max: batteryChart.options.scales.y2.max }, { min: 55, max: 80 });
  const countBeforeNavigation = fetchCount;
  elements.get("dailyReviewPrevBtn").listeners.click();
  assert.equal(elements.get("dailyReviewDate").textContent, "2026-07-16");
  assert.equal(fetchCount, countBeforeNavigation);
});
