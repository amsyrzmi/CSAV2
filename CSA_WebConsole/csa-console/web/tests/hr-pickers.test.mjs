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
const { personLabel, loadPeopleOptions, memberOptions, isDenied, scheduledOptions, payrollRequestOptions, shortWhen } =
  await import(moduleUrl(await readFile(new URL("../lib/hr-pickers.ts", import.meta.url), "utf8")));

const person = (id, name, title = null) => ({ employee_id: id, display_name: name, job_title: title });
const refusal = (status) => Object.assign(new Error("resource_unavailable"), { status });

test("personLabel is the name, then the title when there is one", () => {
  assert.equal(personLabel(person("1", "Aina Rahman", "Engineer")), "Aina Rahman · Engineer");
  assert.equal(personLabel(person("1", "Aina Rahman")), "Aina Rahman");
  assert.equal(personLabel(person("1", "Aina Rahman", "")), "Aina Rahman");
});

test("loadPeopleOptions follows next_cursor until it is null", async () => {
  const cursors = [];
  const pages = {
    "": { items: [person("a", "A", "Lead")], next_cursor: "50" },
    "50": { items: [person("b", "B")], next_cursor: "100" },
    "100": { items: [], next_cursor: null },
  };
  const result = await loadPeopleOptions(async (cursor) => { cursors.push(cursor); return pages[cursor ?? ""]; });
  assert.deepEqual(cursors, [undefined, "50", "100"]);
  assert.deepEqual(result, { options: [["a", "A · Lead"], ["b", "B"]], denied: false });
});

test("loadPeopleOptions stops after twenty pages", async () => {
  let calls = 0;
  const result = await loadPeopleOptions(async () => {
    calls += 1;
    return { items: [person(String(calls), `P${calls}`)], next_cursor: String(calls * 50) };
  });
  assert.equal(calls, 20);
  assert.equal(result.options.length, 20);
  assert.equal(result.denied, false);
});

test("loadPeopleOptions reports a 404 as denied and rethrows anything else", async () => {
  assert.deepEqual(await loadPeopleOptions(async () => { throw refusal(404); }), { options: [], denied: true });
  await assert.rejects(loadPeopleOptions(async () => { throw refusal(503); }), /resource_unavailable/);
  await assert.rejects(loadPeopleOptions(async () => { throw new TypeError("offline"); }), /offline/);
});

test("isDenied is true only for a 404", () => {
  assert.equal(isDenied(refusal(404)), true);
  for (const other of [refusal(403), refusal(409), new Error("x"), null, undefined, "404"]) assert.equal(isDenied(other), false);
});

test("memberOptions falls back to the user id when there is no display name", () => {
  assert.deepEqual(memberOptions([{ user_id: "u1", display_name: "Siti" }, { user_id: "u2", display_name: null }]),
    [["u1", "Siti"], ["u2", "u2"]]);
});

test("scheduledOptions keeps scheduled items only and labels them with a name and the start", () => {
  const items = [
    { id: "s1", employee_id: "e1", starts_at: "2026-11-02T01:00:00Z", status: "scheduled" },
    { id: "s2", employee_id: "e2", starts_at: "2026-11-03T01:00:00Z", status: "cancelled" },
    { id: "s3", employee_id: "e3", starts_at: "2026-11-04T01:00:00Z", status: "scheduled" },
  ];
  const names = new Map([["e1", "Aina"]]);
  const when = (iso) => `at ${iso}`;
  assert.deepEqual(scheduledOptions(items, (item) => names.get(item.employee_id) ?? item.employee_id, when), [
    ["s1", "Aina · at 2026-11-02T01:00:00Z"],
    ["s3", "e3 · at 2026-11-04T01:00:00Z"],
  ]);
});

test("payrollRequestOptions keeps payroll requests and labels them with the period and status", () => {
  const items = [
    { id: "p1", domain: "payroll", status: "pending", content: { starts_on: "2026-10-01", ends_on: "2026-10-31" } },
    { id: "l1", domain: "leave", status: "pending", content: { starts_on: "2026-10-01", ends_on: "2026-10-02" } },
    { id: "p2", domain: "payroll", status: "approved", content: { starts_on: null, ends_on: null } },
  ];
  assert.deepEqual(payrollRequestOptions(items), [
    ["p1", "2026-10-01 to 2026-10-31 · pending"],
    ["p2", "p2 · approved"],
  ]);
});

test("shortWhen writes day, short month, year and 24-hour Malaysia time", () => {
  assert.equal(shortWhen("2026-10-27T01:15:00Z"), "27 Oct 2026, 09:15");
  assert.equal(shortWhen("2026-11-02T14:05:00Z"), "2 Nov 2026, 22:05");
});
