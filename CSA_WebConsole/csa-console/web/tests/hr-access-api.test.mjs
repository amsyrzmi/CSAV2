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

const member = (user_id, on) => ({
  user_id, display_name: "Synthetic Member", employee_id: null, department: null, manager_id: null,
  team_size: 0, is_owner: false, on, included: {}, custom: [],
});
const OTHER = "44444444-4444-4444-8444-444444444444";
const AWAY = "55555555-5555-4555-8555-555555555555";

test("a rebase keeps the owner's own change on top of the latest switches", () => {
  const held = {
    [USER]: { base: ["own_requests"], desired: ["own_requests", "payroll_prepare"], person: member(USER, ["own_requests"]) },
    [MANAGER]: { base: ["directory", "own_requests"], desired: ["own_requests"], person: member(MANAGER, ["directory", "own_requests"]) },
    [OTHER]: { base: [], desired: ["directory"], person: member(OTHER, []) },
    [AWAY]: { base: [], desired: ["approves_team"], person: member(AWAY, []) },
  };
  const fresh = [
    member(USER, ["directory", "own_requests"]),
    member(MANAGER, ["directory", "own_requests", "approves_team"]),
    member(OTHER, ["directory"]),
  ];
  const next = access.rebasePending(held, fresh);
  // Another tab turned directory on: it stays on, and only payroll_prepare is still pending.
  assert.deepEqual(next[USER].base, ["directory", "own_requests"]);
  assert.deepEqual(next[USER].desired, ["directory", "own_requests", "payroll_prepare"]);
  assert.equal(next[USER].person, fresh[0]);
  // A switch the owner was turning off stays off; one someone else turned on stays on.
  assert.deepEqual(next[MANAGER].desired, ["own_requests", "approves_team"]);
  assert.deepEqual(next[MANAGER].base, ["directory", "own_requests", "approves_team"]);
  // Someone else already made the same change: nothing is left to save.
  assert.equal(OTHER in next, false);
  // A member not on the refreshed page keeps the base the page loaded.
  assert.equal(next[AWAY], held[AWAY]);
  assert.deepEqual(held[USER].desired, ["own_requests", "payroll_prepare"], "the input is not changed");
});

test("a rebase keeps included switches with the switch that includes them", () => {
  const held = { [USER]: { base: ["directory"], desired: [], person: member(USER, ["directory"]) } };
  assert.deepEqual(access.rebasePending(held, [member(USER, ["directory", "hr_team"])]), {});
  const kept = access.rebasePending(
    { [USER]: { base: [], desired: ["payroll_review"], person: member(USER, []) } },
    [member(USER, ["directory", "hr_team"])],
  );
  assert.deepEqual(kept[USER].desired, ["directory", "hr_team", "payroll_review"]);
});

test("turning an outer switch off puts the included switch back to its saved value", () => {
  const on = access.setSwitch(["own_requests"], "hr_team", true, ["own_requests"]);
  assert.deepEqual(on, ["directory", "own_requests", "hr_team"]);
  assert.deepEqual(access.setSwitch(on, "hr_team", false, ["own_requests"]), ["own_requests"]);
  assert.deepEqual(access.setSwitch(["directory", "hr_team"], "hr_team", false, ["directory"]), ["directory"]);
  assert.deepEqual(access.setSwitch(["directory", "hr_team"], "hr_team", false, ["directory", "hr_team"]), ["directory"]);
  assert.deepEqual(access.setSwitch(["directory", "hr_team"], "directory", false, []), ["directory", "hr_team"]);
});

test("the new refusal reasons read as plain sentences", () => {
  assert.equal(copy.refusalText("changed_since_load"),
    "Their access changed since you opened this page. Reload to see the latest.");
  const partly = copy.refusalText("partly_saved");
  assert.notEqual(partly, copy.refusalText(null));
  assert.doesNotMatch(partly, /_/);
});

const PENDING_ID = "66666666-6666-4666-8666-666666666666";

test("the pending, apply and dismiss calls carry the tenant", async () => {
  const pending = { items: [{ token_id: PENDING_ID, user_id: USER, display_name: "Aina Rahman", preset: "manager",
    chosen_by: MANAGER, chooser_name: "Daniel Lim", accepted_at: "2026-10-29T01:00:00Z" }] };
  const applied = { user_id: USER, outcome: "saved", reason: null };
  const [, requests] = await capture(async () => {
    assert.deepEqual(await access.pendingInvitations(TENANT), pending);
    assert.deepEqual(await access.applyInvitation(TENANT, PENDING_ID), applied);
    assert.equal(await access.dismissInvitation(TENANT, PENDING_ID), undefined);
  }, (path) => path.includes("/dismiss") ? new Response(null, { status: 204 })
    : path.includes("/apply") ? Response.json(applied) : Response.json(pending));
  assert.deepEqual(requests.map(([path, init]) => [init.method ?? "GET", path]), [
    ["GET", `/console/v1/hr/access/invitations?${SUFFIX}`],
    ["POST", `/console/v1/hr/access/invitations/${PENDING_ID}/apply?${SUFFIX}`],
    ["POST", `/console/v1/hr/access/invitations/${PENDING_ID}/dismiss?${SUFFIX}`],
  ]);
  for (const [, init] of requests.slice(1)) assert.equal(init.headers.get("X-CSRF-Token"), "synthetic-csrf");
});

test("an invitation that was already handled keeps its code and reads as a sentence", async () => {
  const [failure] = await capture(
    () => access.applyInvitation(TENANT, PENDING_ID).then(() => null, (cause) => cause),
    () => Response.json({ detail: "invitation_already_handled" }, { status: 409 }),
  );
  assert.ok(failure instanceof ApiError);
  assert.equal(failure.code, "invitation_already_handled");
  assert.equal(failure.message, "This was already applied or dismissed. Reload to see the latest.");
});

const event = (extra) => ({
  occurred_at: "2026-10-29T01:00:00Z", source: "invitation", preset: "manager", actor_user_id: MANAGER,
  actor_name: "Daniel Lim", target_user_id: USER, target_name: "Aina Rahman",
  turned_on: ["approves_team"], turned_off: [], area: null, area_enabled: null, ...extra,
});

test("an invitation event names who chose the access and the preset", () => {
  assert.equal(copy.eventText(event({})), "Set on invitation by Daniel Lim: Manager for Aina Rahman");
  assert.match(copy.eventText(event({ actor_name: "FourStacks admin", preset: "staff" })), /FourStacks admin/);
  assert.match(copy.eventText(event({ actor_name: null })), /Set on invitation by Former member/);
  assert.equal(copy.eventText(event({ source: "page", preset: null, turned_on: ["recruitment"] })),
    'Daniel Lim turned on "Works on recruitment" for Aina Rahman');
});

test("the pending line names the preset and who chose it", () => {
  const row = { token_id: PENDING_ID, user_id: USER, display_name: "Aina Rahman", preset: "payroll_reviewer",
    chosen_by: MANAGER, chooser_name: null, accepted_at: "2026-10-29T01:00:00Z" };
  assert.equal(copy.pendingText(row), "Payroll reviewer, chosen by Former member");
  assert.equal(copy.pendingText({ ...row, chooser_name: "FourStacks admin", preset: "staff" }),
    "Staff, chosen by FourStacks admin");
});
