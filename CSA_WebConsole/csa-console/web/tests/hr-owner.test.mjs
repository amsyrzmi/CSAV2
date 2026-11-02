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
const source = await readFile(new URL("../lib/hr-owner.ts", import.meta.url), "utf8");
const owner = await import(moduleUrl(source.replace('"./api"', JSON.stringify(apiUrl))));
const { ApiError } = await import(apiUrl);

const TENANT = "a b/1";
const ADA = "11111111-1111-4111-8111-111111111111";
const BEN = "22222222-2222-4222-8222-222222222222";
const KEY = "33333333-3333-4333-8333-333333333333";

const member = (user_id, display_name, role = "tenant_staff", is_enabled = true) =>
  ({ user_id, client_id: TENANT, email: `${display_name}@example.test`, display_name, role, is_enabled });

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

test("read and move name the company, send the key and the expected owner", async () => {
  const [, requests] = await capture(async () => {
    await owner.hrOwnerClient(TENANT).read();
    await owner.hrOwnerClient(TENANT).move(BEN, ADA, KEY);
    await owner.hrOwnerClient(TENANT).move(BEN, null, KEY);
  });
  assert.equal(requests[0][0], "/console/v1/team/hr-owner?client_id=a%20b%2F1");
  assert.equal(requests[0][1].method, undefined);
  const [path, init] = requests[1];
  assert.equal(path, "/console/v1/team/hr-owner");
  assert.equal(init.method, "POST");
  assert.equal(new Headers(init.headers).get("Idempotency-Key"), KEY);
  assert.deepEqual(JSON.parse(init.body), { client_id: TENANT, new_owner_id: BEN, expected_owner_id: ADA });
  assert.equal(JSON.parse(requests[2][1].body).expected_owner_id, null);
});

test("eligibleOwners drops disabled, read-only and the current owner, and sorts by name", () => {
  const members = [
    member("u3", "Zara"), member("u1", "Chen", "tenant_admin"), member("u2", "Bea", "read_only"),
    member("u4", "Dev", "tenant_staff", false), member(ADA, "Ada"), member("u5", "Bob"),
  ];
  const current = { user_id: ADA, display_name: "Ada", usable: true };
  assert.deepEqual(owner.eligibleOwners(members, current).map((m) => m.display_name), ["Bob", "Chen", "Zara"]);
  assert.deepEqual(owner.eligibleOwners(members, null).map((m) => m.display_name), ["Ada", "Bob", "Chen", "Zara"]);
  assert.deepEqual(members.map((m) => m.user_id), ["u3", "u1", "u2", "u4", ADA, "u5"], "input untouched");
});

test("ownerRefusalText gives each sentence verbatim and a fallback for anything else", () => {
  const said = (code) => owner.ownerRefusalText(new ApiError(409, code, code));
  assert.equal(said("owner_read_only"), "A read-only member can't be the HR owner. Change their role first.");
  assert.equal(said("owner_leaving"), "This person is leaving, so they can't be the HR owner. Choose someone else.");
  assert.equal(said("owner_unavailable"),
    "This person can't be the HR owner yet. They need to sign in and change their password first.");
  assert.equal(said("owner_unchanged"), "This person is already the HR owner.");
  assert.equal(said("owner_changed"),
    "The HR owner changed since you opened this page. Reload to see who it is now.");
  assert.equal(said("try_again"), "Someone else changed HR records at the same moment. Please try again.");
  const fallback = "We couldn't change the HR owner. Please try again.";
  assert.equal(said("idempotency_conflict"), fallback);
  assert.equal(said("constructor"), fallback);
  assert.equal(owner.ownerRefusalText(new Error("offline")), fallback);
  assert.equal(owner.ownerRefusalText(undefined), fallback);
});

test("moveUncertain is true for a network error or a 5xx and false for any 4xx", () => {
  assert.equal(owner.moveUncertain(new TypeError("Failed to fetch")), true);
  assert.equal(owner.moveUncertain(new ApiError(500, "x")), true);
  assert.equal(owner.moveUncertain(new ApiError(502, "x")), true);
  assert.equal(owner.moveUncertain(new ApiError(409, "try_again")), false);
  assert.equal(owner.moveUncertain(new ApiError(403, "tenant administrator required")), false);
  assert.equal(owner.moveUncertain(new ApiError(422, "hr_not_available")), false);
});

test("uncertainText sends the admin to the panel only when it was read again", () => {
  assert.equal(owner.uncertainText(true),
    "We couldn't confirm the change. Check who the HR owner is above, then try again.");
  assert.equal(owner.uncertainText(false),
    "We couldn't confirm the change or reload the HR owner. Reload the page before you try again.");
});

test("ownerStale is true only for owner_changed", () => {
  assert.equal(owner.ownerStale(new ApiError(409, "owner_changed")), true);
  assert.equal(owner.ownerStale(new ApiError(409, "owner_unchanged")), false);
  assert.equal(owner.ownerStale(new Error("x")), false);
});

test("moveKey keeps the key for the same request and mints a new one for any other", () => {
  let n = 0;
  const make = () => `key-${++n}`;
  const first = owner.moveKey(null, BEN, ADA, make);
  assert.equal(first.key, "key-1");
  assert.equal(owner.moveKey(first, BEN, ADA, make), first);
  assert.equal(owner.moveKey(first, ADA, ADA, make).key, "key-2", "another person");
  assert.equal(owner.moveKey(first, BEN, null, make).key, "key-3", "another expected owner");
  assert.equal(owner.moveKey(null, BEN, null).key.length, 36, "defaults to crypto.randomUUID");
});

test("confirmText covers no old owner, an old owner and the admin choosing themself", () => {
  const first = "They get full control of who has HR access and can see sensitive employee details.";
  const last = "Payroll stays off until they turn it on. Everyone affected gets an email.";
  assert.equal(owner.confirmText({ oldOwnerName: null, isSelf: false }), `${first} ${last}`);
  assert.equal(owner.confirmText({ oldOwnerName: "Ada Lim", isSelf: false }),
    `${first} Ada Lim loses HR owner powers straight away. ${last}`);
  assert.equal(owner.confirmText({ oldOwnerName: "Ada Lim", isSelf: true }),
    `${first} Ada Lim loses HR owner powers straight away. ${last} You are making yourself the HR owner.`);
  assert.equal(owner.confirmText({ oldOwnerName: null, isSelf: true }),
    `${first} ${last} You are making yourself the HR owner.`);
});

test("moveDoneText says what happened to the emails", () => {
  assert.equal(owner.moveDoneText("sent"), "HR owner changed.");
  assert.equal(owner.moveDoneText("not_sent"),
    "HR owner changed, but we couldn't send the emails. Please tell the people affected.");
  assert.equal(owner.moveDoneText("repeat"),
    "HR owner changed. If you're not sure the emails went out, please tell the people affected.");
});
