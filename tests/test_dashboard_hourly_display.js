const assert = require("node:assert/strict");
const { harness, fixture } = require("./test_dashboard_model_display.js");

async function tests() {
  const h = await harness();
  const chart = h.chart("hourlyForecastChart");
  const soc = chart.data.datasets[4];
  assert.equal(chart.data.datasets.length, 6);
  assert.equal(chart.data.labels.length, 24);
  assert.deepEqual(Array.from(soc.data.slice(0, 7)), Array(7).fill(null));
  assert.deepEqual(Array.from(chart.data.datasets[5].data.slice(0, 7)), Array(7).fill(40));
  assert.equal(soc.data[23], 99);
  assert.match(soc.label, /計画SOC/);
  assert.equal(soc.spanGaps, false);
  assert.match(h.elements.get("hourlyForecastNote").textContent, /計画SOC最大 23:00 99%/);
  assert.match(h.elements.get("hourlyForecastNote").textContent, /夜間SOC計画未確定/);

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
  assert.match(h.elements.get("hourlyForecastNote").textContent, /計画SOC最大 23:00 99%/);
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
  assert.match(h.elements.get("hourlyForecastNote").textContent, /全日発電予測 未完 23\/24/);
  assert.equal(h.elements.get("hourlyForecastNote").textContent.includes("全日発電予測 0.20kWh"), false);

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
  assert.equal(h.elements.get("hourlyForecastNote").textContent.includes("9.00kWh"), false);
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
  assert.match(h.elements.get("hourlyForecastNote").textContent, /参考SOC最大/);
  legacy.forecast_hourly[0].actual_soc_percent = 100;
  await h.replace(legacy);
  assert.equal(chart.data.datasets[5].data[0], 100);
  assert.equal(h.elements.get("hourlyForecastNote").textContent.includes("00:00 100%"), false);
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
  assert.deepEqual(Array.from(chart.data.datasets[5].data.slice(0, 7)), Array(7).fill(40));
  assert.ok(chart.data.datasets[4].data.slice(7).every((value) => value === null));

  const empty = fixture();
  empty.forecast_hourly.forEach((row) => { row.forecast_pv_kwh = null; row.forecast_load_kwh = null; row.forecast_charge_kwh = null; row.forecast_soc_percent = null; row.forecast_run_id = null; });
  await h.replace(empty);
  assert.match(h.elements.get("hourlyForecastNote").textContent, /全日発電予測 未完 0\/24/);
  assert.ok(chart.data.datasets[0].data.every((value) => value === null));
  assert.equal(chart.data.datasets[2].data[0], 0, "valid observed zero is preserved");

  const review = fixture();
  review.daily_review = { date: "2026-07-20", complete_day: false, forecast_available_hour_count: 0,
    plan_forecast_binding: "mismatched", target_soc_percent: null, forecast_day_buy_kwh: null,
    actual_day_buy_kwh: 0, actual_morning_soc_percent: 40 };
  await h.replace(review);
  assert.match(h.elements.get("dailyReviewFindings").textContent, /原本不一致.*部分値 0\/24/);
  assert.match(h.elements.get("dailyReviewNote").textContent, /完全日は未確認/);
  assert.equal(h.elements.get("dailyReviewNote").textContent.includes("48件が揃った"), false);
  assert.match(h.elements.get("dailyReviewGrid").innerHTML, /実績 0.00 kWh/);
  assert.equal(h.requests.length, 0);
  console.log("dashboard hourly display: PASS");
}
tests().catch((error) => { console.error(error); process.exitCode = 1; });
