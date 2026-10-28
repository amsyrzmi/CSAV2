import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

async function load(name, replacements = {}) {
  let source = await readFile(new URL(`../lib/${name}`, import.meta.url), "utf8");
  for (const [from, to] of Object.entries(replacements)) source = source.replaceAll(from, to);
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const view = await import(await load("assistant-view.ts", {
  '"@/lib/navigation"': JSON.stringify(await load("navigation.ts")),
}));

const staff = { role: "tenant_staff", user: { email: "s@example.test", display_name: "S", must_change_password: false }, memberships: [], active_client_id: "t1", csrf_token: "", workspace_assistant: true };
const admin = { ...staff, role: "platform_admin", active_client_id: null };
const all = ["customer_support", "hr", "staff_email", "orders_products"].map((key) => ({ key, status: "active" }));
const proposal = (id, extra = {}) => ({
  id, kind: "email_export", status: "requested", result: null, created_at: "2026-10-26T00:00:00Z",
  expires_at: "2026-10-26T01:00:00Z",
  card: { proposal_id: id, kind: "email_export", title: "T", summary: "S", fields: [], expires_at: "2026-10-26T01:00:00Z", expected_version: null },
  ...extra,
});

test("the dock renders only for writers with a tenant; admins need ?client_id=", () => {
  assert.equal(view.dockTenant(staff, null), "t1");
  assert.equal(view.dockTenant({ ...staff, role: "tenant_admin" }, "other"), "t1");
  assert.equal(view.dockTenant({ ...staff, role: "read_only" }, "t1"), null);
  assert.equal(view.dockTenant({ ...staff, role: null }, null), null);
  assert.equal(view.dockTenant({ ...staff, active_client_id: null }, null), null);
  assert.equal(view.dockTenant(admin, null), null);
  assert.equal(view.dockTenant(admin, "  "), null);
  assert.equal(view.dockTenant(admin, "t9"), "t9");
});

test("the dock stays hidden while the gateway's workspace assistant flag is off", () => {
  assert.equal(view.dockTenant({ ...staff, workspace_assistant: false }, null), null);
  assert.equal(view.dockTenant({ ...staff, workspace_assistant: undefined }, null), null);
  assert.equal(view.dockTenant({ ...admin, workspace_assistant: false }, "t9"), null);
});

test("tab routes follow the shell navigation and keep the admin tenant", () => {
  assert.equal(view.tabHref("inbox", staff, all, "t1"), "/portal/inbox");
  assert.equal(view.tabHref("overview", staff, all, "t1"), "/portal");
  assert.equal(view.tabHref("knowledge_base", staff, all, "t1"), "/portal/faq");
  assert.equal(view.tabHref("hr", staff, all, "t1", "training"), "/portal/hr?section=training");
  assert.equal(view.tabHref("knowledge_base", admin, [], "t /9"), "/knowledge-base?client_id=t+%2F9");
  assert.equal(view.tabHref("overview", admin, [], "t9"), "/?client_id=t9");
  assert.equal(view.tabHref("email", staff, [], "t1"), null, "a locked tab is not offered");
  for (const tab of ["overview", "services", "inbox", "knowledge_base", "business", "hr", "email", "activity", "chat"])
    assert.ok(view.tabHref(tab, staff, all, "t1"), tab);
});

test("the route names the current tab; published context wins", () => {
  assert.equal(view.tabForPath("/portal/faq", staff, all), "knowledge_base");
  assert.equal(view.tabForPath("/hr", admin, []), "hr");
  assert.equal(view.tabForPath("/portal/team", { ...staff, role: "tenant_admin" }, all), null);
  assert.equal(view.tabLabel("inbox", staff, all), "Customer inbox");
  const inbox = { tab: "inbox", record: { kind: "conversation", id: "c1" } };
  assert.deepEqual(view.effectiveContext(inbox, "hr"), inbox);
  assert.deepEqual(view.effectiveContext(null, "hr"), { tab: "hr" });
  assert.equal(view.effectiveContext(null, null), null);
});

test("a resumed turn waits for the target tab's context, then falls back to the bare tab", () => {
  const target = { tab: "hr", section: "training" };
  assert.equal(view.resumeContext({ tab: "inbox" }, target, false), null);
  assert.equal(view.resumeContext(null, target, false), null);
  const hr = { tab: "hr", section: "training", record: { kind: "training_request", id: "r1" } };
  assert.deepEqual(view.resumeContext(hr, target, false), hr);
  assert.deepEqual(view.resumeContext({ tab: "inbox" }, target, true), { tab: "hr", section: "training" });
  assert.deepEqual(view.resumeContext(null, { tab: "hr", section: null }, true), { tab: "hr" });
});

test("results map onto views", () => {
  assert.equal(view.resultView({ status: "denied", code: "resource_unavailable" }).error, true);
  assert.equal(view.resultView({ status: "unavailable", code: "x", retryable: true }).kind, "notice");
  assert.match(view.resultView({ status: "completed", action: "page_guidance", page: "workspace_home", instruction_key: "workspace_home_help", source: "application_guide", as_of: "x" }).text, /internal workspace/);
  assert.deepEqual(view.resultView({ action: "answer", markdown: "**hi**" }), { kind: "answer", text: "**hi**" });
  assert.deepEqual(view.resultView({ action: "navigate", tab: "hr", section: null, record: null, resume: true }), { kind: "navigate", tab: "hr", resume: true });
  const p = proposal("p1");
  assert.deepEqual(view.resultView({ action: "proposal", proposal: p }), { kind: "proposal", proposal: p });
  const person = { employee_id: "e1", display_name: "A", job_title: null, work_email: null };
  assert.deepEqual(view.resultView({ action: "directory_lookup", candidates: [person] }), { kind: "directory", candidates: [person] });
  assert.equal(view.resultView({ action: "directory_lookup", candidates: [] }).kind, "notice");
  assert.equal(view.resultView({ action: "clarification", question: "Which?", options: [] }).question, "Which?");
  assert.equal(
    view.candidateFollowUp(person, " email Ali about the roster "),
    "Use employee e1 for: email Ali about the roster",
  );
  assert.equal(view.candidateFollowUp(person, "x".repeat(1200)).length, "Use employee e1 for: ".length + 1001);
  assert.equal(
    view.clarificationAnswer({ label: "HR", value: "hr" }, "Which tab?", "take me there"),
    "hr\n(Answer to “Which tab?” for: take me there)",
  );
});

test("open proposals honour local outcomes and expiry", () => {
  const turn = (id, result) => ({ id, request_key: "k", body: "b", created_at: "x", duplicate: false, result });
  const turns = [
    turn(1, { action: "proposal", proposal: proposal("a") }),
    turn(2, { action: "proposal", proposal: proposal("b") }),
    turn(3, { action: "proposal", proposal: proposal("c", { expires_at: "2026-10-25T00:00:00Z" }) }),
    turn(4, { action: "answer", markdown: "x" }),
    turn(5, { status: "denied", code: "x" }),
  ];
  const now = Date.parse("2026-10-26T00:30:00Z");
  assert.deepEqual(view.openProposals(turns, {}, now).map((p) => p.id), ["a", "b"]);
  assert.deepEqual(view.openProposals(turns, { b: proposal("b", { status: "cancelled" }) }, now).map((p) => p.id), ["a"]);
});

test("settled proposals summarise their receipts", () => {
  assert.match(view.resultSummary(proposal("p", { status: "executed", result: { subject: "Hi", recipient: "a@b.test" } })), /“Hi”.*a@b\.test.*Nothing was sent/);
  assert.match(view.resultSummary(proposal("p", { status: "executed", result: null })), /Gmail drafts/);
  assert.equal(view.resultSummary(proposal("p", { kind: "training_submit", status: "executed", result: { request: { title: "AWS", status: "pending_approval" } } })), "Submitted “AWS” (now pending approval).");
  assert.equal(view.resultSummary(proposal("p", { kind: "training_submit", status: "executed", result: {} })), "Training request submitted.");
  assert.match(view.resultSummary(proposal("p", { kind: "inbox_decision", status: "executed" })), /recorded/);
  assert.equal(view.resultSummary(proposal("p", { kind: "inbox_decision", status: "executed", result: { outcome: "applied", queued: true } })), "Your decision on the held reply was recorded. Reply queued for delivery.");
  assert.equal(view.resultSummary(proposal("p", { kind: "inbox_decision", status: "executed", result: { outcome: "expired", queued: false } })), "Approval expired. No reply queued.");
  assert.equal(view.resultSummary(proposal("p", { kind: "inbox_decision", status: "executed", result: { outcome: "already_handled", queued: false } })), "Already handled. Nothing was changed.");
  assert.equal(view.resultSummary(proposal("p", { kind: "subscription_action", status: "executed", result: { status: "paused", version: 2 } })), "Subscription updated. It is now paused.");
  assert.equal(view.resultSummary(proposal("p", { kind: "subscription_action", status: "executed", result: null })), "Subscription updated.");
  assert.match(view.resultSummary(proposal("p", { status: "cancelled" })), /Nothing was changed/);
  assert.match(view.resultSummary(proposal("p", { status: "expired" })), /expired/);
});
