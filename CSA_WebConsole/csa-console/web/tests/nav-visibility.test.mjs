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
const { navigationFor, businessPages, knowledgePages, currentChild, entryForPath, groupParentCurrent } = await import(moduleUrl(await readFile(new URL("../lib/navigation.ts", import.meta.url), "utf8")));

const staff = { role: "tenant_staff", user: { email: "s@example.test", display_name: "S", must_change_password: false }, memberships: [], active_client_id: "t", csrf_token: "" };
const active = (...keys) => keys.map((key) => ({ key, status: "active" }));
const labels = (me, packages) => navigationFor(me, packages).map((l) => l.label);

test("tenant member sees only package-active tabs; platform admin sees all", () => {
  const none = labels(staff, []);
  assert.deepEqual(none.filter((l) => ["Customer inbox", "Business knowledge", "Business management", "People & HR", "Email assistant"].includes(l)), []);
  const some = labels(staff, active("customer_support", "hr"));
  assert.ok(some.includes("Customer inbox") && some.includes("Business knowledge") && some.includes("People & HR"));
  assert.ok(!some.includes("Business management") && !some.includes("Email assistant"));
  assert.ok(some.includes("Services") && !some.includes("Agents"));
  const admin = labels({ ...staff, role: "platform_admin" }, []);
  for (const l of ["Customer inbox", "Business knowledge", "Business management", "People & HR", "Email assistant", "Services"]) assert.ok(admin.includes(l), l);
  const reader = labels({ ...staff, role: "read_only" }, active("customer_support", "staff_email"));
  assert.ok(reader.includes("Business knowledge") && !reader.includes("Customer inbox") && !reader.includes("Email assistant"));
});

test("business accepts either package, inactive statuses hide, hrefs follow the portal pattern", () => {
  assert.ok(labels(staff, active("marketplaces")).includes("Business management"));
  assert.ok(labels(staff, active("orders_products")).includes("Business management"));
  assert.ok(!labels(staff, [{ key: "hr", status: "suspended" }]).includes("People & HR"));
  const suspended = labels(staff, [{ key: "customer_support", status: "suspended" }]);
  assert.ok(suspended.includes("Customer inbox"), "suspended Customer Support keeps the Inbox link");
  assert.ok(!suspended.includes("Business knowledge"), "but the Business knowledge stays locked");
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

test("Password opens the in-shell page: /password for platform admins, /portal/password for tenant roles", () => {
  const admin = navigationFor({ ...staff, role: "platform_admin" }, []);
  assert.equal(admin.find((l) => l.key === "password")?.href, "/password");
  for (const role of ["tenant_admin", "tenant_staff", "read_only"]) {
    const link = navigationFor({ ...staff, role }, []).find((l) => l.key === "password");
    assert.equal(link?.label, "Password", role);
    assert.equal(link?.href, "/portal/password", role);
  }
});

test("the Sandbox assistant entry is gone for every role: Overview is the assistant home", () => {
  const all = active("customer_support", "hr", "staff_email", "orders_products", "subscriptions");
  for (const role of ["platform_admin", "tenant_admin", "tenant_staff", "read_only"]) {
    const links = navigationFor({ ...staff, role }, all);
    assert.ok(!links.some((l) => l.label === "Sandbox assistant" || l.key === "chat"), role);
    assert.ok(!links.some((l) => l.href.endsWith("/chat")), role);
    assert.equal(links[0].key, "overview", role);
    assert.equal(links[0].href, role === "platform_admin" ? "/" : "/portal", role);
  }
});

test("business and knowledge entries carry their sub-pages in order", () => {
  const all = active("orders_products", "customer_support");
  const brief = (items) => items.map(({ key, label, href, testId }) => [key, label, href, testId]);
  const portal = navigationFor(staff, all);
  const admin = navigationFor({ ...staff, role: "platform_admin" }, []);
  for (const [links, base, kb] of [[portal, "/portal/business", "/portal/faq"], [admin, "/business", "/knowledge-base"]]) {
    assert.deepEqual(brief(links.find((l) => l.key === "business").children), [
      ["orders", "Orders", base, "business-subnav-orders"],
      ["products", "Products & stock", `${base}/products`, "business-subnav-products"],
      ["stores", "Store connections", `${base}/stores`, "business-subnav-stores"],
    ]);
    assert.deepEqual(brief(links.find((l) => l.key === "knowledge_base").children), [
      ["library", "What it knows", kb, "kb-subnav-library"],
      ["add", "Add knowledge", `${kb}/add`, "kb-subnav-add"],
      ["questions", "Unanswered questions", `${kb}/questions`, "kb-subnav-questions"],
      ["maintenance", "Maintenance and settings", `${kb}/maintenance`, "kb-subnav-maintenance"],
    ]);
  }
  assert.deepEqual(businessPages(false).map((c) => c.href), portal.find((l) => l.key === "business").children.map((c) => c.href));
  assert.equal(knowledgePages(true)[0].href, "/knowledge-base");
  // The parent stays the first sub-page, so old links and bookmarks still land there.
  assert.equal(portal.find((l) => l.key === "business").href, "/portal/business");
  assert.equal(portal.find((l) => l.key === "knowledge_base").href, "/portal/faq");
  assert.equal(portal.find((l) => l.key === "services").children, undefined);
});

test("children never add a top-level key", () => {
  const all = active("orders_products", "customer_support", "hr", "staff_email", "subscriptions");
  const keys = new Set();
  for (const role of ["platform_admin", "tenant_admin", "tenant_staff", "read_only"])
    for (const l of navigationFor({ ...staff, role }, all)) keys.add(l.key);
  assert.equal(keys.size, 13);
});

test("child literals are href-first so the contract regex never reads them", async () => {
  const source = await readFile(new URL("../lib/navigation.ts", import.meta.url), "utf8");
  const keys = [...source.matchAll(/\{ key: "(\w+)", label: "([^"]+)"/g)].map((m) => m[1]).sort();
  assert.deepEqual(keys, [
    "activity", "business", "email", "hr", "inbox", "knowledge_base", "members", "overview",
    "password", "services", "subscriptions", "team", "tenants",
  ]);
});

test("currentChild picks the exact page, then the deepest sub-page, and the parent href matches exactly only", () => {
  const kids = businessPages(false);
  const name = (path) => currentChild(kids, path, "/portal/business")?.key ?? null;
  assert.equal(name("/portal/business"), "orders");
  assert.equal(name("/portal/business/products"), "products");
  assert.equal(name("/portal/business/products/x"), "products");
  assert.equal(name("/portal/business/stores"), "stores");
  assert.equal(name("/portal/business/other"), null, "the parent's own page never claims a deeper path");
  assert.equal(name("/portal/businessx"), null);
  assert.equal(name("/portal"), null);
  // A child href with a query compares by pathname only (HR children carry ?client_id=).
  const hr = [
    { href: "/hr?client_id=t", key: "hr_home", label: "Home" },
    { href: "/hr/people?client_id=t", key: "hr_people", label: "People" },
  ];
  assert.equal(currentChild(hr, "/hr", "/hr")?.key, "hr_home");
  assert.equal(currentChild(hr, "/hr/people/abc", "/hr")?.key, "hr_people");
  assert.equal(currentChild(hr, "/hr/unknown", "/hr"), null);
});

test("entryForPath maps sub-pages to their group and never lets home claim a page", () => {
  const links = navigationFor(staff, active("orders_products", "customer_support", "hr"));
  const key = (path) => entryForPath(links, path)?.key ?? null;
  assert.equal(key("/portal"), "overview");
  assert.equal(key("/portal/business"), "business");
  assert.equal(key("/portal/business/stores"), "business");
  assert.equal(key("/portal/faq/add"), "knowledge_base");
  assert.equal(key("/portal/hr/people/abc"), "hr");
  assert.equal(key("/portal/chat"), null);
  assert.equal(key("/portal/businessx"), null);
  assert.equal(key("/"), null);
  const admin = navigationFor({ ...staff, role: "platform_admin" }, []);
  assert.equal(entryForPath(admin, "/")?.key, "overview");
  assert.equal(entryForPath(admin, "/knowledge-base/questions")?.key, "knowledge_base");
  assert.equal(entryForPath(admin, "/anything")?.key ?? null, null);
});

test("a group's parent link is current on its own page, in the group when hidden, and never while shown", () => {
  const links = navigationFor(staff, active("orders_products"));
  const business = links.find((l) => l.key === "business");
  // The group is shown: the child carries the mark, the parent none.
  assert.equal(groupParentCurrent(business, "/portal/business", true), undefined);
  assert.equal(groupParentCurrent(business, "/portal/business/products", true), undefined);
  // Hidden on the parent's own page: the parent is the current page.
  assert.equal(groupParentCurrent(business, "/portal/business", false), "page");
  // Hidden on a sub-page: "true", never "page", so a nav never has two current links.
  assert.equal(groupParentCurrent(business, "/portal/business/products", false), "true");
  assert.equal(groupParentCurrent(business, "/portal/business/stores", false), "true");
  // Elsewhere, and a path that only starts with the same letters: nothing.
  assert.equal(groupParentCurrent(business, "/portal/services", false), undefined);
  assert.equal(groupParentCurrent(business, "/portal/businessx", false), undefined);
  // A link without sub-pages is current on its own page only.
  const services = links.find((l) => l.key === "services");
  assert.equal(groupParentCurrent(services, "/portal/services", false), "page");
  assert.equal(groupParentCurrent(services, "/portal/services/x", false), undefined);
});
