(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.DashboardCalculations = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  function minuteOf(value) {
    const match = /^(\d{1,2}):(\d{2})$/.exec(String(value || "").trim());
    if (!match) return null;
    const hour = Number(match[1]);
    const minute = Number(match[2]);
    return hour >= 0 && hour < 24 && minute >= 0 && minute < 60 ? hour * 60 + minute : null;
  }

  function allocateNightGridCharge(rows, totalKwh, startTime, endTime) {
    const total = Math.max(0, Number(totalKwh) || 0);
    if (total <= 0) return rows.map(() => 0);
    let start = minuteOf(startTime);
    let end = minuteOf(endTime);
    if (start == null || end == null) {
      start = 4 * 60;
      end = 7 * 60;
    }
    if (start === end) return rows.map(() => 0);
    if (end < start) end += 24 * 60;
    const overlaps = rows.map((row) => {
      const hour = Number(row.hour);
      if (!Number.isFinite(hour)) return 0;
      const hourStart = hour * 60;
      const direct = Math.max(0, Math.min(hourStart + 60, end) - Math.max(hourStart, start));
      const nextDay = Math.max(
        0,
        Math.min(hourStart + 24 * 60 + 60, end) - Math.max(hourStart + 24 * 60, start)
      );
      return direct + nextDay;
    });
    const totalOverlap = overlaps.reduce((sum, minutes) => sum + minutes, 0);
    if (totalOverlap <= 0) return rows.map(() => 0);
    return overlaps.map((minutes) => total * minutes / totalOverlap);
  }

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
      targetSocPercent: plannedTarget ?? batteryTarget ?? finiteOrNull(plan.soc_charge_mode),
      nightChargeKwh: plannedNightCharge ?? batteryNightCharge ?? 0,
    };
  }

  function forecastSocFromLatestActual(rows, capacityKwh, chargeEfficiency, dischargeEfficiency) {
    const capacity = Math.max(0.1, Number(capacityKwh) || 0.1);
    const chargeEff = Math.max(0.01, Number(chargeEfficiency) || 0.01);
    const dischargeEff = Math.max(0.01, Number(dischargeEfficiency) || 0.01);
    const actualSoc = rows.map((row) => {
      if (row.actual_soc_percent == null || row.actual_soc_percent === "") return null;
      const value = Number(row.actual_soc_percent);
      return Number.isFinite(value) && value >= 0 && value <= 100 ? value : null;
    });
    let anchorIndex = -1;
    for (let index = 0; index < actualSoc.length; index += 1) {
      if (actualSoc[index] != null) anchorIndex = index;
    }
    if (anchorIndex < 0) return rows.map(() => null);

    let energyKwh = actualSoc[anchorIndex] / 100 * capacity;
    return rows.map((row, index) => {
      if (index <= anchorIndex) return actualSoc[index];
      const socAtHourStart = Math.max(0, Math.min(100, energyKwh / capacity * 100));
      const pvKwh = row.forecast_pv_kwh == null ? null : Number(row.forecast_pv_kwh);
      const loadKwh = row.forecast_load_kwh == null ? null : Number(row.forecast_load_kwh);
      const chargeKwh = row.forecast_charge_kwh == null ? null : Number(row.forecast_charge_kwh);
      if (Number.isFinite(chargeKwh) && chargeKwh > 0) {
        energyKwh += chargeKwh * chargeEff;
      } else if (Number.isFinite(pvKwh) && Number.isFinite(loadKwh)) {
        const net = pvKwh - loadKwh;
        energyKwh += net >= 0 ? net * chargeEff : net / dischargeEff;
      }
      energyKwh = Math.max(0, Math.min(capacity, energyKwh));
      return Math.round(socAtHourStart * 10) / 10;
    });
  }

  return { minuteOf, allocateNightGridCharge, plannedBatteryValues, forecastSocFromLatestActual };
});
