(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  if (root) root.DashboardStore = api;
})(typeof window !== "undefined" ? window : globalThis, function () {
  function createStore() {
    return {
      meta: null, pvDaily: new Map(), hourly: new Map(), energy: new Map(), cost: new Map(),
      battery: new Map(), batteryFlow: new Map(), monthly: [], params: [], latestSchedule: null,
      dashboardWarnings: [], pvForecastDiagnostics: {}, dailyReview: {}, dailyReviews: new Map(), latestReviewDate: null,
      dates: [], loadingOlder: false,
    };
  }

  function createPeriodState() {
    return { mode: "all", month: null, year: null, initialized: false };
  }

  return { createStore, createPeriodState };
});
