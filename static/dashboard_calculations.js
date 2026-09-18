(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.DashboardCalculations = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function finiteOrNull(value) {
    if (value == null || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function plannedBatteryValues(batteryRow, schedule) {
    const battery = batteryRow || {};
    const plan = schedule || {};
    const plannedTarget = finiteOrNull(plan.planned_target_soc_percent);
    const plannedNightCharge = finiteOrNull(plan.planned_night_charge_kwh);
    const batteryTarget = finiteOrNull(battery.setting_soc_target_percent);
    const batteryNightCharge = finiteOrNull(battery.night_charge_kwh);
    return {
      targetSocPercent: plannedTarget ?? batteryTarget,
      nightChargeKwh: plannedNightCharge ?? batteryNightCharge ?? 0,
    };
  }

  function forecastSocValues(rows) {
    return rows.map((row) => {
      const actual = finiteOrNull(row.actual_soc_percent);
      if (actual != null && actual >= 0 && actual <= 100) return actual;
      const forecast = finiteOrNull(row.forecast_soc_percent);
      return forecast != null && forecast >= 0 && forecast <= 100 ? forecast : null;
    });
  }

  function forecastGridChargeValues(rows) {
    return rows.map((row) => {
      const value = finiteOrNull(row.forecast_grid_charge_kwh);
      return value != null && value >= 0 ? value : null;
    });
  }

  return { plannedBatteryValues, forecastSocValues, forecastGridChargeValues };
});
