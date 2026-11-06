const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const storageKey = "dashboardActualImport";
const execution = "solar-actual-import-abc12";
const completedAt = "2026-11-05T06:00:00Z";
const sources = ["dashboard_calculations.js", "dashboard_dates.js", "dashboard_api.js", "dashboard_store.js", "dashboard.js"]
  .map((filename) => [filename, fs.readFileSync(path.join(__dirname, "..", "static", filename), "utf8")]);

function openPage(storage = new Map(), statuses = [], postStatus = 200) {
  const elements = new Map();
  const requests = [];
  const timers = [];
  const navigations = [];
  let now = Date.parse("2026-11-05T07:00:00Z");
  function element(id = "") {
    if (!elements.has(id)) elements.set(id, {
      id, style: {}, dataset: {}, listeners: {}, classList: { add() {}, remove() {}, toggle() {} },
      addEventListener(type, handler) { this.listeners[type] = handler; },
      appendChild() {}, replaceChildren() {}, querySelectorAll() { return []; }, getContext() { return {}; },
      textContent: "", innerHTML: "", value: "", disabled: false,
    });
    return elements.get(id);
  }
  class Clock extends Date { static now() { return now; } }
  class Chart {
    constructor(_target, config) { this.data = config.data || { labels: [], datasets: [] }; this.options = config.options || {}; }
    update() {} resize() {} destroy() {}
  }
  const context = {
    console, URLSearchParams, Intl, Date: Clock, Chart,
    setTimeout(callback, delay) { timers.push({ callback, delay }); return timers.length; },
    clearTimeout() {}, addEventListener() {},
    sessionStorage: {
      getItem(key) { return storage.get(key) ?? null; },
      setItem(key, value) { storage.set(key, String(value)); },
      removeItem(key) { storage.delete(key); },
    },
    location: { search: "", assign(url) { navigations.push(url); } },
    document: {
      getElementById: element, createElement: (tag) => element(`created-${tag}-${elements.size}`),
      querySelector: element, querySelectorAll: () => [],
    },
    fetch: async (url, options = {}) => {
      requests.push({ url: String(url), method: options.method || "GET" });
      let status = 200;
      let payload = {};
      if (String(url) === "/api/dashboard/actual-import" && options.method === "POST") {
        status = postStatus;
        payload = { execution };
      } else if (String(url).startsWith("/api/dashboard/actual-import?")) {
        const result = statuses.shift() || { status: "running" };
        if (result instanceof Error) throw result;
        payload = result;
      }
      return { ok: status >= 200 && status < 300, status, json: async () => payload };
    },
    __DASHBOARD_DATA__: {},
  };
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  for (const [filename, source] of sources) vm.runInContext(source, context, { filename });
  return {
    elements, requests, timers, navigations, storage,
    button: element("importActualsBtn"), status: element("importActualsStatus"),
    postCount() { return requests.filter((r) => r.method === "POST").length; },
    polls() { return requests.filter((r) => r.url.startsWith("/api/dashboard/actual-import?")); },
    async tick(milliseconds = 5000) {
      const timer = timers.shift();
      assert.ok(timer, "expected a scheduled status check");
      assert.equal(timer.delay, 5000, "polling cadence remains five seconds");
      now += milliseconds;
      timer.callback();
      await settle();
    },
  };
}

async function settle() { await new Promise((resolve) => setImmediate(resolve)); }

async function verifyActualImportRecovery() {
  let checks = 0;
  const storage = new Map();
  const beforeReload = openPage(storage, [{ status: "running" }]);
  await settle();
  assert.deepEqual(beforeReload.requests, [{ url: "/api/dashboard/bootstrap", method: "GET" }]);
  beforeReload.button.listeners.click();
  beforeReload.button.listeners.click();
  await settle();
  assert.equal(beforeReload.postCount(), 1, "double click cannot launch another job");
  assert.equal(storage.get(storageKey), execution);
  assert.equal(beforeReload.button.disabled, true);
  checks += 1;

  const afterReload = openPage(storage, [{ status: "running" }, { status: "succeeded" }]);
  await settle();
  assert.equal(afterReload.postCount(), 0, "reload must not POST");
  assert.equal(afterReload.polls().length, 1, "reload checks the existing result immediately");
  assert.equal(afterReload.polls()[0].url, `/api/dashboard/actual-import?execution=${execution}`);
  assert.equal(afterReload.button.disabled, true);
  assert.match(afterReload.status.textContent, /現在の実行結果を待って/);
  assert.equal(afterReload.requests.filter((r) => r.url === "/api/dashboard/bootstrap").length, 1);
  assert.equal(afterReload.requests.some((r) => r.url.includes("/history")), false);
  afterReload.button.listeners.click();
  assert.equal(afterReload.postCount(), 0);
  await afterReload.tick();
  assert.equal(storage.has(storageKey), false);
  assert.deepEqual(afterReload.navigations, [`/?actual_import=${execution}`]);
  assert.equal(afterReload.postCount(), 0);
  checks += 1;

  const completedPage = openPage(storage);
  await settle();
  assert.equal(completedPage.button.disabled, false);
  assert.equal(completedPage.polls().length, 0);
  assert.equal(completedPage.postCount(), 0);
  checks += 1;

  const partialStorage = new Map([[storageKey, execution]]);
  const partial = openPage(partialStorage, [{ status: "partial", pending_dates: ["2026-11-04"] }]);
  await settle();
  assert.equal(partialStorage.has(storageKey), false);
  assert.match(partialStorage.get("dashboardActualImportNotice"), /2026-11-04/);
  assert.equal(partial.navigations.length, 1);
  assert.equal(partial.postCount(), 0);
  checks += 1;

  for (const actualsSaved of [false, true]) {
    const known = openPage(new Map([[storageKey, execution]]), [
      { status: "failed", actuals_saved: actualsSaved, completed_at: completedAt },
    ]);
    await settle();
    assert.equal(known.button.disabled, false, "confirmed terminal failure can release the button");
    assert.equal(known.storage.has(storageKey), false);
    assert.equal(known.postCount(), 0, "recovery never submits a replacement automatically");
    assert.match(known.status.textContent, actualsSaved ? /表示用データの更新に失敗/ : /取込みに失敗/);
    checks += 1;
  }

  for (const result of [
    { status: "failed", actuals_saved: null, completed_at: completedAt },
    { status: "failed", completed_at: completedAt },
    { status: "failed", actuals_saved: false },
    { status: "failed", actuals_saved: false, completed_at: "invalid" },
    { status: "failed", actuals_saved: false, completed_at: 0 },
    { status: "busy" },
  ]) {
    const unknown = openPage(new Map([[storageKey, execution]]), [result]);
    await settle();
    assert.equal(unknown.button.disabled, true);
    assert.equal(unknown.storage.get(storageKey), execution);
    assert.equal(unknown.postCount(), 0);
    unknown.button.listeners.click();
    assert.equal(unknown.postCount(), 0);
    checks += 1;
  }

  const transient = openPage(new Map([[storageKey, execution]]), [
    new Error("temporary_status_failure"), { status: "running" }, { status: "succeeded" },
  ]);
  await settle();
  assert.match(transient.status.textContent, /通信確認を待って/);
  assert.equal(transient.button.disabled, true);
  await transient.tick();
  assert.match(transient.status.textContent, /現在の実行結果を待って/);
  await transient.tick();
  assert.equal(transient.navigations.length, 1);
  assert.equal(transient.postCount(), 0);
  assert.ok(transient.polls().every((r) => r.url.endsWith(`execution=${execution}`)));
  checks += 1;

  const timeout = openPage(new Map([[storageKey, execution]]), [{ status: "running" }]);
  await settle();
  await timeout.tick(720000);
  assert.equal(timeout.polls().length, 1, "deadline does not add a late request");
  assert.equal(timeout.button.disabled, true);
  assert.equal(timeout.storage.get(storageKey), execution);
  assert.equal(timeout.postCount(), 0);
  assert.match(timeout.status.textContent, /結果を確認できません/);
  checks += 1;

  const unknownSubmission = openPage(new Map([[storageKey, "unknown"]]));
  await settle();
  assert.equal(unknownSubmission.button.disabled, true);
  assert.equal(unknownSubmission.polls().length, 0);
  assert.equal(unknownSubmission.postCount(), 0);
  checks += 1;

  for (const code of [400, 401, 403, 502]) {
    const rejected = openPage(new Map(), [], code);
    await settle();
    rejected.button.listeners.click();
    await settle();
    assert.equal(rejected.postCount(), 1);
    assert.equal(rejected.button.disabled, code === 502);
    assert.equal(rejected.storage.get(storageKey), code === 502 ? "unknown" : undefined);
    if (code === 502) {
      const reloadUnknown = openPage(rejected.storage);
      await settle();
      assert.equal(reloadUnknown.postCount(), 0, "unknown submission is never re-sent after reload");
      assert.equal(reloadUnknown.button.disabled, true);
    }
    checks += 1;
  }
  console.log(`dashboard actual import reload: PASS (${checks} scenarios)`);
}

module.exports = verifyActualImportRecovery;
if (require.main === module) verifyActualImportRecovery().catch((error) => { console.error(error); process.exitCode = 1; });
