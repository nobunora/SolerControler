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

  return { queryString, fetchSlice };
});
