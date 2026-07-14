const assert = require("node:assert/strict");
const { minuteOf, allocateNightGridCharge } = require("../static/dashboard_calculations.js");

assert.equal(minuteOf("02:43"), 163);
assert.equal(minuteOf("04:00"), 240);
assert.equal(minuteOf("07:00"), 420);
assert.equal(minuteOf("23:30"), 1410);
assert.equal(minuteOf("24:00"), null);
assert.equal(minuteOf("2:3"), null);
assert.equal(minuteOf(""), null);

const rows = Array.from({ length: 24 }, (_, hour) => ({ hour }));
const daytime = allocateNightGridCharge(rows, 2, "02:30", "04:30");
assert.deepEqual(daytime.slice(2, 5), [0.5, 1, 0.5]);
assert.equal(daytime.reduce((sum, value) => sum + value, 0), 2);

const crossing = allocateNightGridCharge(rows, 2, "23:30", "01:30");
assert.deepEqual([crossing[23], crossing[0], crossing[1]], [0.5, 1, 0.5]);
assert.equal(crossing.reduce((sum, value) => sum + value, 0), 2);
assert.equal(allocateNightGridCharge(rows, 2, "04:00", "04:00").reduce((a, b) => a + b, 0), 0);

const fallback = allocateNightGridCharge(rows, 3, "", "");
assert.deepEqual(fallback.slice(4, 7), [1, 1, 1]);
