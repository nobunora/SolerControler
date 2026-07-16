(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.DashboardDates = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  function isoDateAdd(dateStr, deltaDays) {
    const match = /^([0-9]{4})-([0-9]{2})-([0-9]{2})$/.exec(String(dateStr || ""));
    if (!match || !Number.isInteger(deltaDays)) return dateStr;
    const year = Number(match[1]);
    const month = Number(match[2]);
    const day = Number(match[3]);
    const base = new Date(Date.UTC(year, month - 1, day));
    if (
      Number.isNaN(base.getTime()) ||
      base.getUTCFullYear() !== year ||
      base.getUTCMonth() !== month - 1 ||
      base.getUTCDate() !== day
    ) return dateStr;
    base.setUTCDate(base.getUTCDate() + deltaDays);
    return `${base.getUTCFullYear()}-${String(base.getUTCMonth() + 1).padStart(2, "0")}-${String(base.getUTCDate()).padStart(2, "0")}`;
  }

  function todayIsoJst(now = new Date()) {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: "Asia/Tokyo", year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(now);
    const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
    return `${values.year || "1970"}-${String(values.month || "01").padStart(2, "0")}-${String(values.day || "01").padStart(2, "0")}`;
  }

  return { isoDateAdd, todayIsoJst };
});
