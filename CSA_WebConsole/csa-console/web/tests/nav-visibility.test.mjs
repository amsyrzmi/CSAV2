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
const { navigationFor } = await import(moduleUrl(await readFile(new URL("../lib/navigation.ts", import.meta.url), "utf8")));

const staff = { role: "tenant_staff", user: { email: "s@example.test", display_name: "S", must_change_password: false }, memberships: [], active_client_id: "t", csrf_token: "" };
const active = (...keys) => keys.map((key) => ({ key, status: "active" }));
const labels = (me, packages) => navigationFor(me, packages).map((l) => l.label);

test("tenant member sees only package-active tabs; platform admin sees all", () => {
  const none = labels(staff, []);
  assert.deepEqual(none.filter((l) => ["Customer inbox", "Knowledge base", "Business management", "People & HR", "Email assistant"].includes(l)), []);
  const some = labels(staff, active("customer_support", "hr"));
  assert.ok(some.includes("Customer inbox") && some.includes("Knowledge base") && some.includes("People & HR"));
  assert.ok(!some.includes("Business management") && !some.includes("Email assistant"));
  assert.ok(some.includes("Services") && !some.includes("Agents"));
  const admin = labels({ ...staff, role: "platform_admin" }, []);
  for (const l of ["Customer inbox", "Knowledge base", "Business management", "People & HR", "Email assistant", "Services"]) assert.ok(admin.includes(l), l);
  const reader = labels({ ...staff, role: "read_only" }, active("customer_support", "staff_email"));
  assert.ok(reader.includes("Knowledge base") && !reader.includes("Customer inbox") && !reader.includes("Email assistant"));
});

test("business accepts either package, inactive statuses hide, hrefs follow the portal pattern", () => {
  assert.ok(labels(staff, active("marketplaces")).includes("Business management"));
  assert.ok(labels(staff, active("orders_products")).includes("Business management"));
  assert.ok(!labels(staff, [{ key: "hr", status: "suspended" }]).includes("People & HR"));
  const suspended = labels(staff, [{ key: "customer_support", status: "suspended" }]);
  assert.ok(suspended.includes("Customer inbox"), "suspended Customer Support keeps the Inbox link");
  assert.ok(!suspended.includes("Knowledge base"), "but the Knowledge base stays locked");
  assert.ok(!labels(staff, [{ key: "customer_support", status: "requested" }]).includes("Customer inbox"));
  const portal = navigationFor(staff, active("staff_email"));
  assert.equal(portal.find((l) => l.label === "Services").href, "/portal/services");
  assert.equal(portal.find((l) => l.label === "Email assistant").href, "/portal/email");
  const admin = navigationFor({ ...staff, role: "platform_admin" }, []);
  assert.equal(admin.find((l) => l.label === "Services").href, "/services");
  assert.equal(admin.find((l) => l.label === "Overview").href, "/");
  assert.ok(admin.some((l) => l.label === "Tenants") && !portal.some((l) => l.label === "Tenants"));
});

test("the Staff workspace tab is gone for every role; the assistant dock replaces it", () => {
  const all = active("customer_support", "hr", "staff_email", "orders_products");
  for (const role of ["platform_admin", "tenant_admin", "tenant_staff", "read_only"]) {
    const links = navigationFor({ ...staff, role }, all);
    assert.ok(!links.some((l) => l.label === "Staff workspace" || l.key === "workspace"), role);
  }
});

test("Subscriptions follows its package for members and is always there for platform admins", () => {
  const member = navigationFor(staff, active("subscriptions", "customer_support"));
  const link = member.find((l) => l.label === "Subscriptions");
  assert.equal(link?.href, "/portal/subscriptions");
  assert.equal(link?.key, "subscriptions");
  const withBusiness = labels(staff, active("subscriptions", "orders_products"));
  assert.equal(withBusiness.indexOf("Subscriptions"), withBusiness.indexOf("Business management") + 1);
  assert.ok(labels({ ...staff, role: "read_only" }, active("subscriptions")).includes("Subscriptions"));
  for (const packages of [[], active("customer_support"), [{ key: "subscriptions", status: "suspended" }],
    [{ key: "subscriptions", status: "requested" }]]) {
    assert.ok(!labels(staff, packages).includes("Subscriptions"), JSON.stringify(packages));
  }
  const admin = navigationFor({ ...staff, role: "platform_admin" }, []);
  assert.equal(admin.find((l) => l.label === "Subscriptions")?.href, "/subscriptions");
});
