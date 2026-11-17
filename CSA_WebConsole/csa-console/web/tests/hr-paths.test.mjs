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
const { HR_TABS, parseHrPath, hrHref, legacyHrRoute, isHrPagePath, visibleTabs, navigateLandingPath } =
  await import(moduleUrl(await readFile(new URL("../lib/hr-paths.ts", import.meta.url), "utf8")));

const UUID = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d";

test("HR_TABS lists the nine tabs in order with their path segments", () => {
  assert.deepEqual(HR_TABS.map((item) => [item.tab, item.label, item.segment]), [
    ["home", "Home", null],
    ["people", "People", "people"],
    ["processes", "Processes", "processes"],
    ["recruitment", "Recruitment", "recruitment"],
    ["leave", "Leave & attendance", "leave"],
    ["payroll", "Payroll", "payroll"],
    ["training", "Training", "training"],
    ["requests", "Requests", "requests"],
    ["settings", "Settings", "settings"],
  ]);
});

test("parseHrPath accepts the tabs, people views and profile ids only", () => {
  assert.deepEqual(parseHrPath(undefined), { tab: "home" });
  assert.deepEqual(parseHrPath([]), { tab: "home" });
  assert.deepEqual(parseHrPath(["people"]), { tab: "people", view: "list" });
  assert.deepEqual(parseHrPath(["people", "org-chart"]), { tab: "people", view: "org-chart" });
  assert.deepEqual(parseHrPath(["people", "departments"]), { tab: "people", view: "departments" });
  assert.deepEqual(parseHrPath(["people", "new"]), { tab: "people", view: "new" });
  assert.deepEqual(parseHrPath(["people", UUID.toUpperCase()]), { tab: "people", view: "profile", employeeId: UUID });
  for (const segment of ["recruitment", "leave", "payroll", "training", "requests", "settings"])
    assert.deepEqual(parseHrPath([segment]), { tab: segment });
  assert.deepEqual(parseHrPath(["processes"]), { tab: "processes", view: "list" });
  for (const bad of [["onboarding"], ["home"], ["people", "nope"], ["people", "list"], ["people", UUID, "x"],
    ["leave", "x"], ["__proto__"], ["constructor"], [""], ["people", "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4"]])
    assert.equal(parseHrPath(bad), null, JSON.stringify(bad));
});

test("parseHrPath accepts the processes routes and rejects malformed ones", () => {
  assert.deepEqual(parseHrPath(["processes"]), { tab: "processes", view: "list" });
  assert.deepEqual(parseHrPath(["processes", "templates"]), { tab: "processes", view: "templates" });
  assert.deepEqual(parseHrPath(["processes", "templates", "new"]), { tab: "processes", view: "new-template" });
  assert.deepEqual(parseHrPath(["processes", UUID]), { tab: "processes", view: "detail", processId: UUID });
  assert.deepEqual(parseHrPath(["processes", UUID.toUpperCase()]), { tab: "processes", view: "detail", processId: UUID });
  assert.deepEqual(parseHrPath(["processes", "templates", UUID]), { tab: "processes", view: "template", templateId: UUID });
  assert.deepEqual(parseHrPath(["processes", "templates", UUID.toUpperCase()]), { tab: "processes", view: "template", templateId: UUID });
  for (const bad of [["processes", "nope"], ["processes", "list"], ["processes", "template"], ["processes", UUID, "x"],
    ["processes", "templates", "nope"], ["processes", "templates", UUID, "x"], ["processes", "templates", "templates"],
    ["processes", "templates", "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4"], ["processes", "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4dd"],
    ["processes", "__proto__"], ["processes", "constructor"], ["processes", ""], ["processes", "templates", ""],
    ["processes", UUID, UUID], ["Processes"], ["process"], ["processes", "new"], ["processes", "templates", "new", "x"],
    ["processes", "templates", "NEW"], ["processes", "templates", "new", UUID], ["processes", "new", "templates"]])
    assert.equal(parseHrPath(bad), null, JSON.stringify(bad));
});

test("hrHref keeps the platform tenant and encodes it", () => {
  assert.equal(hrHref({ tab: "people", view: "list" }, true, "a b/1"), "/hr/people?client_id=a+b%2F1");
  assert.equal(hrHref({ tab: "home" }, false, "t"), "/portal/hr");
  assert.equal(hrHref({ tab: "home" }, true, "t"), "/hr?client_id=t");
  assert.equal(hrHref({ tab: "leave" }, false, null), "/portal/hr/leave");
  assert.equal(hrHref({ tab: "people", view: "org-chart" }, false, "t"), "/portal/hr/people/org-chart");
  assert.equal(hrHref({ tab: "people", view: "profile", employeeId: UUID }, true, "t"), `/hr/people/${UUID}?client_id=t`);
  assert.equal(hrHref({ tab: "people", view: "new" }, true, null), "/hr/people/new");
  assert.equal(hrHref({ tab: "processes", view: "list" }, false, null), "/portal/hr/processes");
  assert.equal(hrHref({ tab: "processes", view: "templates" }, true, "t"), "/hr/processes/templates?client_id=t");
  assert.equal(hrHref({ tab: "processes", view: "detail", processId: UUID }, false, "t"), `/portal/hr/processes/${UUID}`);
  assert.equal(hrHref({ tab: "processes", view: "new-template" }, false, null), "/portal/hr/processes/templates/new");
  assert.equal(hrHref({ tab: "processes", view: "new-template" }, true, "t"), "/hr/processes/templates/new?client_id=t");
  assert.equal(hrHref({ tab: "processes", view: "template", templateId: UUID }, true, "t"),
    `/hr/processes/templates/${UUID}?client_id=t`);
});

test("every route hrHref builds parses back to the same route", () => {
  const routes = [
    { tab: "home" }, { tab: "recruitment" }, { tab: "leave" }, { tab: "payroll" },
    { tab: "training" }, { tab: "requests" }, { tab: "settings" },
    { tab: "people", view: "list" }, { tab: "people", view: "org-chart" },
    { tab: "people", view: "departments" }, { tab: "people", view: "new" },
    { tab: "people", view: "profile", employeeId: UUID },
    { tab: "processes", view: "list" }, { tab: "processes", view: "templates" },
    { tab: "processes", view: "detail", processId: UUID },
    { tab: "processes", view: "template", templateId: UUID },
    { tab: "processes", view: "new-template" },
  ];
  for (const route of routes) {
    const href = hrHref(route, false, null);
    const segments = href.replace("/portal/hr", "").split("/").filter(Boolean);
    assert.deepEqual(parseHrPath(segments), route, href);
  }
});

test("legacy sections land on their tab (Review Focus 3)", () => {
  assert.deepEqual(legacyHrRoute("directory"), { tab: "people", view: "list" });
  assert.deepEqual(legacyHrRoute("people"), { tab: "people", view: "list" });
  assert.deepEqual(legacyHrRoute("training"), { tab: "training" });
  assert.deepEqual(legacyHrRoute("workflows"), { tab: "requests" });
  assert.deepEqual(legacyHrRoute("requests"), { tab: "requests" });
  assert.deepEqual(legacyHrRoute("attendance"), { tab: "leave" });
  assert.deepEqual(legacyHrRoute("leave"), { tab: "leave" });
  for (const same of ["recruitment", "payroll", "settings"]) assert.deepEqual(legacyHrRoute(same), { tab: same });
  for (const bad of [null, "", "__proto__", "constructor", "onboarding", "Directory", "https://example.test"])
    assert.equal(legacyHrRoute(bad), null, String(bad));
});

test("a platform admin keeps the tenant when a legacy link is redirected", () => {
  const route = legacyHrRoute("training");
  assert.equal(hrHref(route, true, "tenant /one"), "/hr/training?client_id=tenant+%2Fone");
});

test("isHrPagePath accepts exactly what parseHrPath accepts under /hr and /portal/hr", () => {
  for (const good of ["/hr", "/portal/hr", "/hr/people", "/portal/hr/people/org-chart", `/portal/hr/people/${UUID}`,
    `/hr/people/${UUID.toUpperCase()}`, "/hr/settings", "/portal/hr/leave", "/hr/processes", "/portal/hr/processes/templates",
    `/portal/hr/processes/${UUID}`, `/hr/processes/templates/${UUID}`, "/hr/processes/templates/new",
    "/portal/hr/processes/templates/new"])
    assert.equal(isHrPagePath(good), true, good);
  for (const bad of ["/portal/hrx", "/hrx", "/hr/onboarding", "/portal/hr/people/nope", `/hr/people/${UUID}/x`,
    "/hr/", "/portal/hr//people", "/hr/processes/nope", `/hr/processes/templates/${UUID}/x`, "/hr/processes/templates/new/x", "/hr/processes/new", "/portal/hr/processes/templates/", "/portal", "/", "/hr/__proto__", "/portal/hr/leave/x", "/admin/hr", "hr/people"])
    assert.equal(isHrPagePath(bad), false, bad);
});

const STAFF = {
  employee: { tenant_id: "t", employee_id: "e" },
  client_owner: false,
  private_areas: [],
  actions: ["directory.read", "training.read", "training.draft", "training.submit", "training.withdraw"],
  switches: ["directory", "own_requests"],
  workflows: ["leave.read", "leave.prepare"],
  hr_duties: false,
};

test("visibleTabs hides tabs with nothing to do", () => {
  assert.deepEqual(visibleTabs(STAFF), ["home", "people", "leave", "training", "requests"]);
  assert.deepEqual(visibleTabs({ ...STAFF, client_owner: true }),
    ["home", "people", "processes", "leave", "training", "requests", "settings"]);
  assert.deepEqual(visibleTabs({ ...STAFF, private_areas: ["payroll"] }),
    ["home", "people", "leave", "payroll", "training", "requests"]);
  assert.deepEqual(visibleTabs({ ...STAFF, private_areas: ["recruitment"] }),
    ["home", "people", "recruitment", "leave", "training", "requests"]);
  assert.deepEqual(visibleTabs({ ...STAFF, private_areas: ["attendance"], workflows: [] }),
    ["home", "people", "leave", "training"]);
});

test("visibleTabs follows the switches and workflows", () => {
  const bare = { ...STAFF, actions: [], workflows: [], switches: [] };
  assert.deepEqual(visibleTabs(bare), ["home"]);
  assert.deepEqual(visibleTabs({ ...bare, switches: ["manage_people"] }), ["home", "people", "processes"]);
  assert.deepEqual(visibleTabs({ ...bare, switches: ["sensitive_details"] }), ["home", "people"]);
  assert.deepEqual(visibleTabs({ ...bare, client_owner: true }), ["home", "people", "processes", "leave", "settings"]);
  assert.deepEqual(visibleTabs({ ...bare, workflows: ["recruitment.read"] }), ["home", "recruitment", "requests"]);
  assert.deepEqual(visibleTabs({ ...bare, workflows: ["attendance.prepare"] }), ["home", "leave", "requests"]);
  assert.deepEqual(visibleTabs({ ...bare, workflows: ["payroll.prepare"] }), ["home", "requests"]);
  assert.deepEqual(visibleTabs({ ...bare, actions: ["training.decide"] }), ["home", "training"]);
  assert.deepEqual(visibleTabs({ ...bare, actions: ["directory.read"] }), ["home"]);
});

test("visibleTabs shows Processes only to the HR owner and Manage people holders", () => {
  const bare = { ...STAFF, actions: [], workflows: [], switches: [] };
  const shown = (ctx) => visibleTabs(ctx).includes("processes");
  assert.equal(shown({ ...bare, client_owner: true }), true);
  assert.equal(shown({ ...bare, switches: ["manage_people"] }), true);
  assert.equal(shown({ ...bare, switches: ["directory", "own_requests", "manage_people"] }), true);
  // Every other switch, private area and workflow, together, still leaves it hidden.
  for (const switchId of ["directory", "own_requests", "approves_team", "hr_team", "sensitive_details"])
    assert.equal(shown({ ...bare, switches: [switchId] }), false, switchId);
  assert.equal(shown({ ...STAFF, private_areas: ["recruitment", "attendance", "payroll"], hr_duties: true,
    switches: ["directory", "own_requests", "approves_team", "hr_team", "sensitive_details"] }), false);
  assert.equal(shown(STAFF), false);
});

test("navigateLandingPath is where an HR navigate lands after the legacy redirect", () => {
  assert.equal(navigateLandingPath("/portal/hr?section=training"), "/portal/hr/training");
  assert.equal(navigateLandingPath("/portal/hr?section=directory"), "/portal/hr/people");
  assert.equal(navigateLandingPath("/portal/hr?section=workflows"), "/portal/hr/requests");
  assert.equal(navigateLandingPath("/hr?section=training&client_id=tenant-1"), "/hr/training");
  assert.equal(navigateLandingPath("/hr?client_id=tenant-1&section=attendance"), "/hr/leave");
});

test("navigateLandingPath keeps the HR home for no section or a section with no legacy mapping", () => {
  assert.equal(navigateLandingPath("/portal/hr"), "/portal/hr");
  assert.equal(navigateLandingPath("/portal/hr?section=agents"), "/portal/hr");
  assert.equal(navigateLandingPath("/hr?section=tools&client_id=t"), "/hr");
  assert.equal(navigateLandingPath("/hr?client_id=t"), "/hr");
});

test("navigateLandingPath leaves every other navigate as its path without the query", () => {
  assert.equal(navigateLandingPath("/portal/services?section=training"), "/portal/services");
  assert.equal(navigateLandingPath("/email?client_id=t"), "/email");
  assert.equal(navigateLandingPath("/portal"), "/portal");
  assert.equal(navigateLandingPath("/portal/hrx?section=training"), "/portal/hrx");
});
