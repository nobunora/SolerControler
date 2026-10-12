const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function fixture() {
  const date = "2026-07-20";
  const rows = Array.from({ length: 24 }, (_, hour) => ({
    date, hour, forecast_run_id: "run-1", forecast_pv_kwh: hour === 9 ? 0.2 : 0,
    forecast_load_kwh: 0.1, forecast_charge_kwh: 0, forecast_capacity_kwh: 10,
    forecast_soc_percent: hour >= 7 ? (hour === 23 ? 99 : 50) : null,
    actual_soc_percent: hour < 7 ? 40 : null, actual_load_kwh: hour < 7 ? 0 : null,
    actual_pv_kwh: hour < 7 ? 0 : null,
  }));
  return {
    forecast_hourly: rows,
    energy_daily: [{ date, forecast_pv_kwh: 0.2, actual_pv_kwh: 0, forecast_load_kwh: 2.4, actual_load_kwh: 0, forecast_available_hour_count: 24 }],
    cost_daily: [{ date, self_consumption_kwh: 0, savings_yen: 0 }],
    cost_monthly: [{ month: "2026-07", period_start: "2026-07-15", period_end: "2026-08-14", self_consumption_kwh: 0, savings_yen: 0 }],
    battery_daily: [{ date, setting_soc_target_percent: 50, night_charge_kwh: 2, pv_charge_end_soc_percent: 99 }],
    battery_flow_daily: [{ date, charge_kwh: 0, discharge_kwh: 0 }],
    pv_daily: [{ date, actual_temp_c: 25 }],
    latest_schedule: { plan_date: date, planned_target_soc_percent: 50, planned_night_charge_kwh: 2, charge_start_time: "04:00", charge_end_time: "07:00", plan_updated_at: "2026-07-19T18:00:00Z" },
    model_parameters: [
      { name: "physical_pv_global_bias_scale", mean_value: 999, sample_count: 7 },
      { name: "pv_forecast_error_ratio_sample_count", mean_value: 0, sample_count: 7 },
      { name: "pv_forecast_error_ratio_std", mean_value: 0.3, sample_count: 7 },
    ],
    pv_forecast_diagnostics: {
      physical: { scales: { radiation_scale: 999, global_bias_scale: 999, daypart: { morning: { scale: 999, count: 7 } } }, data_quality: { global_days: 7 } },
      pv_model: {
        schema_version: 1, status: "valid", reason_code: null,
        model_id: "physical_clear_ghi_annual1", basis_version: "noaa_geometric_1min_midpoint_annual1_v1",
        plan_date: date, generated_at: "2026-07-19T18:00:00Z", forecast_run_id: "run-1",
        raw_plan_sha256: "a".repeat(64), control_version_id: "control-1",
        calibration_sha256: "b".repeat(64), forecast_sha256: "c".repeat(64),
        training_day_count: 17, training_sample_count: 221,
        training_start_date: "2026-04-18", training_end_date: "2026-07-18",
        training_as_of: "2026-07-19T17:00:00Z", created_at: "2026-07-19T17:10:00Z", usable_from: "2026-07-19T17:10:00Z",
        terms: ["1", "x", "cos_phi", "sin_phi"], theta4: [2.8, -0.000001, 0, 0.1], rank: 4, condition_number: 6.7,
        retrieved_at: "2026-07-19T17:50:00Z", as_of: "2026-07-19T17:55:00Z", provider_issued_at: null,
        repaired_hours: [], totals: { full_day_pv_kwh: 0.2, decision_day_pv_kwh: 0.2, morning_pv_kwh: 0.2, midday_pv_kwh: 0, evening_pv_kwh: 0 },
      },
    },
    daily_review: { date, complete_day: true, forecast_available_hour_count: 24, plan_forecast_binding: "matched", actual_day_buy_kwh: 0 },
    meta: { oldest_loaded_date: "2026-07-01", global_oldest_date: "2026-07-01", global_newest_date: date, has_more_before: false },
  };
}

async function harness(payload = fixture()) {
  const elements = new Map();
  function element(id) {
    if (elements.has(id)) return elements.get(id);
    let html = "";
    const value = { id, children: [], textContent: "", style: {}, disabled: false,
      classList: { add() {}, remove() {}, toggle() {} }, listeners: {},
      addEventListener(event, callback) { this.listeners[event] = callback; },
      appendChild(child) { this.children.push(child); } };
    Object.defineProperty(value, "innerHTML", { get: () => html, set: (next) => { html = next; value.children = []; } });
    elements.set(id, value);
    return value;
  }
  const template = fs.readFileSync(path.join(__dirname, "../templates/dashboard.html"), "utf8");
  const ids = new Set(Array.from(template.matchAll(/\bid="([^"]+)"/g), (match) => match[1]));
  for (const id of ids) element(id);
  let created = 0;
  class Chart {
    constructor(target, config) { this.target = target; this.data = config.data; this.options = config.options; Chart.instances.push(this); }
    update() {}
    resize() {}
  }
  Chart.instances = [];
  class Clock extends Date {
    constructor(...args) { super(...(args.length ? args : ["2026-07-20T12:00:00Z"])); }
  }
  const requests = [];
  const context = {
    console, URLSearchParams, Intl, Date: Clock, Chart, setTimeout: () => 0,
    fetch: async (url) => { requests.push(String(url)); throw new Error("unexpected HTTP"); },
    document: {
      getElementById: (id) => ids.has(id) ? element(id) : null,
      querySelector: (selector) => selector === "#learningParamsTable tbody" ? element(selector) : null,
      createElement: (tag) => element(`new-${tag}-${++created}`), querySelectorAll: () => [],
    },
  };
  context.window = context;
  context.globalThis = context;
  context.__DASHBOARD_DATA__ = payload;
  context.addEventListener = () => {};
  vm.createContext(context);
  for (const filename of ["dashboard_calculations.js", "dashboard_dates.js", "dashboard_api.js", "dashboard_store.js", "dashboard.js"]) {
    vm.runInContext(fs.readFileSync(path.join(__dirname, "../static", filename), "utf8"), context, { filename });
  }
  await new Promise(setImmediate);
  const run = (code) => vm.runInContext(code, context);
  return {
    context, run, elements, requests, template,
    chart: (id) => Chart.instances.find((chart) => chart.target.id === id),
    table: () => element("#learningParamsTable tbody").children.map((row) => row.children.map((cell) => cell.textContent)),
    async replace(next) { context.__replacement = next; run("absorbSlice(__replacement, true)"); await run("refreshDashboard()"); },
  };
}

async function tests() {
  const h = await harness();
  assert.match(h.elements.get("pvModelName").textContent, /NEW100/);
  assert.match(h.elements.get("pvModelLearningPeriod").textContent, /17日.*221時間区間/);
  assert.match(h.elements.get("pvModelTiming").textContent, /提供者発行: 不明/);
  assert.match(h.elements.get("pvModelTiming").textContent, /末尾offset/);
  assert.match(h.elements.get("pvModelExplanation").textContent, /PV\(h\)=max\(0,C\(d,h\)\)\*min\(F\(h\),Q\(d,h\)\)/);
  assert.match(h.elements.get("pvModelExplanation").textContent, /365または366/);
  assert.ok(h.table().some((row) => row[1] === "theta1 (x)" && row[2] === "-0.000001"));
  assert.ok(h.table().some((row) => row[1] === "theta2 (cos_phi)" && row[2] === "0"));
  assert.ok(h.table().some((row) => row[0] === "旧方式履歴" && row[2] === "999" && row[3] === "7 計画保存回"));
  assert.ok(h.table().some((row) => row[1] === "PV予測誤差サンプル数" && row[2] === "0" && row[4].includes("完全精度を意味しない")));
  assert.equal(h.table().some((row) => row[0] === "旧物理PV"), false);
  assert.equal(h.requests.length, 0);
  assert.equal((h.template.match(/<canvas\b/g) || []).length, 8);
  assert.equal((h.template.match(/<table\b/g) || []).length, 1);
  assert.equal((h.template.match(/\bid="/g) || []).length, 43);

  const changed = fixture();
  changed.forecast_hourly[8].forecast_run_id = "run-2";
  await h.replace(changed);
  assert.match(h.elements.get("pvModelName").textContent, /未確認/);
  await h.replace(fixture());
  assert.match(h.elements.get("pvModelName").textContent, /NEW100/);

  const repair = fixture();
  repair.pv_forecast_diagnostics.pv_model.repaired_hours = [8, 9];
  repair.pv_forecast_diagnostics.pv_model.generated_at = "<img src=x onerror=alert(1)>";
  await h.replace(repair);
  assert.match(h.elements.get("pvModelRepair").textContent, /08:00.*09:00/);
  assert.match(h.elements.get("pvModelProvenance").textContent, /<img/);
  assert.equal(h.elements.get("pvModelProvenance").innerHTML, "");

  for (const status of ["legacy", "invalid", "unsupported"]) {
    const legacy = fixture();
    legacy.pv_forecast_diagnostics.pv_model = { status, plan_date: "2026-07-20", generated_at: null, reason_code: "trace_missing" };
    await h.replace(legacy);
    if (status === "legacy") {
      assert.match(h.elements.get("pvModelExplanation").textContent, /GTI.*PR/);
      assert.ok(h.table().some((row) => row[0] === "旧物理PV"));
    } else {
      assert.match(h.elements.get("pvModelExplanation").textContent, /断定しません/);
    }
    assert.equal(h.table().some((row) => row[0] === "NEW100係数"), false);
  }
  const missing = fixture();
  missing.forecast_hourly[7].forecast_run_id = null;
  await h.replace(missing);
  assert.match(h.elements.get("pvModelName").textContent, /未確認/);
  const extra = fixture().forecast_hourly.concat([{ date: "2026-07-20", hour: 5, actual_load_kwh: 0, forecast_run_id: null,
    forecast_pv_kwh: null, forecast_load_kwh: null, forecast_soc_percent: null }]);
  h.context.__rows = extra;
  h.context.__summary = fixture().pv_forecast_diagnostics.pv_model;
  assert.equal(h.run("bindPvModelToForecast(__summary,__rows,'2026-07-20').status"), "valid");
  console.log("dashboard model display: PASS");
}

module.exports = { harness, fixture };
if (require.main === module) tests().catch((error) => { console.error(error); process.exitCode = 1; });
