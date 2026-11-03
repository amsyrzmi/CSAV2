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
const { HR_TABS, hrNavItems } =
  await import(moduleUrl(await readFile(new URL("../lib/hr-paths.ts", import.meta.url), "utf8")));

const ALL = HR_TABS.map((item) => item.tab);
const TENANT = "11111111-1111-4111-8111-111111111111";

test("hrNavItems keeps HR_TABS order and drops hidden tabs", () => {
  assert.deepEqual(hrNavItems(ALL, false, TENANT).map((item) => item.key), ALL.map((tab) => `hr_${tab}`));
  // The input order does not matter; the order is HR_TABS'.
  const shown = hrNavItems(["settings", "leave", "home"], false, TENANT);
  assert.deepEqual(shown.map((item) => [item.key, item.label, item.testId]), [
    ["hr_home", "Home", "hr-nav-home"],
    ["hr_leave", "Leave & attendance", "hr-nav-leave"],
    ["hr_settings", "Settings", "hr-nav-settings"],
  ]);
  assert.deepEqual(shown.map((item) => item.href), ["/portal/hr", "/portal/hr/leave", "/portal/hr/settings"]);
});

test("hrNavItems people links to the list", () => {
  const people = hrNavItems(["home", "people"], false, TENANT).find((item) => item.key === "hr_people");
  assert.equal(people.href, "/portal/hr/people");
  assert.equal(people.label, "People");
  assert.equal(people.testId, "hr-nav-people");
});

test("hrNavItems keeps the admin tenant in client_id", () => {
  const items = hrNavItems(ALL, true, TENANT);
  for (const item of items) assert.ok(item.href.endsWith(`?client_id=${TENANT}`), item.href);
  assert.deepEqual(items.slice(0, 2).map((item) => item.href), [
    `/hr?client_id=${TENANT}`,
    `/hr/people?client_id=${TENANT}`,
  ]);
  // Staff never carry it, and an admin with no tenant yet gets bare paths.
  assert.ok(hrNavItems(ALL, false, TENANT).every((item) => !item.href.includes("client_id")));
  assert.equal(hrNavItems(["home"], true, null)[0].href, "/hr");
});

test("hrNavItems with only home is one item", () => {
  assert.deepEqual(hrNavItems(["home"], false, TENANT), [
    { key: "hr_home", label: "Home", href: "/portal/hr", testId: "hr-nav-home" },
  ]);
  assert.deepEqual(hrNavItems([], false, TENANT), []);
});
