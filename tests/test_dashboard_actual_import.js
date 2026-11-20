const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const storageKey = "dashboardActualImport";
const execution = "solar-actual-import-abc12";
const completedAt = "2026-11-05T06:00:00Z";
const sources = ["dashboard_calculations.js", "dashboard_dates.js", "dashboard_api.js", "dashboard_store.js", "dashboard.js"]
  .map((filename) => [filename, fs.readFileSync(path.join(__dirname, "..", "static", filename), "utf8")]);

function browserGroup() {
  return { storage: new Map(), listeners: [], held: false, now: Date.parse("2026-11-05T07:00:00Z") };
}

function openPage(storage = new Map(), statuses = [], postStatus = 200, group = null) {
  const elements = new Map();
  const requests = [];
  const timers = [];
  const navigations = [];
  const bodies = [];
  const documentListeners = {};
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
  class Clock extends Date {
    constructor(...args) { super(...(args.length ? args : [Clock.now()])); }
    static now() { return group ? group.now : now; }
  }
  class Chart {
    constructor(_target, config) { this.data = config.data || { labels: [], datasets: [] }; this.options = config.options || {}; }
    update() {} resize() {} destroy() {}
  }
  const context = {
    console, URLSearchParams, Intl, Date: Clock, Chart,
    setTimeout(callback, delay) { timers.push({ callback, delay, at: Clock.now() + delay }); return timers.length; },
    clearTimeout(id) { if (timers[id - 1]) timers[id - 1].cancelled = true; },
    addEventListener(type, handler) { if (group && type === "storage") group.listeners.push({ owner: context, handler }); },
    sessionStorage: {
      getItem(key) { return storage.get(key) ?? null; },
      setItem(key, value) { storage.set(key, String(value)); },
      removeItem(key) { storage.delete(key); },
    },
    location: { search: "", assign(url) { navigations.push(url); } },
    document: {
      getElementById: element, createElement: (tag) => element(`created-${tag}-${elements.size}`),
      querySelector: element, querySelectorAll: () => [],
      addEventListener(type, handler) { documentListeners[type] = handler; }, visibilityState: "visible",
    },
    fetch: async (url, options = {}) => {
      requests.push({ url: String(url), method: options.method || "GET" });
      let status = 200;
      let payload = {};
      if (String(url) === "/api/dashboard/actual-import" && options.method === "POST") {
        bodies.push(JSON.parse(options.body));
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
  if (group) {
    context.localStorage = {
      getItem: (key) => group.storage.get(key) ?? null,
      setItem(key, value) {
        group.storage.set(key, String(value));
        for (const listener of group.listeners) if (listener.owner !== context) listener.handler({ key });
      },
    };
    context.navigator = { locks: {
      async request(_name, _options, callback) {
        if (group.held) return await callback(null);
        group.held = true;
        try { return await callback({}); } finally { group.held = false; }
      },
    } };
  }
  context.window = context;
  context.globalThis = context;
  vm.createContext(context);
  for (const [filename, source] of sources) vm.runInContext(source, context, { filename });
  return {
    elements, requests, timers, navigations, storage, bodies, context,
    button: element("importActualsBtn"), status: element("importActualsStatus"),
    postCount() { return requests.filter((r) => r.method === "POST").length; },
    polls() { return requests.filter((r) => r.url.startsWith("/api/dashboard/actual-import?")); },
    async tick(milliseconds = 5000) {
      const timer = timers.find((item) => !item.cancelled && item.delay === 5000);
      assert.ok(timer, "expected a scheduled status check");
      assert.equal(timer.delay, 5000, "polling cadence remains five seconds");
      timer.cancelled = true;
      if (group) group.now += milliseconds;
      else now += milliseconds;
      timer.callback();
      await settle();
    },
    async advance(milliseconds) {
      group.now += milliseconds;
      const due = timers.filter((timer) => !timer.cancelled && timer.at <= group.now);
      for (const timer of due) { timer.cancelled = true; timer.callback(); }
      await settle();
    },
    async becomeVisible() { documentListeners.visibilitychange(); await settle(); },
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

  const missingStorage = new Map([[storageKey, execution]]);
  const missingPage = openPage(missingStorage, [{ status: "succeeded", missing_review: {
    status: "checked", recoverable_dates: ["2026-11-01"], unavailable_dates: ["2026-11-02"],
  } }]);
  await settle();
  assert.match(missingStorage.get("dashboardActualImportNotice"), /CSV取得可能 2026-11-01/);
  assert.match(missingStorage.get("dashboardActualImportNotice"), /CSV未提供 2026-11-02/);
  assert.equal(missingPage.postCount(), 0);
  checks += 1;

  const unavailableStorage = new Map([[storageKey, execution]]);
  openPage(unavailableStorage, [{ status: "succeeded", missing_review: { status: "unavailable" } }]);
  await settle();
  assert.match(unavailableStorage.get("dashboardActualImportNotice"), /欠損確認はできませんでした/);
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

  const interval = 1800000;
  const scheduleKey = "dashboardActualImportSchedule";
  const timedGroup = browserGroup();
  const timed = openPage(new Map(), [{ status: "succeeded" }], 200, timedGroup);
  await settle();
  await timed.advance(interval - 1);
  assert.equal(timed.postCount(), 0, "opening and early timer wakeups do not import");
  await timed.advance(1);
  assert.equal(timed.postCount(), 1);
  assert.equal(timed.navigations.length, 1);
  assert.equal(JSON.parse(timedGroup.storage.get(scheduleKey)).pending, null);
  checks += 1;

  const tabs = browserGroup();
  const firstTab = openPage(new Map(), [{ status: "succeeded" }], 200, tabs);
  const secondTab = openPage(new Map(), [], 200, tabs);
  await settle();
  await firstTab.advance(interval);
  await secondTab.advance(0);
  assert.equal(firstTab.postCount() + secondTab.postCount(), 1, "tabs share one import per interval");
  assert.equal(secondTab.navigations.length, 1, "other tabs display the newly published data too");
  checks += 1;

  const unknownGroup = browserGroup();
  const autoUnknown = openPage(new Map(), [], 502, unknownGroup);
  await autoUnknown.advance(interval);
  assert.equal(autoUnknown.postCount(), 1);
  await autoUnknown.advance(interval);
  const newUnknownTab = openPage(new Map(), [], 200, unknownGroup);
  await newUnknownTab.advance(interval);
  newUnknownTab.button.listeners.click();
  await settle();
  assert.equal(newUnknownTab.postCount(), 0, "an unknown submission is not repeated even in another tab");
  assert.equal(JSON.parse(unknownGroup.storage.get(scheduleKey)).pending, "unknown");
  checks += 1;

  const failureGroup = browserGroup();
  const autoFailed = openPage(new Map(), [{ status: "failed", actuals_saved: false, completed_at: completedAt }, { status: "succeeded" }], 200, failureGroup);
  await autoFailed.advance(interval);
  await autoFailed.advance(interval);
  assert.equal(autoFailed.postCount(), 1, "a confirmed failure pauses recurring submissions for review");
  assert.equal(autoFailed.button.disabled, false);
  autoFailed.button.listeners.click();
  await settle();
  assert.equal(autoFailed.postCount(), 2, "explicit manual recovery remains possible");
  assert.equal(autoFailed.navigations.length, 1);
  checks += 1;

  const dateGroup = browserGroup();
  dateGroup.now = Date.parse("2026-11-05T14:55:00Z");
  const autoDate = openPage(new Map(), [{ status: "succeeded" }], 200, dateGroup);
  autoDate.elements.get("importActualsDate").value = "2026-10-01";
  autoDate.elements.get("importActualsDate").listeners.change();
  await autoDate.advance(interval);
  assert.deepEqual(autoDate.bodies, [{}], "automatic import delegates today's date to the server after midnight, ignoring a past manual selection");
  checks += 1;

  const asleepGroup = browserGroup();
  const asleep = openPage(new Map(), [{ status: "running" }], 200, asleepGroup);
  await asleep.advance(interval * 8);
  await asleep.becomeVisible();
  await asleep.becomeVisible();
  assert.equal(asleep.postCount(), 1, "waking after missed intervals submits only once");
  assert.equal(asleep.button.disabled, true);
  checks += 1;

  const busyGroup = browserGroup();
  const waiting = openPage(new Map(), [{ status: "succeeded" }], 200, busyGroup);
  busyGroup.held = true;
  await waiting.advance(interval);
  assert.equal(waiting.postCount(), 0);
  busyGroup.held = false;
  await waiting.advance(60000);
  assert.equal(waiting.postCount(), 1, "another tab's lock defers work without queuing a duplicate submission");
  checks += 1;

  const resumeGroup = browserGroup();
  resumeGroup.storage.set(scheduleKey, JSON.stringify({ nextAt: resumeGroup.now, pending: execution, paused: false }));
  const sharedResume = openPage(new Map(), [{ status: "succeeded" }], 200, resumeGroup);
  await sharedResume.becomeVisible();
  assert.equal(sharedResume.postCount(), 0, "a known shared execution is polled, never resubmitted");
  assert.equal(sharedResume.polls().length, 1);
  assert.equal(sharedResume.navigations.length, 1);
  checks += 1;

  const storageFailureGroup = browserGroup();
  const storageFailure = openPage(new Map(), [{ status: "succeeded" }], 200, storageFailureGroup);
  const originalSet = storageFailure.context.localStorage.setItem;
  storageFailure.context.localStorage.setItem = (key, value) => {
    if (JSON.parse(value).pending === execution) throw new Error("storage_full_after_submission");
    originalSet(key, value);
  };
  await storageFailure.advance(interval);
  assert.equal(storageFailure.postCount(), 1);
  assert.equal(storageFailure.polls().length, 1, "a storage error after acceptance must not stop receipt polling");
  assert.equal(storageFailure.navigations.length, 1);
  checks += 1;

  const corruptGroup = browserGroup();
  corruptGroup.storage.set(scheduleKey, JSON.stringify({ nextAt: "invalid", pending: "unknown", paused: false }));
  const corrupt = openPage(new Map(), [], 200, corruptGroup);
  await corrupt.advance(interval);
  await corrupt.becomeVisible();
  assert.equal(corrupt.postCount(), 0, "corrupted scheduling state never resets an unknown submission and auto-resends");
  assert.equal(JSON.parse(corruptGroup.storage.get(scheduleKey)).pending, "unknown");
  checks += 1;

  const manualGroup = browserGroup();
  const manualDate = openPage(new Map(), [{ status: "succeeded" }], 200, manualGroup);
  manualDate.elements.get("importActualsDate").value = "2026-10-01";
  manualDate.elements.get("importActualsDate").listeners.change();
  manualDate.button.listeners.click();
  await settle();
  assert.deepEqual(manualDate.bodies, [{ target_date: "2026-10-01" }], "manual imports retain the explicitly selected date");
  checks += 1;

  for (const initialized of [false, true]) {
    const legacyGroup = browserGroup();
    if (initialized) legacyGroup.storage.set(scheduleKey, JSON.stringify({ nextAt: legacyGroup.now + interval, pending: null, paused: false }));
    openPage(new Map([[storageKey, "unknown"]]), [], 200, legacyGroup);
    const legacyOtherTab = openPage(new Map(), [], 200, legacyGroup);
    await legacyOtherTab.advance(interval);
    assert.equal(legacyOtherTab.postCount(), 0, "an existing tab's legacy unknown submission must also block automatic submission in new tabs");
    assert.equal(JSON.parse(legacyGroup.storage.get(scheduleKey)).pending, "unknown");
    checks += 1;
  }
  console.log(`dashboard actual import reload: PASS (${checks} scenarios)`);
}

module.exports = verifyActualImportRecovery;
if (require.main === module) verifyActualImportRecovery().catch((error) => { console.error(error); process.exitCode = 1; });
