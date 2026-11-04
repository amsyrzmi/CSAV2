import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

function moduleUrl(javascript) {
  return `data:text/javascript;base64,${Buffer.from(javascript).toString("base64")}`;
}
const compile = (source) => ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
}).outputText;
const { navigationFor } = await import(moduleUrl(compile(await readFile(new URL("../lib/navigation.ts", import.meta.url), "utf8"))));
const source = await readFile(new URL("../lib/overview-home.ts", import.meta.url), "utf8");
const code = compile(source);
const { overviewBody, homeLinks } = await import(moduleUrl(code));

const pkg = (key, status = "active") => ({ key, status });

test("Customer support active or suspended keeps the chat", () => {
  assert.equal(overviewBody([pkg("customer_support")]), "chat");
  assert.equal(overviewBody([pkg("customer_support", "suspended"), pkg("hr")]), "chat");
});

test("a company without Customer support gets the small home", () => {
  assert.equal(overviewBody([]), "home");
  assert.equal(overviewBody([pkg("hr"), pkg("staff_email")]), "home");
  assert.equal(overviewBody([pkg("customer_support", "requested"), pkg("hr")]), "home");
  assert.equal(overviewBody([pkg("customer_support", "available")]), "home");
});

test("a failed read never removes the chat", () => {
  assert.equal(overviewBody(null), "chat");
});

const member = (role = "tenant_staff") => ({ role, user: {}, memberships: [], active_client_id: "t" });
const asMember = (packages, role) => homeLinks(navigationFor(member(role), packages), false, null);

test("the small home links to what the company has, in order, and always to Services", () => {
  const names = (packages, role) => asMember(packages, role).map((l) => l.label);
  assert.deepEqual(names([]), ["Services"]);
  assert.deepEqual(names([pkg("hr")]), ["People & HR", "Services"]);
  assert.deepEqual(names([pkg("staff_email"), pkg("hr")]), ["People & HR", "Email assistant", "Services"]);
  assert.deepEqual(names([pkg("hr", "suspended"), pkg("staff_email", "requested")]), ["Services"]);
  assert.deepEqual(names([pkg("staff_email")], "read_only"), ["Services"], "the rail's role rule applies");
  assert.deepEqual(asMember([pkg("hr")]).map((l) => l.href), ["/portal/hr", "/portal/services"]);
});

test("a company with only Bookings gets a link to Bookings, before Services", () => {
  const names = (packages, role) => asMember(packages, role).map((l) => l.label);
  assert.deepEqual(names([pkg("bookings")]), ["Bookings", "Services"]);
  assert.deepEqual(names([pkg("bookings"), pkg("hr")]), ["People & HR", "Bookings", "Services"]);
  assert.deepEqual(asMember([pkg("bookings")]).map((l) => l.href), ["/portal/bookings", "/portal/services"]);
  assert.deepEqual(names([pkg("bookings", "requested")]), ["Services"]);
});

test("a platform admin's links are the admin pages for the chosen company", () => {
  // What a member of that company would see, as the page computes it for a platform admin.
  const rail = navigationFor(member("tenant_admin"), [pkg("hr")]);
  assert.deepEqual(
    homeLinks(rail, true, "tenant a&b").map((l) => l.href),
    ["/hr?client_id=tenant%20a%26b", "/services?client_id=tenant%20a%26b"],
  );
  assert.deepEqual(homeLinks(navigationFor(member("tenant_admin"), []), true, "t").map((l) => l.label), ["Services"]);
});
