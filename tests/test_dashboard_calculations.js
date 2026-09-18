const assert = require("node:assert/strict");
const { plannedBatteryValues } = require("../static/dashboard_calculations.js");

assert.deepEqual(
  plannedBatteryValues(
    { setting_soc_target_percent: null, night_charge_kwh: null },
    { soc_charge_mode: "50", planned_target_soc_percent: 100, planned_night_charge_kwh: 9.93 }
  ),
  { targetSocPercent: 100, nightChargeKwh: 9.93 }
);

assert.deepEqual(
  plannedBatteryValues(
    { setting_soc_target_percent: 65, night_charge_kwh: 2.5 },
    { soc_charge_mode: "50", planned_target_soc_percent: 77, planned_night_charge_kwh: 3.1403 }
  ),
  { targetSocPercent: 77, nightChargeKwh: 3.1403 }
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
