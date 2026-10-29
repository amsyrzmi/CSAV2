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
const read = (file) => readFile(new URL(file, import.meta.url), "utf8");
const { navigationFor } = await import(moduleUrl(await read("../lib/navigation.ts")));
const { RAIL_COOKIE, parseRail, railCookie, NAV_ICONS } = await import(
  moduleUrl(await read("../lib/shell-state.ts"))
);

test("parseRail returns the exact valid values", () => {
  assert.equal(RAIL_COOKIE, "fs-rail");
  assert.equal(parseRail("collapsed"), "collapsed");
  assert.equal(parseRail("expanded"), "expanded");
});

test("parseRail defaults to collapsed for anything else", () => {
  for (const raw of [
    null, undefined, "", "EXPANDED", "Expanded", " expanded", "expanded ", "expanded; Path=/",
    "<script>", "expanded\r\nSet-Cookie: a=b", "constructor", "__proto__", "toString", "0",
  ]) {
    assert.equal(parseRail(raw), "collapsed", JSON.stringify(raw));
  }
});

test("railCookie writes a year-long, same-site cookie holding only a validated value", () => {
  assert.equal(railCookie("expanded"), "fs-rail=expanded; Path=/; Max-Age=31536000; SameSite=Lax");
  assert.equal(railCookie("collapsed"), "fs-rail=collapsed; Path=/; Max-Age=31536000; SameSite=Lax");
  assert.equal(railCookie("<script>"), "fs-rail=collapsed; Path=/; Max-Age=31536000; SameSite=Lax");
});

test("NAV_ICONS covers every key navigationFor can output, each with its own icon", () => {
  const user = { email: "a@example.test", display_name: "A", must_change_password: false };
  const packages = ["customer_support", "orders_products", "hr", "staff_email", "subscriptions"]
    .map((key) => ({ key, status: "active" }));
  const keys = new Set();
  for (const role of ["platform_admin", "tenant_admin", "tenant_staff", "read_only"]) {
    const me = { role, user, memberships: [], active_client_id: "t", csrf_token: "" };
    for (const link of navigationFor(me, packages)) keys.add(link.key);
  }
  assert.ok(keys.size >= 14, `expected every entry to appear, saw ${[...keys]}`);
  for (const key of keys) assert.ok(NAV_ICONS[key], `no icon for ${key}`);
  const names = Object.values(NAV_ICONS);
  assert.equal(new Set(names).size, names.length, "two keys share an icon");
});
