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
const apiUrl = moduleUrl(await readFile(new URL("../lib/api.ts", import.meta.url), "utf8"));
const source = await readFile(new URL("../lib/hr-access-api.ts", import.meta.url), "utf8");
const access = await import(moduleUrl(source.replace('"./api"', JSON.stringify(apiUrl))));
const copy = await import(moduleUrl(await readFile(new URL("../lib/hr-access-copy.ts", import.meta.url), "utf8")));
const { ApiError } = await import(apiUrl);

const TENANT = "a b/1";
const SUFFIX = "client_id=a%20b%2F1";
const USER = "11111111-1111-4111-8111-111111111111";
const BATCH = "22222222-2222-4222-8222-222222222222";
const MANAGER = "33333333-3333-4333-8333-333333333333";

async function capture(run, reply = () => Response.json({})) {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (path, init) => {
    if (path === "/console/v1/auth/csrf") return Response.json({ csrf_token: "synthetic-csrf" });
    requests.push([path, init]);
    return reply(path, init);
  };
  try { return [await run(), requests]; } finally { globalThis.fetch = original; }
}

test("accessPeople names the tenant like hrClient and leaves out empty filters", async () => {
  const [, requests] = await capture(async () => {
    await access.accessPeople(TENANT, {});
    await access.accessPeople(TENANT, { q: "Aina Rahman", has: "payroll_review", team: "", cursor: undefined });
    await access.accessPeople(TENANT, { cursor: "50", team: "Sales & Ops", reports_to: MANAGER });
    await access.accessRecent(TENANT);
    await access.accessRecent(TENANT, "c/1");
  });
  assert.deepEqual(requests.map(([path]) => path), [
    `/console/v1/hr/access/people?${SUFFIX}`,
    `/console/v1/hr/access/people?${SUFFIX}&q=Aina+Rahman&has=payroll_review`,
    `/console/v1/hr/access/people?${SUFFIX}&cursor=50&team=Sales+%26+Ops&reports_to=${MANAGER}`,
    `/console/v1/hr/access/recent?${SUFFIX}`,
    `/console/v1/hr/access/recent?${SUFFIX}&cursor=c%2F1`,
  ]);
  for (const [, init] of requests) {
    assert.equal(init.method ?? "GET", "GET");
    assert.equal(init.credentials, "same-origin");
    assert.equal(new Headers(init.headers).has("X-CSRF-Token"), false);
  }
});

test("saveAccessChanges posts the batch as JSON with the CSRF header", async () => {
  const body = { batch_id: BATCH, version: "v1", dry_run: true, changes: [{ user_id: USER, on: ["directory", "own_requests"] }] };
  const result = { results: [{ user_id: USER, outcome: "saved", reason: null }], version: "v1", warnings: [] };
  const [answer, requests] = await capture(() => access.saveAccessChanges(TENANT, body), () => Response.json(result));
  assert.deepEqual(answer, result);
  assert.equal(requests.length, 1);
  const [path, init] = requests[0];
  assert.equal(path, `/console/v1/hr/access/changes?${SUFFIX}`);
  assert.equal(init.method, "POST");
  assert.deepEqual(JSON.parse(init.body), body);
  assert.equal(init.headers.get("X-CSRF-Token"), "synthetic-csrf");
  assert.equal(init.headers.get("Content-Type"), "application/json");
});

test("a stale version comes back as an ApiError with status 409", async () => {
  const body = { batch_id: BATCH, version: "old", dry_run: false, changes: [{ user_id: USER, on: [] }] };
  const [failure] = await capture(
    () => access.saveAccessChanges(TENANT, body).then(() => null, (cause) => cause),
    () => Response.json({ detail: "access_changed" }, { status: 409 }),
  );
  assert.ok(failure instanceof ApiError);
  assert.equal(failure.status, 409);
  assert.equal(failure.code, "access_changed");
  assert.equal(access.isAccessChanged(failure), true);
  assert.equal(access.isAccessChanged(new ApiError(409, "invalid_cursor")), false);
});

const warning = (kind, extra = {}) => ({
  kind, user_id: null, employee_id: null, user_name: null, employee_name: null, manager_name: null, ...extra,
});

test("warning sentences name the people involved", () => {
  const approver = copy.warningText(warning("no_approver", { employee_name: "Aina Rahman", manager_name: "Daniel Lim" }));
  assert.match(approver, /Aina Rahman/);
  assert.match(approver, /Daniel Lim/);
  const noManager = copy.warningText(warning("no_manager", { employee_name: "Aina Rahman" }));
  assert.match(noManager, /Aina Rahman/);
  assert.match(noManager, /Only you, as HR owner, can approve these/);
  assert.match(copy.warningText(warning("manager_without_team", { user_name: "Daniel Lim" })), /Daniel Lim/);
  assert.match(copy.warningText(warning("prepares_and_reviews", { user_name: "Siti Noor" })), /Siti Noor/);
  assert.match(copy.warningText(warning("no_payroll_reviewer")), /Reviews payroll/);
  for (const kind of ["no_approver", "no_manager", "manager_without_team", "prepares_and_reviews"]) {
    const text = copy.warningText(warning(kind));
    assert.doesNotMatch(text, /null|undefined|_/, `${kind} reads well without names`);
  }
});

test("a member who left shows as Former member", () => {
  assert.equal(copy.formerMember(null), "Former member");
  assert.equal(copy.formerMember("Aina Rahman"), "Aina Rahman");
});

test("every switch has the agreed label, an explanation and a group", () => {
  assert.deepEqual(Object.fromEntries(Object.entries(copy.SWITCH_COPY).map(([id, item]) => [id, item.label])), {
    directory: "See everyone's basic details",
    own_requests: "Can request their own training, leave and attendance",
    approves_team: "Approves training, leave and attendance for their team",
    hr_team: "HR team member",
    recruitment: "Works on recruitment",
    attendance: "Works on attendance",
    payroll_prepare: "Prepares payroll",
    payroll_review: "Reviews payroll",
  });
  for (const item of Object.values(copy.SWITCH_COPY)) {
    assert.ok(item.explanation.length > 20);
    assert.ok(["Everyday", "Responsibilities", "Private areas"].includes(item.group));
  }
  assert.deepEqual(Object.keys(copy.PRESET_COPY), ["staff", "manager", "hr_team", "payroll_preparer", "payroll_reviewer"]);
});

test("desired sets keep included switches and presets only add", () => {
  assert.deepEqual(access.withIncluded(["hr_team"]), ["directory", "hr_team"]);
  assert.deepEqual(access.withIncluded(["payroll_review", "directory"]), ["directory", "payroll_review"]);
  assert.deepEqual(access.applyPreset(["payroll_review"], "hr_team"),
    ["directory", "own_requests", "hr_team", "recruitment", "attendance", "payroll_review"]);
  assert.equal(access.includedWith(["hr_team", "directory"], "directory"), "hr_team");
  assert.equal(access.includedWith(["directory"], "directory"), null);
  assert.equal(access.changeCount(["directory"], ["directory", "own_requests", "approves_team"]), 2);
  assert.equal(access.changeCount(["directory", "hr_team"], ["hr_team", "directory"]), 0);
});
