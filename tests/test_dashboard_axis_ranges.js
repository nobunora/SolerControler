const assert = require("node:assert/strict");
const { harness, fixture } = require("./test_dashboard_model_display.js");

async function tests() {
  const payload = fixture();
  payload.forecast_hourly[9].forecast_pv_kwh = 6.8;
  payload.latest_schedule.planned_night_charge_kwh = 30;
  payload.energy_daily[0] = { ...payload.energy_daily[0], forecast_pv_kwh: 500, actual_pv_kwh: 600, forecast_load_kwh: 300, actual_load_kwh: 330 };
  payload.cost_daily[0] = { date: "2026-07-20", self_consumption_kwh: 60, savings_yen: -2300 };
  payload.cost_monthly[0] = { ...payload.cost_monthly[0], self_consumption_kwh: 1200, savings_yen: 90000 };
  payload.battery_daily[0].night_charge_kwh = 30;
  const h = await harness(payload);
  for (const id of ["hourlyForecastChart", "pvChart", "loadChart", "dailyKwhChart", "dailyYenChart", "monthlyCostChart", "batteryChart", "batteryLifeChart"]) {
    const chart = h.chart(id);
    for (const dataset of chart.data.datasets) {
      const axis = chart.options.scales[dataset.yAxisID || "y"];
      assert.ok(Number.isFinite(axis.min) && Number.isFinite(axis.max), `${id} finite bounds`);
      for (const value of dataset.data) {
        if (typeof value !== "number" || !Number.isFinite(value)) continue;
        assert.ok(value >= axis.min && value <= axis.max, `${id}/${dataset.label}: ${value} within ${axis.min}..${axis.max}`);
      }
      assert.equal(dataset.spanGaps, false, `${id} must not draw through missing observations`);
      assert.equal(dataset.tension, 0, `${id} must not invent smooth negative PV or peak overshoot`);
    }
  }
  assert.ok(h.chart("hourlyForecastChart").options.scales.y.max >= 10);
  assert.equal(h.chart("hourlyForecastChart").options.scales.y2.max, 100);
  assert.equal(h.chart("batteryChart").options.scales.y2.min, 0);
  assert.equal(h.chart("batteryChart").options.scales.y2.max, 100);
  const stacked = h.chart("batteryLifeChart");
  stacked.data.datasets.forEach((dataset, index) => { dataset.data = [index % 2 ? -7 : 30]; });
  h.context.__chart = stacked;
  h.run("ensureVisibleChartRanges(__chart)");
  assert.ok(stacked.options.scales.y.max >= 60, "positive stack sum must fit");
  assert.ok(stacked.options.scales.y.min <= -14, "negative stack sum must fit");
  const snapshot = stacked.data.datasets.map((dataset) => dataset.data.slice());
  h.run("ensureVisibleChartRanges(__chart)");
  assert.deepEqual(stacked.data.datasets.map((dataset) => dataset.data), snapshot, "axis changes do not clip values");
  assert.equal(h.requests.length, 0);
  console.log("dashboard axis ranges: PASS");
}
tests().catch((error) => { console.error(error); process.exitCode = 1; });
