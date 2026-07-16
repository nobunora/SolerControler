const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const elements = new Map();
function element(id = "") {
  if (elements.has(id)) return elements.get(id);
  const value = {
    id,
    style: {},
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {},
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
  fetch: async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      pv_daily: [], forecast_hourly: [], energy_daily: [], cost_daily: [], cost_monthly: [],
      battery_daily: [], battery_flow_daily: [], model_parameters: [], latest_schedule: {},
      dashboard_warnings: [], pv_forecast_diagnostics: {}, daily_review: {}, meta: {},
    }),
  }),
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
});
