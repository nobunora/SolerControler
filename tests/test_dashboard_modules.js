const assert = require("node:assert/strict");
const dates = require("../static/dashboard_dates.js");
const api = require("../static/dashboard_api.js");
const store = require("../static/dashboard_store.js");

assert.equal(dates.isoDateAdd("2026-07-31", 1), "2026-08-01");
assert.equal(dates.isoDateAdd("2026-03-01", -1), "2026-02-28");
assert.equal(dates.isoDateAdd("2026-02-30", 1), "2026-02-30");
assert.equal(dates.isoDateAdd("2026-07-16", Number.NaN), "2026-07-16");
assert.equal(dates.todayIsoJst(new Date("2026-07-15T15:30:00Z")), "2026-07-16");
assert.equal(api.queryString({ window_days: 31, end_date: "", include_static: "1" }), "window_days=31&include_static=1");

const first = store.createStore();
const second = store.createStore();
first.pvDaily.set("2026-07-16", {});
assert.equal(second.pvDaily.size, 0);
assert.deepEqual(store.createPeriodState(), { mode: "all", month: null, year: null, initialized: false });

(async () => {
  let request = null;
  const payload = await api.fetchSlice(
    { window_days: 31, end_date: "2026-07-16", include_static: false },
    async (url, options) => {
      request = { url, options };
      return { ok: true, json: async () => ({ pv_daily: [] }) };
    },
  );
  assert.deepEqual(payload, { pv_daily: [] });
  assert.deepEqual(request, {
    url: "/api/dashboard?window_days=31&end_date=2026-07-16&include_static=0",
    options: { credentials: "include" },
  });
  await assert.rejects(
    () => api.fetchSlice({}, async () => ({ ok: false, status: 503 })),
    /api_error_503/,
  );
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
