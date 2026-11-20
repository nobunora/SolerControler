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
assert.deepEqual(store.createPeriodState(), { mode: "month", month: null, year: null, initialized: false });

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
  let bootstrapRequest = null;
  const bootstrap = await api.fetchBootstrap(async (url, options) => {
    bootstrapRequest = { url, options };
    return { ok: true, json: async () => ({ meta: { snapshot_kind: "bootstrap" } }) };
  });
  assert.deepEqual(bootstrap, { meta: { snapshot_kind: "bootstrap" } });
  assert.deepEqual(bootstrapRequest, {
    url: "/api/dashboard/bootstrap",
    options: { credentials: "include" },
  });

  let earlyRequests = 0;
  const earlyFetch = async () => {
    earlyRequests += 1;
    return { ok: true, json: async () => ({ meta: { early: true } }) };
  };
  api.preloadBootstrap({ energy_daily: [{ date: "2026-07-16" }] }, earlyFetch);
  assert.equal(earlyRequests, 0, "embedded bootstrap data must not be downloaded again");
  api.preloadBootstrap({}, earlyFetch);
  api.preloadBootstrap({}, earlyFetch);
  assert.equal(earlyRequests, 1, "early loading starts once while chart scripts load");
  assert.deepEqual(await api.fetchBootstrap(earlyFetch), { meta: { early: true } });
  assert.equal(earlyRequests, 1, "renderer consumes the existing request");
  await api.fetchBootstrap(earlyFetch);
  assert.equal(earlyRequests, 2, "later explicit refreshes can fetch fresh data");
  api.preloadBootstrap({}, async () => { throw new Error("early_load_failure"); });
  await new Promise((resolve) => setImmediate(resolve));
  await assert.rejects(() => api.fetchBootstrap(earlyFetch), /early_load_failure/);
  assert.equal(earlyRequests, 2, "a failed early request is reported without a duplicate request");

  global.window = { location: { search: "?actual_import=solar-actual-import-abc12" } };
  let importedPath;
  api.preloadBootstrap({}, async (url) => {
    importedPath = url;
    return { ok: true, json: async () => ({}) };
  });
  await api.fetchBootstrap();
  delete global.window;
  assert.equal(importedPath, "/api/dashboard/bootstrap?actual_import=solar-actual-import-abc12", "early loading preserves receipt-based cache refresh");

  let historyRequest = null;
  await api.fetchHistory(async (url, options) => {
    historyRequest = { url, options };
    return { ok: true, json: async () => ({ meta: { snapshot_kind: "history" } }) };
  });
  assert.deepEqual(historyRequest, {
    url: "/api/dashboard/history",
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
