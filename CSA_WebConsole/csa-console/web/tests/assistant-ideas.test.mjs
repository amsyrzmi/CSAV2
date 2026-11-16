import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

const compile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const url = (javascript) => `data:text/javascript;base64,${Buffer.from(javascript).toString("base64")}`;
const source = await readFile(new URL("../lib/assistant-ideas.ts", import.meta.url), "utf8");
const { ideasFor } = await import(url(compile(source)));
const shipped = JSON.parse(await readFile(new URL("../lib/assistant-ideas.json", import.meta.url), "utf8"));

const both = ["tenant_admin", "tenant_staff"];
const idea = (id, tab, extra = {}) => ({
  id, tab, roles: both, packages: [], en: `${id} en`, ms: `${id} ms`, ...extra,
});
const pkg = (key, status = "active") => ({ key, status });
const ask = (ideas, opts = {}) => ideasFor(ideas, {
  tab: null, role: "tenant_staff", packages: [], language: "en", ...opts,
});
const ids = (list) => list.map((item) => item.id);

test("current-tab ideas come first, then any, each in file order", () => {
  const list = [idea("a1", "any"), idea("h1", "hr"), idea("a2", "any"), idea("h2", "hr"), idea("b1", "bookings")];
  assert.deepEqual(ids(ask(list, { tab: "hr", limit: 10 })), ["h1", "h2", "a1", "a2"]);
  assert.deepEqual(ids(ask(list, { tab: "overview", limit: 10 })), ["a1", "a2"]);
  assert.deepEqual(ids(ask(list, { tab: null, limit: 10 })), ["a1", "a2"]);
});

test("a read-only member and a missing role get no ideas", () => {
  const list = [idea("a1", "any")];
  assert.deepEqual(ask(list, { role: "read_only" }), []);
  assert.deepEqual(ask(list, { role: null }), []);
  assert.deepEqual(ids(ask(list, { role: "tenant_staff" })), ["a1"]);
});

test("an idea needs its roles; a platform admin counts as a tenant admin", () => {
  const list = [idea("adm", "any", { roles: ["tenant_admin"] }), idea("stf", "any", { roles: ["tenant_staff"] })];
  assert.deepEqual(ids(ask(list, { role: "tenant_staff" })), ["stf"]);
  assert.deepEqual(ids(ask(list, { role: "tenant_admin" })), ["adm"]);
  assert.deepEqual(ids(ask(list, { role: "platform_admin" })), ["adm"]);
});

test("every package an idea needs must be active", () => {
  const list = [
    idea("free", "any"),
    idea("hr", "any", { packages: ["hr"] }),
    idea("both", "any", { packages: ["hr", "bookings"] }),
  ];
  assert.deepEqual(ids(ask(list)), ["free"]);
  assert.deepEqual(ids(ask(list, { packages: [pkg("hr"), pkg("bookings", "suspended")] })), ["free", "hr"]);
  assert.deepEqual(ids(ask(list, { packages: [pkg("hr"), pkg("bookings")] })), ["free", "hr", "both"]);
  assert.deepEqual(ids(ask(list, { packages: [pkg("hr", "requested")] })), ["free"]);
});

test("the language picks the English or Malay text", () => {
  const list = [idea("a1", "any", { en: "Open Payments", ms: "Buka Pembayaran" })];
  assert.deepEqual(ask(list, { language: "en" }), [{ id: "a1", text: "Open Payments" }]);
  assert.deepEqual(ask(list, { language: "ms" }), [{ id: "a1", text: "Buka Pembayaran" }]);
});

test("limit is respected and defaults to four", () => {
  const list = ["a", "b", "c", "d", "e", "f"].map((id) => idea(id, "any"));
  assert.equal(ask(list).length, 4);
  assert.deepEqual(ids(ask(list, { limit: 2 })), ["a", "b"]);
  assert.equal(ask(list, { limit: 0 }).length, 0);
  assert.equal(ask(list, { limit: 99 }).length, 6);
});

test("the shipped list gives a staff member with the base packages at least four chips on Overview", () => {
  const base = ["customer_support", "staff_email", "hr", "bookings"].map((key) => pkg(key));
  for (const role of ["tenant_staff", "tenant_admin", "platform_admin"]) {
    assert.ok(ask(shipped, { tab: "overview", role, packages: base, language: "en" }).length >= 4, role);
    assert.ok(ask(shipped, { tab: "overview", role, packages: base, language: "ms" }).length >= 4, role);
  }
  assert.ok(ask(shipped, { tab: "overview", packages: [] }).length >= 1);
});
