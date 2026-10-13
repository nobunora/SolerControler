const assert = require("node:assert/strict");
const { harness, fixture } = require("./test_dashboard_model_display.js");

async function tests() {
  const h = await harness();
  const chart = h.chart("hourlyForecastChart");
  const soc = chart.data.datasets[4];
  assert.equal(chart.data.datasets.length, 5);
  assert.equal(chart.data.labels.length, 24);
  assert.deepEqual(Array.from(soc.data.slice(0, 7)), Array(7).fill(40));
  assert.equal(soc.data[23], 99);
  assert.equal(soc.data.filter((value) => value !== null).length, 24);
  assert.match(soc.label, /計画SOC/);
  assert.equal(soc.spanGaps, false);
  assert.match(h.elements.get("hourlyForecastNote").textContent, /計画SOC最大 23:00 99%/);
  assert.match(h.elements.get("hourlyForecastNote").textContent, /全日発電予測 0.20kWh/);
  assert.match(h.elements.get("hourlyForecastNote").textContent, /実績取得済み分.*7\/24/);
  assert.match(h.elements.get("hourlyForecastNote").textContent, /00〜06時のSOCは実測優先/);

  const night = fixture();
  night.forecast_hourly.forEach((row) => { row.actual_soc_percent = null; });
  await h.replace(night);
  const nightReference = Array.from(chart.data.datasets[4].data.slice(0, 7));
  assert.ok(nightReference.every((value) => Number.isFinite(value)));
  assert.ok(nightReference[0] < nightReference[6], "night charging must be visible before 07:00");
  assert.equal(nightReference[4], nightReference[3], "04:00 must show SOC before the 04-05 charging interval");
  assert.ok(nightReference[6] < night.forecast_hourly[7].forecast_soc_percent,
    "06:00 must not include the 06-07 charging interval before the saved 07:00 target");
  assert.deepEqual(Array.from(chart.data.datasets[4].data.slice(7)),
    night.forecast_hourly.slice(7).map((row) => row.forecast_soc_percent), "daytime optimizer trace must remain exact");
  night.forecast_hourly[0].actual_soc_percent = 0;
  night.forecast_hourly[4].actual_soc_percent = 17;
  await h.replace(night);
  assert.equal(chart.data.datasets[4].data[0], 0);
  assert.equal(chart.data.datasets[4].data[4], 17, "observations override night charging estimates");
  night.forecast_hourly[0].actual_soc_percent = 100;
  await h.replace(night);
  assert.match(h.elements.get("hourlyForecastNote").textContent, /計画SOC最大 23:00 99%/,
    "night observations must not be reported as the optimizer's planned SOC peak");
  for (const bad of [true, "17", NaN, -1, 101]) {
    night.forecast_hourly[4].actual_soc_percent = bad;
    await h.replace(night);
    assert.equal(chart.data.datasets[4].data[4], nightReference[4]);
  }
  const noCapacity = fixture();
  noCapacity.forecast_hourly.forEach((row) => { row.actual_soc_percent = null; row.forecast_capacity_kwh = null; });
  await h.replace(noCapacity);
  assert.ok(chart.data.datasets[4].data.slice(0, 7).every((value) => value === null));
  assert.equal(chart.data.datasets[4].data[7], 50);
  const differentTarget = fixture();
  differentTarget.forecast_hourly.forEach((row) => { row.actual_soc_percent = null; });
  differentTarget.latest_schedule.planned_target_soc_percent = 90;
  await h.replace(differentTarget);
  assert.deepEqual(Array.from(chart.data.datasets[4].data.slice(0, 7)), nightReference,
    "night reference must be anchored to the saved 07:00 SOC, not another schedule target");

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
  assert.deepEqual(Array.from(chart.data.datasets[4].data.slice(0, 7)), Array(7).fill(40));
  cross.forecast_hourly.forEach((row) => { row.actual_soc_percent = null; });
  await h.replace(cross);
  assert.ok(chart.data.datasets[4].data.slice(0, 7).every((value) => value === null));

  const legacy = fixture();
  legacy.forecast_hourly.forEach((row) => { row.forecast_soc_percent = null; });
  await h.replace(legacy);
  assert.match(chart.data.datasets[4].label, /参考SOC/);
  assert.deepEqual(Array.from(chart.data.datasets[4].data.slice(0, 7)), Array(7).fill(40));
  assert.match(h.elements.get("hourlyForecastNote").textContent, /参考SOC最大/);
  legacy.forecast_hourly[0].actual_soc_percent = 100;
  await h.replace(legacy);
  assert.equal(chart.data.datasets[4].data[0], 100);
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
  assert.deepEqual(Array.from(chart.data.datasets[4].data.slice(0, 7)), Array(7).fill(40));
  assert.ok(chart.data.datasets[4].data.slice(7).every((value) => value === null));

  const mixed = fixture();
  mixed.forecast_hourly[8].forecast_run_id = "another-run";
  await h.replace(mixed);
  assert.deepEqual(Array.from(chart.data.datasets[4].data.slice(0, 7)), Array(7).fill(40));
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
