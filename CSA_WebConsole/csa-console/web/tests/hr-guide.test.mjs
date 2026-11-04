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
const { addHolidayDate, localToday, HOLIDAY_LIST_MAX } =
  await import(moduleUrl(await readFile(new URL("../lib/hr-guide.ts", import.meta.url), "utf8")));

test("localToday uses the local calendar day, zero padded", () => {
  assert.equal(localToday(new Date(2026, 0, 5, 23, 59)), "2026-01-05");
  assert.equal(localToday(new Date(2026, 10, 30, 0, 1)), "2026-11-30");
});

test("addHolidayDate appends to the comma list in the format Settings saves", () => {
  assert.deepEqual(addHolidayDate("", "2026-02-01"), { list: "2026-02-01" });
  assert.deepEqual(addHolidayDate("2026-01-01", "2026-02-01"), { list: "2026-01-01, 2026-02-01" });
  assert.deepEqual(addHolidayDate("2026-01-01,  2026-01-02 , ", "2026-02-01"), { list: "2026-01-01, 2026-01-02, 2026-02-01" });
});

test("addHolidayDate says why it did not add", () => {
  assert.match(addHolidayDate("", "").error, /Choose a date/);
  assert.match(addHolidayDate("2026-01-01", "2026-01-01").error, /already in the list/);
  const full = Array.from({ length: 16 }, (_, i) => `2026-01-${String(i + 1).padStart(2, "0")}`).join(", ");
  assert.ok(full.length <= HOLIDAY_LIST_MAX);
  assert.match(addHolidayDate(full, "2026-02-01").error, /list is full/);
});

test("the list that is saved still parses to the same dates", () => {
  const { list } = addHolidayDate("2026-01-01, 2026-01-02", "2026-03-04");
  assert.deepEqual(list.split(",").map((item) => item.trim()).filter(Boolean), ["2026-01-01", "2026-01-02", "2026-03-04"]);
});
