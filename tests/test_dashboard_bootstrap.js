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
  }
  update() {}
  resize() {}
  destroy() {}
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
  fetch: async () => {
    fetchCount += 1;
    return {
      ok: true,
      status: 200,
      json: async () => ({
        pv_daily: [],
        forecast_hourly: [{ date: "2026-07-20", hour: 7, forecast_pv_kwh: 0.4937, forecast_load_kwh: 1.4005, forecast_charge_kwh: 0 }],
        energy_daily: [{ date: "2026-07-17", actual_load_kwh: 1 }], cost_daily: [], cost_monthly: [],
        battery_daily: [], battery_flow_daily: [], model_parameters: [],
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
  const countBeforeNavigation = fetchCount;
  elements.get("dailyReviewPrevBtn").listeners.click();
  assert.equal(elements.get("dailyReviewDate").textContent, "2026-07-16");
  assert.equal(fetchCount, countBeforeNavigation);
});
