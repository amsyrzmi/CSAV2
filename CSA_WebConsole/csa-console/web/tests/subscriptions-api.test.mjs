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
const source = await readFile(new URL("../lib/subscriptions-api.ts", import.meta.url), "utf8");
const subs = await import(moduleUrl(source.replace('"./api"', JSON.stringify(apiUrl))));
const { api, ApiError } = await import(apiUrl);

const SUB = "33333333-3333-4333-8333-333333333333";
const REQ = "44444444-4444-4444-8444-444444444444";

async function capture(run) {
  const original = globalThis.fetch;
  const requests = [];
  globalThis.fetch = async (path, init) => {
    if (path === "/console/v1/auth/csrf") return Response.json({ csrf_token: "synthetic-csrf" });
    requests.push([path, init]);
    return Response.json({});
  };
  try { return [await run(), requests]; } finally { globalThis.fetch = original; }
}
const shape = (requests) => requests.map(([path, init]) => [init.method ?? "GET", path]);

test("tenant members send no client_id; empty filters are left out", async () => {
  const [, requests] = await capture(async () => {
    await subs.listPlans(null, false);
    await subs.listPlans(null, true);
    await subs.createPlan(null, { code: "basic", name: "Basic", price: "59.90" });
    await subs.updatePlan(null, "basic plan", { is_active: false });
    await subs.searchSubscriptions(null, {});
    await subs.searchSubscriptions(null, { q: "Ali Baba", status: "paused", plan: "", before: SUB });
    await subs.getSubscription(null, SUB);
    await subs.createSubscription(null, { contact_id: SUB, plan_code: "basic" });
    await subs.proposeAction(null, SUB, { action: "pause", resume_at: "2026-11-01" });
    await subs.listRequests(null, "open");
    await subs.listRequests(null, "decided", REQ);
    await subs.decideRequest(null, REQ, { decision: "approve", resume_at: null });
  });
  assert.deepEqual(shape(requests), [
    ["GET", "/console/v1/subscriptions/plans"],
    ["GET", "/console/v1/subscriptions/plans?include_archived=true"],
    ["POST", "/console/v1/subscriptions/plans"],
    ["PATCH", "/console/v1/subscriptions/plans/basic%20plan"],
    ["GET", "/console/v1/subscriptions"],
    ["GET", `/console/v1/subscriptions?q=Ali+Baba&status=paused&before=${SUB}`],
    ["GET", `/console/v1/subscriptions/${SUB}`],
    ["POST", "/console/v1/subscriptions"],
    ["POST", `/console/v1/subscriptions/${SUB}/actions`],
    ["GET", "/console/v1/subscriptions/requests?status=open"],
    ["GET", `/console/v1/subscriptions/requests?status=decided&before=${REQ}`],
    ["POST", `/console/v1/subscriptions/requests/${REQ}/decide`],
  ]);
  assert.deepEqual(JSON.parse(requests[3][1].body), { is_active: false });
  assert.deepEqual(JSON.parse(requests[8][1].body), { action: "pause", resume_at: "2026-11-01" });
  assert.deepEqual(JSON.parse(requests[11][1].body), { decision: "approve", resume_at: null });
  for (const [, init] of requests) {
    assert.equal(init.credentials, "same-origin");
    if ((init.method ?? "GET") === "GET") assert.equal(init.headers.has("X-CSRF-Token"), false);
    else assert.equal(init.headers.get("X-CSRF-Token"), "synthetic-csrf");
  }
});

test("platform admins name the tenant first on every call", async () => {
  const tenant = "a b/1";
  const [, requests] = await capture(async () => {
    await subs.listPlans(tenant, true);
    await subs.searchSubscriptions(tenant, { q: "ali", status: "active", plan: "pro" });
    await subs.getSubscription(tenant, SUB);
    await subs.proposeAction(tenant, SUB, { action: "resume" });
    await subs.listRequests(tenant, "open");
    await subs.decideRequest(tenant, REQ, { decision: "decline", note: "No" });
  });
  const query = "client_id=a+b%2F1";
  assert.deepEqual(requests.map(([path]) => path), [
    `/console/v1/subscriptions/plans?${query}&include_archived=true`,
    `/console/v1/subscriptions?${query}&q=ali&status=active&plan=pro`,
    `/console/v1/subscriptions/${SUB}?${query}`,
    `/console/v1/subscriptions/${SUB}/actions?${query}`,
    `/console/v1/subscriptions/requests?${query}&status=open`,
    `/console/v1/subscriptions/requests/${REQ}/decide?${query}`,
  ]);
  assert.equal(requests[3][1].method, "POST");
  assert.equal(requests[3][1].headers.get("X-CSRF-Token"), "synthetic-csrf");
});

test("subscription refusals are explained in plain words and keep their code", async () => {
  const verbatim = {
    plan_unavailable: "That plan is not available for this company.",
    action_not_allowed: "This change is not possible from the subscription's current state.",
    request_decided: "Someone has already decided this request.",
  };
  const explained = [
    "plan_unchanged", "price_override_requires_immediate_change", "renews_at_before_start",
    "renews_at_not_after_current", "request_has_no_subscription", "request_subscription_mismatch",
    "request_has_no_plan", "request_action_mismatch", "resume_at_applies_to_pause_only",
    "request_in_progress", "proposal_stale", "plan_exists", "proposal_not_executed",
  ];
  const original = globalThis.fetch;
  try {
    for (const code of [...Object.keys(verbatim), ...explained]) {
      globalThis.fetch = async () => Response.json({ detail: code }, { status: 422 });
      await assert.rejects(api("/console/v1/subscriptions"), (error) => {
        assert.ok(error instanceof ApiError);
        assert.equal(error.code, code);
        if (code in verbatim) assert.equal(error.message, verbatim[code]);
        assert.notEqual(error.message, code, code);
        assert.doesNotMatch(error.message, /_/, code);
        return true;
      });
    }
  } finally {
    globalThis.fetch = original;
  }
});

test("money and dates follow the writing guide", () => {
  assert.equal(subs.money("59.9", "MYR"), "RM 59.90");
  assert.equal(subs.money(59.9, "MYR"), "RM 59.90");
  assert.equal(subs.money("1234.5", "MYR"), "RM 1,234.50");
  assert.equal(subs.money("10", "USD"), "USD 10.00");
  assert.equal(subs.money(null, "MYR"), "—");
  assert.equal(subs.formatDay("2026-10-27"), "27 Oct 2026");
  assert.equal(subs.formatDay("2026-09-05"), "5 Sep 2026");
  assert.equal(subs.formatDay("2026-10-27T01:15:00Z", "Asia/Kuala_Lumpur"), "27 Oct 2026");
  assert.equal(subs.formatDateTime("2026-10-27T01:15:00Z", "Asia/Kuala_Lumpur"), "27 Oct 2026, 09:15");
  assert.equal(subs.formatDateTime("2026-10-27T15:05:00Z", "Asia/Kuala_Lumpur"), "27 Oct 2026, 23:05");
  assert.equal(subs.formatDay(null), "—");
});

test("timeline events read as plain sentences from action and payload", () => {
  const names = { basic: "Basic", pro: "Pro" };
  const say = (action, payload = {}, actor = "22222222-2222-4222-8222-222222222222") =>
    subs.eventSentence({ id: 1, actor, action, from_status: null, to_status: null, payload,
      created_at: "2026-10-27T01:15:00Z" }, (code) => names[code] ?? code, "Asia/Kuala_Lumpur");
  assert.equal(say("created", { plan: "basic" }), "Subscription started on the Basic plan.");
  assert.equal(say("paused", { resume_at: "2026-11-01", reason: null }), "Paused until 1 Nov 2026.");
  assert.equal(say("paused", { resume_at: null, reason: "Travelling" }),
    "Paused until someone resumes it. Reason: Travelling");
  assert.equal(say("resumed", { old_renews_at: null, new_renews_at: "2026-11-27T01:15:00+00:00" }),
    "Resumed. Next renewal on 27 Nov 2026.");
  assert.equal(say("resumed", { resume_at: "2026-11-01", renews_at: "2026-11-27T01:15:00+00:00" },
    "system:subscriptions_due"), "Resumed automatically on the planned date. Next renewal on 27 Nov 2026.");
  assert.equal(say("plan_changed", { old_plan: "basic", new_plan: "pro", old_price: "59.90",
    new_price: 99, price_override: false }), "Plan changed from Basic (RM 59.90) to Pro (RM 99.00).");
  assert.equal(say("plan_changed", { old_plan: "basic", new_plan: "pro", old_price: "59.90",
    new_price: "49.00", price_override: true, reason: "Loyalty" }),
    "Plan changed from Basic (RM 59.90) to Pro at a special price of RM 49.00. Reason: Loyalty");
  assert.equal(say("plan_changed", { old_plan: "basic", new_plan: "pro", old_price: 59.9,
    new_price: "99.00" }, "system:subscriptions_due"),
    "Scheduled plan change applied: Basic (RM 59.90) to Pro (RM 99.00).");
  assert.equal(say("plan_change_scheduled", { old_plan: "basic", new_plan: "pro",
    effective_at: "2026-12-01T00:00:00+08:00" }), "Plan change from Basic to Pro scheduled for 1 Dec 2026.");
  assert.equal(say("pending_change_skipped", { plan: "pro" }, "system:subscriptions_due"),
    "The scheduled change to Pro was not applied. The plan is archived or the subscription had ended.");
  assert.equal(say("cancel_scheduled", { renews_at: "2026-11-27T01:15:00+00:00" }),
    "Set to cancel when the period ends on 27 Nov 2026.");
  assert.equal(say("cancel_scheduled", { renews_at: null }), "Set to cancel at the end of the period.");
  assert.equal(say("cancelled_at_period_end", {}, "system:subscriptions_due"),
    "Cancelled at the end of the period.");
  assert.equal(say("cancelled", { cleared_pending_plan: null }), "Cancelled straight away.");
  assert.equal(say("cancelled", { cleared_pending_plan: "pro" }),
    "Cancelled straight away. The scheduled change to Pro was dropped.");
  assert.equal(say("renewal_extended", { old_renews_at: "2026-11-27T01:15:00+00:00",
    new_renews_at: "2026-12-27T01:15:00+00:00" }), "Renewal moved from 27 Nov 2026 to 27 Dec 2026.");
  assert.equal(say("renewed", { previous_renews_at: "2026-10-27T01:15:00+00:00",
    renews_at: "2026-11-27T01:15:00+00:00" }, "system:subscriptions_due"),
    "Renewed for another period. Next renewal on 27 Nov 2026.");
  assert.equal(say("something_new", {}), "Subscription updated.");
});

test("action buttons follow the executor's allowed starting states", () => {
  const sub = (status, cancel_at_period_end = false) => ({ status, cancel_at_period_end });
  assert.deepEqual(subs.availableActions(sub("active")),
    ["pause", "change_plan", "cancel_at_period_end", "cancel_now", "extend_renewal"]);
  assert.deepEqual(subs.availableActions(sub("active", true)),
    ["pause", "change_plan", "cancel_now", "extend_renewal"]);
  assert.deepEqual(subs.availableActions(sub("paused")), ["resume", "change_plan", "cancel_now"]);
  assert.deepEqual(subs.availableActions(sub("cancelled")), []);
});
