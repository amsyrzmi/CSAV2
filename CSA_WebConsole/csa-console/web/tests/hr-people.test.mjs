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
const people = await import(moduleUrl(await readFile(new URL("../lib/hr-people.ts", import.meta.url), "utf8")));
const {
  EMPLOYMENT_TYPES, STATUS_LABELS, NO_MANAGER, NEW_DEPARTMENT, PEOPLE_COPY,
  personBody, employmentProfileIssue, departmentBody, orgTree, homeItems, emptyText, historyText, peopleRefusalText,
  runsHomeMaintenance, peopleFilters, profileTabs, personFormValues, personFormIssue, managerChoices,
  departmentChoices, overviewRows, homeQuickActions, myBalanceLines, myTrainingLines,
} = people;

const ID = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d";
const DEPARTMENT = "44444444-4444-4444-8444-444444444444";
const MANAGER = "33333333-3333-4333-8333-333333333333";

test("the pinned labels and sentinels", () => {
  assert.deepEqual(EMPLOYMENT_TYPES, [
    ["full_time", "Full-time"], ["part_time", "Part-time"], ["contract", "Contract"], ["intern", "Intern"],
  ]);
  assert.deepEqual(STATUS_LABELS, { active: "Active", starting: "Starting", left: "Left" });
  assert.equal(NO_MANAGER, "");
  assert.equal(NEW_DEPARTMENT, "__new__");
  assert.equal(PEOPLE_COPY.noManager, "No manager (top of company)");
  assert.equal(PEOPLE_COPY.newDepartment, "New department…");
});

test("PEOPLE_COPY.stale is the pinned sentence", () => {
  assert.equal(PEOPLE_COPY.stale, "Someone changed this person since you opened it. Reload to see their change.");
});

const FORM = {
  display_name: "  Aina Rahman ", job_title: " Designer ", start_date: "2026-11-03",
  manager_id: MANAGER, department_id: DEPARTMENT, new_department: "",
  work_email: " aina@example.test ", work_phone: " +60 12-345 6789 ", employment_type: "full_time",
  region: "", legal_entity_reference: "", employment_category: "",
};

test("personBody trims, adds the operation and leaves the profile out when it is empty", () => {
  assert.deepEqual(personBody("add", FORM, { employeeId: ID }), {
    operation: "person.add",
    employee_id: ID,
    values: {
      display_name: "Aina Rahman", job_title: "Designer", start_date: "2026-11-03", manager_id: MANAGER,
      department_id: DEPARTMENT, work_email: "aina@example.test", work_phone: "+60 12-345 6789",
      employment_type: "full_time",
    },
  });
});

test("personBody update carries the record version", () => {
  const body = personBody("update", FORM, { employeeId: ID, version: 7 });
  assert.equal(body.operation, "person.update");
  assert.equal(body.expected_version, 7);
  assert.equal(body.employee_id, ID);
  assert.throws(() => personBody("update", FORM, { employeeId: ID }), /version/);
  assert.equal("expected_version" in personBody("add", FORM, { employeeId: ID, version: 7 }), false);
});

test("personBody turns empty optional fields into null and no manager into manager_id null", () => {
  const body = personBody("add", {
    ...FORM, job_title: "  ", start_date: "", manager_id: NO_MANAGER, department_id: "",
    work_email: "", work_phone: "   ", employment_type: "",
  }, { employeeId: ID });
  assert.deepEqual(body.values, {
    display_name: "Aina Rahman", job_title: null, start_date: null, manager_id: null,
    department_id: null, work_email: null, work_phone: null, employment_type: null,
  });
});

test("personBody sends a new department by name and drops the department id", () => {
  const body = personBody("add", { ...FORM, department_id: NEW_DEPARTMENT, new_department: "  Customer  care " }, { employeeId: ID });
  assert.equal(body.values.new_department, "Customer  care");
  assert.equal("department_id" in body.values, false);
  const blank = personBody("add", { ...FORM, department_id: NEW_DEPARTMENT, new_department: "  " }, { employeeId: ID });
  assert.equal(blank.values.new_department, null);
  assert.equal("department_id" in blank.values, false);
  assert.equal("new_department" in personBody("add", FORM, { employeeId: ID }).values, false);
});

test("personBody sends the employment profile whenever any of its fields has text", () => {
  const full = { region: "sabah", legal_entity_reference: " ENT-1 ", employment_category: " Permanent " };
  // All three filled: sent, trimmed.
  for (const mode of ["add", "update"])
    assert.deepEqual(personBody(mode, { ...FORM, ...full }, { employeeId: ID, version: 1 }).employment_profile,
      { region: "sabah", legal_entity_reference: "ENT-1", employment_category: "Permanent" }, mode);
  // Partly filled: still sent, so the server refuses it instead of the update wiping the other two.
  for (const missing of ["region", "legal_entity_reference", "employment_category"])
    assert.deepEqual(personBody("update", { ...FORM, ...full, [missing]: " " }, { employeeId: ID, version: 1 }).employment_profile,
      { region: "sabah", legal_entity_reference: "ENT-1", employment_category: "Permanent", [missing]: "" }, missing);
  assert.deepEqual(personBody("update", { ...FORM, region: "sabah" }, { employeeId: ID, version: 1 }).employment_profile,
    { region: "sabah", legal_entity_reference: "", employment_category: "" });
  // All blank: omitted, which clears the stored profile on update.
  for (const mode of ["add", "update"])
    assert.equal("employment_profile" in personBody(mode, FORM, { employeeId: ID, version: 1 }), false, mode);
  assert.equal("employment_profile" in personBody("update", { ...FORM, region: "  ", legal_entity_reference: " " }, { employeeId: ID, version: 1 }), false);
});

test("employmentProfileIssue flags only a partly filled profile", () => {
  const issue = (extra) => employmentProfileIssue({ ...FORM, ...extra });
  assert.equal(issue({}), null);
  assert.equal(issue({ region: "sabah", legal_entity_reference: "E", employment_category: "P" }), null);
  assert.equal(issue({ region: " " }), null);
  for (const extra of [{ region: "sabah" }, { legal_entity_reference: "E" }, { region: "sabah", employment_category: "P" }])
    assert.equal(issue(extra), "Fill in all three employment details, or leave all three empty.");
  assert.equal(peopleRefusalText("employment_profile_must_be_complete"), "Fill in all three employment details, or leave all three empty.");
  assert.match(peopleRefusalText("invalid_hr_input"), /all three employment details/);
});

test("departmentBody names the operation, trims and sends the version or null", () => {
  assert.deepEqual(departmentBody({ departmentId: DEPARTMENT, name: "  Finance ", archived: false, version: null }), {
    operation: "department.save", department_id: DEPARTMENT, expected_version: null, name: "Finance", archived: false,
  });
  assert.deepEqual(departmentBody({ departmentId: DEPARTMENT, name: "Finance", archived: true, version: 3 }), {
    operation: "department.save", department_id: DEPARTMENT, expected_version: 3, name: "Finance", archived: true,
  });
});

function node(id, name, manager = null) {
  return { employee_id: id, display_name: name, job_title: null, department: null, manager_id: manager };
}
const shape = (tree) => tree.map((item) => [item.display_name, shape(item.children)]);

test("orgTree nests people under their manager and orders by name", () => {
  const tree = orgTree({
    nodes: [node("c", "Chen", "a"), node("a", "Aina"), node("b", "Bala", "a"), node("d", "Dev", "c"), node("z", "Zul")],
    looped: [],
  });
  assert.deepEqual(shape(tree), [["Aina", [["Bala", []], ["Chen", [["Dev", []]]]]], ["Zul", []]]);
  assert.equal(tree[0].employee_id, "a");
});

test("orgTree puts a person whose manager is not in the chart at the top", () => {
  assert.deepEqual(shape(orgTree({ nodes: [node("a", "Aina", "gone")], looped: [] })), [["Aina", []]]);
  assert.deepEqual(orgTree({ nodes: [], looped: [] }), []);
});

test("orgTree puts looped people at the top and keeps everyone under them", () => {
  const tree = orgTree({
    nodes: [node("a", "Aina", "b"), node("b", "Bala", "a"), node("c", "Chen", "b"), node("d", "Dev")],
    looped: ["a", "b"],
  });
  assert.deepEqual(shape(tree), [["Aina", []], ["Bala", [["Chen", []]]], ["Dev", []]]);
});

test("orgTree never drops a person caught in a loop the server did not report", () => {
  const tree = orgTree({ nodes: [node("a", "Aina", "b"), node("b", "Bala", "a")], looped: [] });
  const names = JSON.stringify(shape(tree));
  assert.match(names, /Aina/);
  assert.match(names, /Bala/);
});

function person(id, name, extra = {}) {
  return { employee_id: id, display_name: name, status: "starting", start_date: null, ...extra };
}
// 3 Nov 2026, 10:00 in Kuala Lumpur.
const NOW = new Date("2026-11-03T02:00:00Z");
const HOME = { training_waiting: 0, requests_waiting: 0 };
const interview = (id, starts, status = "scheduled") => ({ id, starts_at: starts, ends_at: starts, status });

test("homeItems counts waiting work and unread notices, and nothing when they are zero", () => {
  assert.deepEqual(homeItems({ home: HOME, interviews: [], unread: 0, starting: [], now: NOW }), []);
  const items = homeItems({ home: { training_waiting: 2, requests_waiting: 1 }, interviews: [], unread: 3, starting: [], now: NOW });
  assert.deepEqual(items.map((item) => [item.kind, item.label, item.href]), [
    ["training", "2 training requests waiting for your decision", { tab: "training" }],
    ["requests", "1 HR request waiting for your decision", { tab: "requests" }],
    ["notifications", "3 unread notifications", { tab: "home" }],
  ]);
});

test("homeItems keeps interviews from today to 7 days ahead, by Kuala Lumpur date", () => {
  const items = homeItems({
    home: HOME, unread: 0, starting: [], now: NOW,
    interviews: [
      interview("early", "2026-11-02T15:59:00Z"),       // 2 Nov 23:59 KL: yesterday
      interview("start", "2026-11-02T16:00:00Z"),       // 3 Nov 00:00 KL: today
      interview("last", "2026-11-10T15:59:00Z"),        // 10 Nov 23:59 KL: day 7
      interview("late", "2026-11-10T16:00:00Z"),        // 11 Nov 00:00 KL: day 8
      interview("cancelled", "2026-11-05T02:00:00Z", "cancelled"),
      interview("middle", "2026-11-05T02:30:00Z"),
    ],
  });
  assert.deepEqual(items.map((item) => item.kind), ["interview", "interview", "interview"]);
  assert.deepEqual(items.map((item) => item.label), [
    "Interview on 3 Nov 2026, 00:00",
    "Interview on 5 Nov 2026, 10:30",
    "Interview on 10 Nov 2026, 23:59",
  ]);
  assert.deepEqual(items[0].href, { tab: "recruitment" });
});

test("homeItems keeps starters from today to 14 days ahead and links to their profile", () => {
  const items = homeItems({
    home: HOME, interviews: [], unread: 0, now: NOW,
    starting: [
      person("past", "Past", { start_date: "2026-11-02" }),
      person("none", "None"),
      person("far", "Far", { start_date: "2026-11-18" }),
      person("last", "Last", { start_date: "2026-11-17" }),
      person("today", "Today", { start_date: "2026-11-03" }),
      person("gone", "Gone", { start_date: "2026-11-04", status: "left" }),
    ],
  });
  assert.deepEqual(items.map((item) => [item.kind, item.label]), [
    ["starting", "Today starts on 3 Nov 2026"],
    ["starting", "Last starts on 17 Nov 2026"],
  ]);
  assert.deepEqual(items[0].href, { tab: "people", view: "profile", employeeId: "today" });
});

test("homeItems takes the 1b leaving seam and shows nothing for it while it is empty", () => {
  const base = { home: HOME, interviews: [], unread: 0, starting: [], now: NOW };
  assert.deepEqual(homeItems({ ...base, leaving: [] }), []);
  assert.deepEqual(homeItems(base), []);
  const items = homeItems({ ...base, leaving: [person("l", "Lee", { status: "active" })] });
  assert.deepEqual(items.map((item) => item.kind), ["leaving"]);
});

test("homeItems follows the Kuala Lumpur date across midnight UTC", () => {
  // 2 Nov 2026, 17:00 UTC is already 3 Nov in Kuala Lumpur.
  const items = homeItems({
    home: HOME, interviews: [], unread: 0, now: new Date("2026-11-02T17:00:00Z"),
    starting: [person("a", "Ann", { start_date: "2026-11-03" }), person("b", "Bob", { start_date: "2026-11-02" })],
  });
  assert.deepEqual(items.map((item) => item.label), ["Ann starts on 3 Nov 2026"]);
});

test("emptyText has an editor and a non-editor variant (Review Focus 5)", () => {
  for (const kind of ["people", "departments", "org"]) {
    assert.match(emptyText(kind, true), /Add (person|department)/, kind);
    assert.match(emptyText(kind, false), /HR owner/, kind);
    assert.doesNotMatch(emptyText(kind, false), /Add (person|department)/, kind);
    assert.equal(emptyText(kind), emptyText(kind, false), `${kind} defaults to the non-editor text`);
  }
  assert.equal(emptyText("home"), "Nothing needs you today.");
  assert.equal(emptyText("home", true), "Nothing needs you today.");
  assert.match(emptyText("unlinked"), /isn't linked to an employee record/);
  assert.match(emptyText("unlinked"), /HR owner/);
});

test("historyText says what changed in plain words", () => {
  const change = (field, old, value) => ({ field, old, new: value });
  assert.equal(historyText(change("job_title", "Designer", "Lead designer")),
    'Job title changed from "Designer" to "Lead designer"');
  assert.equal(historyText(change("work_email", null, "a@example.test")), 'Work email set to "a@example.test"');
  assert.equal(historyText(change("manager", "Aina", null)), 'Manager cleared (was "Aina")');
  assert.equal(historyText(change("display_name", null, null)), "Name changed");
  assert.equal(historyText(change("employment_type", "part_time", "full_time")),
    'Employment type changed from "Part-time" to "Full-time"');
  assert.equal(historyText(change("start_date", "2026-11-03", "2026-12-01")),
    'Start date changed from "3 Nov 2026" to "1 Dec 2026"');
  assert.equal(historyText(change("active", "true", "false")), 'Status changed from "Active" to "Left"');
  assert.equal(historyText(change("region", "sabah", "sarawak")), 'Region changed from "Sabah" to "Sarawak"');
  assert.equal(historyText(change("legal_entity_reference", null, "ENT-1")), 'Legal entity set to "ENT-1"');
});

test("peopleRefusalText has one plain sentence per refusal and a fallback", () => {
  const codes = [
    "employee_exists", "person_changed", "manager_unavailable", "manager_loop", "department_unavailable",
    "department_archived", "department_changed", "department_name_taken", "account_taken", "member_unavailable",
    "manager_is_you", "own_manager_owner_only",
  ];
  const seen = new Set();
  for (const code of codes) {
    const text = peopleRefusalText(code);
    assert.ok(text.length > 20, code);
    assert.doesNotMatch(text, /_/, code);
    assert.notEqual(text, peopleRefusalText("something_else"), code);
    seen.add(text);
  }
  assert.equal(seen.size, codes.length);
  assert.equal(peopleRefusalText("person_changed"), PEOPLE_COPY.stale);
  assert.equal(peopleRefusalText("manager_is_you"), "You can't make yourself someone's manager. Ask the HR owner.");
  assert.equal(peopleRefusalText("own_manager_owner_only"), "Only the HR owner can change your own manager.");
  for (const bad of ["__proto__", "constructor", "", "toString"])
    assert.equal(peopleRefusalText(bad), PEOPLE_COPY.refusalFallback, bad);
});

test("only a writing HR owner runs local maintenance from Home", () => {
  assert.equal(runsHomeMaintenance({ client_owner: true }, false), true);
  assert.equal(runsHomeMaintenance({ client_owner: true }, true), false);
  assert.equal(runsHomeMaintenance({ client_owner: false }, false), false);
  assert.equal(runsHomeMaintenance({ client_owner: false }, true), false);
});

test("peopleFilters drops 'all' and blank values and maps No department to none", () => {
  assert.deepEqual(peopleFilters({}, { q: "  ", department: "all", manager: "all", status: "all" }), {});
  assert.deepEqual(peopleFilters({}, { manager: "" }), {});
  assert.deepEqual(peopleFilters({}, { q: " aina ", department: "none" }), { q: "aina", department: "none" });
  assert.deepEqual(peopleFilters({}, { department: DEPARTMENT, manager: MANAGER, status: "left" }),
    { department: DEPARTMENT, manager: MANAGER, status: "left" });
  assert.deepEqual(peopleFilters({ department: DEPARTMENT, status: "left" }, { department: "all" }), { status: "left" });
});

test("peopleFilters keeps the cursor only while the filters stay the same", () => {
  assert.deepEqual(peopleFilters({ q: "aina" }, { cursor: "50" }), { q: "aina", cursor: "50" });
  assert.deepEqual(peopleFilters({ q: "aina", cursor: "50" }, { cursor: "100" }), { q: "aina", cursor: "100" });
  assert.deepEqual(peopleFilters({ q: "aina" }, { q: " aina ", cursor: "50" }), { q: "aina", cursor: "50" });
  assert.deepEqual(peopleFilters({ q: "aina", cursor: "50" }, { status: "left" }), { q: "aina", status: "left" });
  assert.deepEqual(peopleFilters({ q: "aina", cursor: "50" }, { q: "bala", cursor: "50" }), { q: "bala" });
  assert.deepEqual(peopleFilters({ department: DEPARTMENT, cursor: "50" }, { department: "all" }), {});
  assert.deepEqual(peopleFilters({ q: "aina", cursor: "50" }, {}), { q: "aina" });
});

const PERSON = {
  employee_id: ID, display_name: "Aina Rahman", job_title: "Designer", department_id: DEPARTMENT, department: "Design",
  manager_id: MANAGER, manager_name: "Bala", work_email: "aina@example.test", work_phone: "+60 12-345 6789",
  start_date: "2026-11-03", employment_type: "part_time", status: "active",
  employment_profile: { region: "sabah", legal_entity_reference: "ENT-1", employment_category: "Permanent" },
  version: 4, account_linked: true,
};
const BLANK_PERSON = {
  ...PERSON, job_title: null, department_id: null, department: null, manager_id: null, manager_name: null,
  work_email: null, work_phone: null, start_date: null, employment_type: null, employment_profile: null,
  version: null, account_linked: null,
};

test("profileTabs labels the server's sections in the server's order", () => {
  assert.deepEqual(profileTabs(["overview", "access", "history"]), [
    { value: "overview", label: "Overview" }, { value: "access", label: "Access" }, { value: "history", label: "History" },
  ]);
  assert.deepEqual(profileTabs(["overview"]), [{ value: "overview", label: "Overview" }]);
  assert.deepEqual(profileTabs(["history", "overview"]).map((tab) => tab.value), ["history", "overview"]);
  assert.deepEqual(profileTabs(["overview", "personal"]), [{ value: "overview", label: "Overview" }]);
});

test("personFormValues prefills every field and the employment profile from the person (full replace)", () => {
  const form = personFormValues(PERSON);
  assert.deepEqual(form, {
    display_name: "Aina Rahman", job_title: "Designer", start_date: "2026-11-03", manager_id: MANAGER,
    department_id: DEPARTMENT, new_department: "", work_email: "aina@example.test", work_phone: "+60 12-345 6789",
    employment_type: "part_time", region: "sabah", legal_entity_reference: "ENT-1", employment_category: "Permanent",
  });
  // An untouched prefilled form saves exactly what is stored: no field is lost.
  const body = personBody("update", form, { employeeId: ID, version: PERSON.version });
  assert.deepEqual(body.values, {
    display_name: "Aina Rahman", job_title: "Designer", start_date: "2026-11-03", manager_id: MANAGER,
    department_id: DEPARTMENT, work_email: "aina@example.test", work_phone: "+60 12-345 6789", employment_type: "part_time",
  });
  assert.deepEqual(body.employment_profile, PERSON.employment_profile);
  assert.equal(body.expected_version, 4);
});

test("personFormValues turns missing values into empty fields, and no person into an empty form", () => {
  const blank = personFormValues(BLANK_PERSON);
  assert.equal(blank.manager_id, NO_MANAGER);
  assert.equal(blank.department_id, "");
  for (const key of ["job_title", "start_date", "work_email", "work_phone", "employment_type", "region",
    "legal_entity_reference", "employment_category", "new_department"]) assert.equal(blank[key], "", key);
  const empty = personFormValues();
  assert.deepEqual(Object.values(empty), Object.values(empty).map(() => ""));
  assert.deepEqual(Object.keys(empty).sort(), Object.keys(FORM).sort());
});

test("personFormIssue blocks a missing name, an add without title or start, a blank new department and a partial profile", () => {
  assert.equal(personFormIssue("add", FORM), null);
  assert.equal(personFormIssue("update", FORM), null);
  assert.equal(personFormIssue("add", { ...FORM, display_name: "  " }), "Enter the person's name.");
  assert.equal(personFormIssue("update", { ...FORM, display_name: "" }), "Enter the person's name.");
  assert.equal(personFormIssue("add", { ...FORM, job_title: " " }), "Enter a job title.");
  assert.equal(personFormIssue("add", { ...FORM, start_date: "" }), "Choose a start date.");
  // Editing keeps job title and start date optional, as the server does.
  assert.equal(personFormIssue("update", { ...FORM, job_title: "", start_date: "" }), null);
  for (const mode of ["add", "update"]) {
    assert.equal(personFormIssue(mode, { ...FORM, department_id: NEW_DEPARTMENT, new_department: "  " }),
      "Type a name for the new department, or choose another department.", mode);
    assert.equal(personFormIssue(mode, { ...FORM, department_id: NEW_DEPARTMENT, new_department: "Care" }), null, mode);
    assert.equal(personFormIssue(mode, { ...FORM, region: "sabah" }),
      "Fill in all three employment details, or leave all three empty.", mode);
  }
});

const VIEWER = "55555555-5555-4555-8555-555555555555";
const OPTIONS = [[ID, "Aina Rahman · Designer"], [MANAGER, "Bala"], [VIEWER, "Me · HR"], ["x", "Xin"]];

test("managerChoices never offers the person as their own manager", () => {
  const { options, locked } = managerChoices({ options: OPTIONS, person: PERSON, viewer: { owner: true, employeeId: VIEWER } });
  assert.deepEqual(options, [[MANAGER, "Bala"], [VIEWER, "Me · HR"], ["x", "Xin"]]);
  assert.equal(locked, false);
});

test("managerChoices keeps a current manager who has left, marked (left) (F5)", () => {
  const { options } = managerChoices({
    options: OPTIONS.filter(([id]) => id !== MANAGER), person: PERSON, viewer: { owner: true, employeeId: null },
  });
  assert.deepEqual(options.at(-1), [MANAGER, "Bala (left)"]);
  assert.equal(options.filter(([id]) => id === MANAGER).length, 1);
  const unnamed = managerChoices({ options: [], person: { ...PERSON, manager_name: null }, viewer: { owner: true, employeeId: null } });
  assert.deepEqual(unnamed.options, [[MANAGER, "Current manager (left)"]]);
  assert.deepEqual(managerChoices({ options: [], person: BLANK_PERSON, viewer: { owner: true, employeeId: null } }).options, []);
});

test("managerChoices hides the viewer from a non-owner's choices, unless they already are the manager", () => {
  const add = managerChoices({ options: OPTIONS, viewer: { owner: false, employeeId: VIEWER } });
  assert.deepEqual(add.options.map(([id]) => id), [ID, MANAGER, "x"]);
  assert.equal(add.locked, false);
  const owner = managerChoices({ options: OPTIONS, viewer: { owner: true, employeeId: VIEWER } });
  assert.deepEqual(owner.options.map(([id]) => id), [ID, MANAGER, VIEWER, "x"]);
  const kept = managerChoices({ options: OPTIONS, person: { ...PERSON, manager_id: VIEWER, manager_name: "Me" },
    viewer: { owner: false, employeeId: VIEWER } });
  assert.deepEqual(kept.options.map(([id]) => id), [MANAGER, VIEWER, "x"]);
});

test("managerChoices locks a non-owner's own manager", () => {
  const self = { ...PERSON, employee_id: VIEWER };
  assert.equal(managerChoices({ options: OPTIONS, person: self, viewer: { owner: false, employeeId: VIEWER } }).locked, true);
  assert.equal(managerChoices({ options: OPTIONS, person: self, viewer: { owner: true, employeeId: VIEWER } }).locked, false);
  assert.equal(managerChoices({ options: OPTIONS, person: PERSON, viewer: { owner: false, employeeId: VIEWER } }).locked, false);
  assert.equal(managerChoices({ options: OPTIONS, person: PERSON, viewer: { owner: false, employeeId: null } }).locked, false);
});

const dept = (id, name, archived = false) => ({ department_id: id, name, archived, version: 1, people: 0 });

test("departmentChoices offers active departments and keeps the current one, marked (archived) (F5)", () => {
  const list = [dept("a", "Design"), dept(DEPARTMENT, "Old team", true), dept("c", "Finance", true), dept("d", "Sales")];
  assert.deepEqual(departmentChoices(list), [["a", "Design"], ["d", "Sales"]]);
  assert.deepEqual(departmentChoices(list, PERSON), [["a", "Design"], [DEPARTMENT, "Old team (archived)"], ["d", "Sales"]]);
  assert.deepEqual(departmentChoices([dept(DEPARTMENT, "Design")], PERSON), [[DEPARTMENT, "Design"]]);
  // The list could not be read: the person's department is still offered, so saving never clears it.
  assert.deepEqual(departmentChoices([], PERSON), [[DEPARTMENT, "Design"]]);
  assert.deepEqual(departmentChoices([], BLANK_PERSON), []);
});

test("overviewRows shows plain labels, a dash for missing values, and HR-only rows only when present", () => {
  assert.deepEqual(overviewRows(PERSON), [
    ["Name", "Aina Rahman"], ["Job title", "Designer"], ["Department", "Design"], ["Manager", "Bala"],
    ["Work email", "aina@example.test"], ["Work phone", "+60 12-345 6789"], ["Start date", "3 Nov 2026"],
    ["Employment type", "Part-time"], ["Status", "Active"],
    ["Region", "Sabah"], ["Legal entity", "ENT-1"], ["Employment category", "Permanent"],
    ["Console account", "Linked"],
  ]);
  assert.deepEqual(overviewRows(BLANK_PERSON), [
    ["Name", "Aina Rahman"], ["Job title", "—"], ["Department", "—"], ["Manager", "—"],
    ["Work email", "—"], ["Work phone", "—"], ["Start date", "—"], ["Employment type", "—"], ["Status", "Active"],
  ]);
  assert.deepEqual(overviewRows({ ...BLANK_PERSON, status: "starting", account_linked: false }).slice(-2),
    [["Status", "Starting"], ["Console account", "Not linked"]]);
});

const CONTEXT = { client_owner: false, switches: [], actions: [] };

test("homeQuickActions offers only what the viewer may do", () => {
  const labels = (context, readonly = false) => homeQuickActions(context, readonly).map((item) => item.label);
  assert.deepEqual(labels(CONTEXT), []);
  assert.deepEqual(homeQuickActions({ ...CONTEXT, switches: ["manage_people"] }, false), [
    { key: "add-person", label: "Add person", href: { tab: "people", view: "new" } },
  ]);
  assert.deepEqual(homeQuickActions({ ...CONTEXT, actions: ["training.draft"] }, false), [
    { key: "request-training", label: "Request training", href: { tab: "training" } },
  ]);
  assert.deepEqual(labels({ ...CONTEXT, client_owner: true, actions: ["training.draft"] }),
    ["Add person", "Request training", "Open the access page"]);
  assert.deepEqual(homeQuickActions({ ...CONTEXT, client_owner: true }, false).at(-1).href, { tab: "settings" });
  // A read-only login cannot write, but the owner may still open the access page.
  assert.deepEqual(labels({ ...CONTEXT, client_owner: true, switches: ["manage_people"], actions: ["training.draft"] }, true),
    ["Open the access page"]);
});

test("myBalanceLines keeps the viewer's own balances, newest year first, in plain words", () => {
  const balance = (employee, year, type, remaining, days) => ({ employee_id: employee, year, leave_type: type, remaining, days });
  const lines = myBalanceLines([
    balance(ID, 2025, "annual", "2", "14"), balance("other", 2026, "annual", "9", "14"),
    balance(ID, 2026, "sick", "14", "14"), balance(ID, 2026, "annual", "12.5", "14"), balance(ID, 2026, "carer", "1", "1"),
  ], ID);
  assert.deepEqual(lines.map((line) => line.text), [
    "2026 · Annual leave: 12.5 of 14 days left",
    "2026 · Sick leave: 14 of 14 days left",
    "2026 · Carer: 1 of 1 day left",
    "2025 · Annual leave: 2 of 14 days left",
  ]);
  assert.equal(new Set(lines.map((line) => line.key)).size, 4);
  assert.deepEqual(myBalanceLines([balance(ID, 2026, "annual", "1", "1")], null), []);
});

test("myTrainingLines keeps the viewer's own training requests with plain statuses", () => {
  const request = (id, employee, title, status) => ({ request_id: id, employee: { employee_id: employee }, content: { title }, status });
  assert.deepEqual(myTrainingLines([
    request("r1", ID, "First aid", "submitted"), request("r2", "other", "Excel", "draft"), request("r3", ID, "Driving", "withdrawn"),
  ], ID), [
    { key: "r1", title: "First aid", status: "Waiting for a decision" },
    { key: "r3", title: "Driving", status: "Withdrawn" },
  ]);
  assert.deepEqual(myTrainingLines([request("r1", ID, "First aid", "draft")], null), []);
});
