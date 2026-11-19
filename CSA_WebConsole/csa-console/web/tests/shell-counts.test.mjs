import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const source = await readFile(new URL("../lib/shell-counts.ts", import.meta.url), "utf8");
const code = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { wantsShellCounts, parseUnread, parseNeedsUs, nextCount } = await import(
  `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`
);

test("the production source holds no runtime alias imports", () => {
  assert.equal(/\bfrom\s+["']@\//.test(source), false);
});

test("a platform admin or a person without a role reads nothing", () => {
  for (const link of [true, false]) {
    assert.deepEqual(wantsShellCounts("platform_admin", link), { notifications: false, queue: false });
    assert.deepEqual(wantsShellCounts(null, link), { notifications: false, queue: false });
  }
});

test("every company member reads the notification count, the queue only with an Inbox link", () => {
  for (const role of ["tenant_admin", "tenant_staff", "read_only"]) {
    assert.deepEqual(wantsShellCounts(role, false), { notifications: true, queue: false });
    assert.deepEqual(wantsShellCounts(role, true), { notifications: true, queue: true });
  }
});

test("parseUnread reads a whole number of at least 0 and nothing else", () => {
  assert.equal(parseUnread({ unread: 0 }), 0);
  assert.equal(parseUnread({ unread: 7 }), 7);
  for (const body of [
    null,
    undefined,
    "3",
    3,
    [],
    {},
    { unread: -1 },
    { unread: 1.5 },
    { unread: "4" },
    { unread: null },
    { unread: Number.NaN },
    { unread: Number.POSITIVE_INFINITY },
    { needs_us: 4 },
  ]) {
    assert.equal(parseUnread(body), null, JSON.stringify(body));
  }
});

test("parseNeedsUs reads needs_us the same way", () => {
  assert.equal(parseNeedsUs({ needs_us: 0, mine: 2 }), 0);
  assert.equal(parseNeedsUs({ needs_us: 12 }), 12);
  for (const body of [
    null,
    "12",
    [],
    {},
    { needs_us: -3 },
    { needs_us: 2.5 },
    { needs_us: "2" },
    { needs_us: null },
    { unread: 2 },
  ]) {
    assert.equal(parseNeedsUs(body), null, JSON.stringify(body));
  }
});

test("nextCount keeps the previous answer when the read failed", () => {
  assert.equal(nextCount(5, null), 5);
  assert.equal(nextCount(null, null), null);
  assert.equal(nextCount(5, 0), 0);
  assert.equal(nextCount(null, 3), 3);
  assert.equal(nextCount(2, 9), 9);
});
