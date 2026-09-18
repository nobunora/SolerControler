(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.DashboardCalculations = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function plannedBatteryValues(batteryRow, schedule) {
    const battery = batteryRow || {};
    const plan = schedule || {};
    const finiteOrNull = (value) => {
      if (value == null || value === "") return null;
      const parsed = Number(value);
      return Number.isFinite(parsed) ? parsed : null;
    };
    const plannedTarget = finiteOrNull(plan.planned_target_soc_percent);
    const plannedNightCharge = finiteOrNull(plan.planned_night_charge_kwh);
    const batteryTarget = finiteOrNull(battery.setting_soc_target_percent);
    const batteryNightCharge = finiteOrNull(battery.night_charge_kwh);
    return {
      targetSocPercent: plannedTarget ?? batteryTarget,
      nightChargeKwh: plannedNightCharge ?? batteryNightCharge ?? 0,
    };
  }

  return { plannedBatteryValues };
});
