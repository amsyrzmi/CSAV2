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
const { requestActions } =
  await import(moduleUrl(await readFile(new URL("../lib/hr-requests.ts", import.meta.url), "utf8")));

const ALL = ["recruitment", "attendance", "leave", "payroll"];

test("requestActions offers Prepare only for domains with a prepare grant, in the panel's order", () => {
  const actions = requestActions(["leave.prepare", "attendance.read", "recruitment.prepare", "attendance.prepare"], ALL);
  assert.deepEqual(actions.prepare, ["recruitment", "attendance", "leave"]);
  assert.equal(actions.decides("leave"), false);
});

test("requestActions offers approve and reject only for domains with a decide grant", () => {
  const actions = requestActions(["leave.decide", "attendance.read", "payroll.decide"], ALL);
  assert.deepEqual(actions.prepare, []);
  assert.equal(actions.decides("leave"), true);
  assert.equal(actions.decides("payroll"), true);
  assert.equal(actions.decides("attendance"), false);
  assert.equal(actions.decides("recruitment"), false);
});

test("requestActions stays inside the panel's domains", () => {
  const actions = requestActions(["leave.prepare", "leave.decide", "recruitment.read", "recruitment.prepare"], ["recruitment"]);
  assert.deepEqual(actions.prepare, ["recruitment"]);
  assert.equal(actions.decides("leave"), false);
  assert.equal(actions.decides("recruitment"), false);
});

test("requestActions matches whole grants, not prefixes", () => {
  const actions = requestActions(["leave.prepared", "leaves.prepare", "leave.decider", "payroll.decide.extra"], ALL);
  assert.deepEqual(actions.prepare, []);
  for (const domain of ALL) assert.equal(actions.decides(domain), false);
  assert.deepEqual(requestActions([], ALL).prepare, []);
});
