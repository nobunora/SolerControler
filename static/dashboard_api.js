(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.DashboardApi = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  function queryString(params) {
    const output = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === "") continue;
      output.set(key, String(value));
    }
    return output.toString();
  }

  async function fetchJson(path, fetchImpl = fetch) {
    const response = await fetchImpl(path, { credentials: "include" });
    if (!response.ok) throw new Error(`api_error_${response.status}`);
    return await response.json();
  }

  let bootstrapRequest = null;

  function hasBootstrapRows(payload) {
    return ["pv_daily", "energy_daily", "cost_daily", "battery_daily"].some((key) => payload[key]?.length);
  }

  function preloadBootstrap(initialPayload, fetchImpl = fetch) {
    if (hasBootstrapRows(initialPayload) || bootstrapRequest) return;
    // Handle rejection immediately: chart scripts may still be downloading.
    bootstrapRequest = fetchJson(afterImport("/api/dashboard/bootstrap"), fetchImpl)
      .then((payload) => ({ payload }), (error) => ({ error }));
  }

  async function fetchBootstrap(fetchImpl = fetch) {
    if (bootstrapRequest) {
      const result = await bootstrapRequest;
      bootstrapRequest = null;
      if (result.error) throw result.error;
      return result.payload;
    }
    return await fetchJson(afterImport("/api/dashboard/bootstrap"), fetchImpl);
  }

  async function fetchHistory(fetchImpl = fetch) {
    return await fetchJson(afterImport("/api/dashboard/history"), fetchImpl);
  }

  function afterImport(path) {
    const search = typeof window !== "undefined" && window.location ? window.location.search : "";
    const execution = new URLSearchParams(search).get("actual_import");
    return execution ? `${path}?${queryString({ actual_import: execution })}` : path;
  }

  async function fetchSlice(options = {}, fetchImpl = fetch) {
    const query = queryString({
      window_days: options.window_days,
      end_date: options.end_date,
      include_static: options.include_static ? "1" : "0",
    });
    const response = await fetchImpl(`/api/dashboard?${query}`, { credentials: "include" });
    if (!response.ok) throw new Error(`api_error_${response.status}`);
    return await response.json();
  }

  return { queryString, fetchJson, fetchBootstrap, preloadBootstrap, hasBootstrapRows, fetchHistory, fetchSlice };
});
