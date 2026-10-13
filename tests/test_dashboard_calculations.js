const assert = require("node:assert/strict");
const {
  minuteOf,
  allocateNightGridCharge,
  plannedBatteryValues,
  forecastSocFromLatestActual,
  forecastNightSocAtHourStart,
} = require("../static/dashboard_calculations.js");

assert.equal(minuteOf("02:43"), 163);
assert.equal(minuteOf("04:00"), 240);
assert.equal(minuteOf("07:00"), 420);
assert.equal(minuteOf("23:30"), 1410);
assert.equal(minuteOf("24:00"), null);
assert.equal(minuteOf("2:3"), null);
assert.equal(minuteOf(""), null);

const rows = Array.from({ length: 24 }, (_, hour) => ({ hour }));
const daytime = allocateNightGridCharge(rows, 2, "02:30", "04:30");
assert.deepEqual(daytime.slice(2, 5), [0.5, 1, 0.5]);
assert.equal(daytime.reduce((sum, value) => sum + value, 0), 2);

const crossing = allocateNightGridCharge(rows, 2, "23:30", "01:30");
assert.deepEqual([crossing[23], crossing[0], crossing[1]], [0.5, 1, 0.5]);
assert.equal(crossing.reduce((sum, value) => sum + value, 0), 2);
assert.equal(allocateNightGridCharge(rows, 2, "04:00", "04:00").reduce((a, b) => a + b, 0), 0);

const fallback = allocateNightGridCharge(rows, 3, "", "");
assert.deepEqual(fallback.slice(4, 7), [1, 1, 1]);

assert.deepEqual(
  plannedBatteryValues(
    { setting_soc_target_percent: null, night_charge_kwh: null },
    { soc_charge_mode: "0", planned_target_soc_percent: 77, planned_night_charge_kwh: 3.1403 }
  ),
  { targetSocPercent: 77, nightChargeKwh: 3.1403 }
);
assert.deepEqual(
  plannedBatteryValues(
    { setting_soc_target_percent: 65, night_charge_kwh: 2.5 },
    { soc_charge_mode: "0", planned_target_soc_percent: 77, planned_night_charge_kwh: 3.1403 }
  ),
  { targetSocPercent: 77, nightChargeKwh: 3.1403 }
);
assert.deepEqual(
  plannedBatteryValues(
    { setting_soc_target_percent: 65, night_charge_kwh: 2.5 },
    { soc_charge_mode: "0" }
  ),
  { targetSocPercent: 65, nightChargeKwh: 2.5 }
);

const socFallback = forecastSocFromLatestActual(
  [
    { hour: 16, actual_soc_percent: 20, forecast_pv_kwh: 0, forecast_load_kwh: 1 },
    { hour: 17, actual_soc_percent: 10, forecast_pv_kwh: 0, forecast_load_kwh: 1 },
    { hour: 18, actual_soc_percent: null, forecast_pv_kwh: 2, forecast_load_kwh: 1 },
    { hour: 19, actual_soc_percent: null, forecast_pv_kwh: 0, forecast_load_kwh: 1 },
  ],
  10,
  1,
  1,
);
assert.deepEqual(socFallback, [20, 10, 10, 20]);
assert.deepEqual(
  forecastSocFromLatestActual([{ hour: 18, forecast_pv_kwh: 1, forecast_load_kwh: 1 }], 10, 1, 1),
  [null],
);

const nightRows = Array.from({ length: 24 }, (_, hour) => ({ hour }));
const nightGrid = nightRows.map(({ hour }) => hour >= 4 && hour < 7 ? 1 : 0);
assert.deepEqual(forecastNightSocAtHourStart(nightRows, nightGrid, 80, 10, 1),
  [50, 50, 50, 50, 50, 60, 70, ...Array(17).fill(null)]);
assert.deepEqual(forecastNightSocAtHourStart(nightRows, nightGrid, 80, 10, 0.9).slice(0, 7),
  [53, 53, 53, 53, 53, 62, 71]);
assert.deepEqual(forecastNightSocAtHourStart(nightRows, nightGrid.map(() => 0), 0, 10, 1).slice(0, 7), Array(7).fill(0));
assert.deepEqual(forecastNightSocAtHourStart(nightRows, allocateNightGridCharge(nightRows, 3, "04:30", "06:30"), 80, 10, 1).slice(4, 7),
  [50, 57.5, 72.5]);
assert.equal(forecastNightSocAtHourStart(nightRows, allocateNightGridCharge(nightRows, 3, "00:00", "03:00"), 80, 10, 1)[0], 50);
assert.deepEqual(forecastNightSocAtHourStart([...nightRows].reverse(), [...nightGrid].reverse(), 80, 10, 1),
  [...forecastNightSocAtHourStart(nightRows, nightGrid, 80, 10, 1)].reverse());
for (const invalid of [null, true, "80", NaN, -1, 101]) {
  assert.ok(forecastNightSocAtHourStart(nightRows, nightGrid, invalid, 10, 1).every((point) => point === null));
}
assert.ok(forecastNightSocAtHourStart(nightRows, nightGrid, 80, 0, 1).every((point) => point === null));
assert.ok(forecastNightSocAtHourStart(nightRows, nightGrid.slice(1), 80, 10, 1).every((point) => point === null));
assert.ok(forecastNightSocAtHourStart(nightRows.slice(1), nightGrid.slice(1), 80, 10, 1).every((point) => point === null));
assert.ok(forecastNightSocAtHourStart([{ hour: 1 }, ...nightRows.slice(1)], nightGrid, 80, 10, 1).every((point) => point === null));
