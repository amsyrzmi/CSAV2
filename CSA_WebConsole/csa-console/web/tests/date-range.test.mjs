import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

function moduleUrl(source) {
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const {
  monthGrid, presets, selectDay, toInstants, describeRange, addDays, addMonths, parseDay, todayLocal,
} = await import(moduleUrl(await readFile(new URL("../lib/date-range.ts", import.meta.url), "utf8")));

const days = (rows) => rows.flat().filter((cell) => cell !== null);

test("monthGrid has 6 or fewer rows of 7 and Monday first", () => {
  // November 2026 starts on a Sunday: six leading nulls, so Monday-first needs 6 rows.
  const november = monthGrid(2026, 11);
  assert.ok(november.length <= 6);
  assert.ok(november.every((row) => row.length === 7));
  assert.deepEqual(november[0], [null, null, null, null, null, null, "2026-11-01"]);
  assert.equal(november.at(-1).at(-1), null); // 30 Nov is a Monday
  // Sunday first moves the 1st to the first column.
  assert.equal(monthGrid(2026, 11, 0)[0][0], "2026-11-01");
  // A month that starts on Monday has no leading padding.
  assert.equal(monthGrid(2026, 6)[0][0], "2026-06-01");
  // The worst case still fits six rows.
  for (let month = 1; month <= 12; month += 1) {
    for (const start of [0, 1]) {
      const rows = monthGrid(2026, month, start);
      assert.ok(rows.length >= 4 && rows.length <= 6, `${month}/${start}`);
      assert.ok(rows.every((row) => row.length === 7));
    }
  }
});

test("monthGrid February 2028 (leap) holds 29 days", () => {
  const february = days(monthGrid(2028, 2));
  assert.equal(february.length, 29);
  assert.equal(february.at(-1), "2028-02-29");
  assert.equal(days(monthGrid(2027, 2)).length, 28);
  assert.equal(days(monthGrid(2100, 2)).length, 28); // not a leap year
  assert.equal(days(monthGrid(2000, 2)).length, 29);
  assert.equal(days(monthGrid(2026, 12)).at(-1), "2026-12-31");
});

test("presets last7 spans exactly 7 days ending today", () => {
  const list = presets("2026-11-02");
  assert.deepEqual(list.map((p) => p.key), ["today", "last7", "last30", "month", "clear"]);
  assert.deepEqual(list.map((p) => p.label), ["Today", "Last 7 days", "Last 30 days", "This month", "Clear"]);
  const by = Object.fromEntries(list.map((p) => [p.key, p.range]));
  assert.deepEqual(by.today, { from: "2026-11-02", to: "2026-11-02" });
  assert.deepEqual(by.last7, { from: "2026-10-27", to: "2026-11-02" });
  assert.deepEqual(by.last30, { from: "2026-10-04", to: "2026-11-02" });
  assert.deepEqual(by.month, { from: "2026-11-01", to: "2026-11-30" });
  assert.deepEqual(by.clear, { from: null, to: null });
  // Across a year start and a leap day.
  assert.deepEqual(presets("2027-01-03").find((p) => p.key === "last7").range, { from: "2026-12-28", to: "2027-01-03" });
  assert.equal(presets("2028-03-01").find((p) => p.key === "last7").range.from, "2028-02-24");
  assert.equal(presets("2028-02-10").find((p) => p.key === "month").range.to, "2028-02-29");
});

test("selectDay first click sets a one-day range", () => {
  assert.deepEqual(selectDay({ from: null, to: null }, "2026-11-12"), { from: "2026-11-12", to: "2026-11-12" });
});

test("selectDay second earlier click swaps ends", () => {
  const first = selectDay({ from: null, to: null }, "2026-11-12");
  assert.deepEqual(selectDay(first, "2026-11-18"), { from: "2026-11-12", to: "2026-11-18" });
  assert.deepEqual(selectDay(first, "2026-11-03"), { from: "2026-11-03", to: "2026-11-12" });
  assert.deepEqual(selectDay(first, "2026-11-12"), { from: "2026-11-12", to: "2026-11-12" });
});

test("selectDay third click restarts", () => {
  const range = { from: "2026-11-12", to: "2026-11-18" };
  assert.deepEqual(selectDay(range, "2026-11-20"), { from: "2026-11-20", to: "2026-11-20" });
  assert.deepEqual(selectDay(range, "2026-11-01"), { from: "2026-11-01", to: "2026-11-01" });
  // A half-open range (one end only) completes rather than restarts.
  assert.deepEqual(selectDay({ from: "2026-11-12", to: null }, "2026-11-15"), { from: "2026-11-12", to: "2026-11-15" });
});

test("toInstants makes to exclusive by one day and keeps null", () => {
  const instants = toInstants({ from: "2026-11-12", to: "2026-11-12" });
  assert.equal(instants.from, new Date(2026, 10, 12, 0, 0, 0, 0).toISOString());
  assert.equal(instants.to, new Date(2026, 10, 13, 0, 0, 0, 0).toISOString());
  assert.deepEqual(toInstants({ from: null, to: null }), { from: null, to: null });
  assert.equal(toInstants({ from: "2026-11-12", to: null }).to, null);
  assert.equal(toInstants({ from: null, to: "2026-11-12" }).from, null);
  assert.equal(toInstants({ from: null, to: "2026-11-12" }).to, new Date(2026, 10, 13).toISOString());
});

test("toInstants at a month boundary (31 Oct to 1 Nov) spans the right instants", () => {
  const original = process.env.TZ;
  try {
    // Kuala Lumpur is UTC+8 all year.
    process.env.TZ = "Asia/Kuala_Lumpur";
    assert.deepEqual(toInstants({ from: "2026-10-31", to: "2026-11-01" }), {
      from: "2026-10-30T16:00:00.000Z",
      to: "2026-11-01T16:00:00.000Z",
    });
    // New York ends daylight time on 1 Nov 2026: 31 Oct starts at 04:00Z, 2 Nov at 05:00Z.
    process.env.TZ = "America/New_York";
    assert.deepEqual(toInstants({ from: "2026-10-31", to: "2026-11-01" }), {
      from: "2026-10-31T04:00:00.000Z",
      to: "2026-11-02T05:00:00.000Z",
    });
    // Year end: the exclusive end is the next 1 Jan.
    process.env.TZ = "UTC";
    assert.equal(toInstants({ from: "2026-12-31", to: "2026-12-31" }).to, "2027-01-01T00:00:00.000Z");
  } finally {
    if (original === undefined) delete process.env.TZ;
    else process.env.TZ = original;
  }
});

test("describeRange wording", () => {
  assert.equal(describeRange({ from: null, to: null }), "Any date");
  assert.equal(describeRange({ from: "2026-11-12", to: "2026-11-12" }), "12 Nov 2026");
  assert.equal(describeRange({ from: "2026-11-12", to: "2026-11-18" }), "12 Nov to 18 Nov 2026");
  assert.equal(describeRange({ from: "2026-12-28", to: "2027-01-03" }), "28 Dec 2026 to 3 Jan 2027");
  assert.equal(describeRange({ from: "2026-11-12", to: null }), "12 Nov 2026");
  assert.equal(describeRange({ from: "nonsense", to: null }), "Any date");
});

test("day arithmetic and parsing", () => {
  assert.equal(addDays("2026-10-31", 1), "2026-11-01");
  assert.equal(addDays("2026-03-01", -1), "2026-02-28");
  assert.equal(addDays("2028-03-01", -1), "2028-02-29");
  assert.equal(addMonths("2026-01-31", 1), "2026-02-28");
  assert.equal(addMonths("2028-01-31", 1), "2028-02-29");
  assert.equal(addMonths("2026-01-15", -1), "2025-12-15");
  assert.equal(addMonths("2026-12-15", 1), "2027-01-15");
  assert.equal(parseDay("2026-02-30"), null);
  assert.equal(parseDay("12 Nov 2026"), null);
  assert.deepEqual(parseDay("2026-11-02"), { year: 2026, month: 11, day: 2 });
  assert.equal(todayLocal(new Date(2026, 10, 2, 23, 59)), "2026-11-02");
});
