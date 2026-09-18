const assert = require("node:assert/strict");
const {
  plannedBatteryValues,
  forecastSocValues,
  forecastGridChargeValues,
} = require("../static/dashboard_calculations.js");

assert.deepEqual(
  plannedBatteryValues(
    { setting_soc_target_percent: 65, night_charge_kwh: 2.5 },
    { soc_charge_mode: "50", planned_target_soc_percent: 100, planned_night_charge_kwh: 9.93 }
  ),
  { targetSocPercent: 100, nightChargeKwh: 9.93 }
);

assert.deepEqual(
  plannedBatteryValues(
    { setting_soc_target_percent: 65, night_charge_kwh: 2.5 },
    { soc_charge_mode: "50" }
  ),
  { targetSocPercent: 65, nightChargeKwh: 2.5 }
);

assert.deepEqual(
  plannedBatteryValues(
    { setting_soc_target_percent: null, night_charge_kwh: null },
    { soc_charge_mode: "50" }
  ),
  { targetSocPercent: null, nightChargeKwh: 0 }
);

assert.deepEqual(
  forecastSocValues([
    { hour: 0, actual_soc_percent: 41, forecast_soc_percent: null },
    { hour: 3, actual_soc_percent: null, forecast_soc_percent: 52.5 },
    { hour: 4, actual_soc_percent: 55, forecast_soc_percent: 56.5 },
    { hour: 5, actual_soc_percent: null, forecast_soc_percent: null },
  ]),
  [41, 52.5, 55, null]
);

assert.deepEqual(
  forecastGridChargeValues([
    { hour: 3, forecast_grid_charge_kwh: 1.5 },
    { hour: 4, forecast_grid_charge_kwh: 0 },
    { hour: 5, forecast_grid_charge_kwh: null },
    { hour: 6, forecast_grid_charge_kwh: -1 },
  ]),
  [1.5, 0, null, null]
);
