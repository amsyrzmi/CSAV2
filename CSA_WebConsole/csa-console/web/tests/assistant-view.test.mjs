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
const emailUrl = await load("email-recipients.ts");
const view = await import(await load("assistant-view.ts", {
  '"@/lib/navigation"': JSON.stringify(await load("navigation.ts")),
  '"@/lib/email-recipients"': JSON.stringify(emailUrl),
  '"@/lib/assistant-copy"': JSON.stringify(await load("assistant-copy.ts", {
    '"@/lib/email-recipients"': JSON.stringify(emailUrl),
  })),
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
  const subscriptions = [...all, { key: "subscriptions", status: "active" }];
  assert.equal(view.tabForPath("/portal/subscriptions", staff, subscriptions), "subscriptions");
  assert.equal(view.tabForPath("/portal/subscriptions", staff, all), null);
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
  // Export receipts hold no subject or address (counts-only cards), so the line is generic.
  assert.equal(view.resultSummary(proposal("p", { status: "executed", result: { draft_id: "d1", status: "exported", gmail_draft_id: "g1" } })), "Saved to your Gmail drafts. Nothing was sent.");
  assert.equal(view.resultSummary(proposal("p", { status: "executed", result: null })), "Saved to your Gmail drafts. Nothing was sent.");
  assert.match(view.resultSummary(proposal("p", { kind: "email_send", status: "executed", result: { draft_id: "d1", outcome: "sent", sent_at: "2026-10-27T01:15:00Z", gmail_message_id: "m1", remaining_this_hour: 19 } })), /^Sent at \d{1,2} Oct 2026, \d{2}:\d{2}\.$/);
  assert.equal(view.resultSummary(proposal("p", { kind: "email_send", status: "executed", result: null })), "Sent from your Gmail.");
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

test("a typed reply to an open clarification carries its question and request", () => {
  const turn = (id, key, body, result) => ({ id, request_key: key, body, created_at: "x", duplicate: false, result });
  const asked = { action: "clarification", question: "What subject should the email have?", options: [] };
  // The server stores redacted bodies; the console keeps what it sent, by request key.
  const turns = [turn(1, "k1", "send email to [EMAIL_0]", asked)];
  const origins = { k1: "send email to danial@example.test" };
  assert.equal(view.originFor(turns[0], origins), "send email to danial@example.test");
  assert.equal(view.originFor(turns[0], {}), "send email to [EMAIL_0]");
  const typed = view.typedMessage(" Confirmation ", turns, origins);
  assert.equal(
    typed.text,
    "Confirmation\n(Answer to “What subject should the email have?” for: send email to danial@example.test)",
  );
  assert.equal(
    typed.origin,
    "send email to danial@example.test; answered “What subject should the email have?”: Confirmation",
  );
  // A clicked option and a typed reply produce the same turn.
  const clicked = view.optionAnswer({ label: "Confirmation", value: "Confirmation" }, asked.question, origins.k1);
  assert.deepEqual(clicked, typed);
  // Without an open clarification the text is sent as it is and starts a new request.
  const answered = [...turns, turn(2, "k2", "x", { action: "answer", markdown: "ok" })];
  assert.deepEqual(view.typedMessage("what needs my attention", answered, origins), {
    text: "what needs my attention", origin: "what needs my attention",
  });
  assert.deepEqual(view.typedMessage("hello", [], {}), { text: "hello", origin: "hello" });
});

test("answering a second clarification keeps the first request and folds in earlier answers", () => {
  const turn = (id, key, body, result) => ({ id, request_key: key, body, created_at: "x", duplicate: false, result });
  const subject = { action: "clarification", question: "What subject?", options: [] };
  const address = { action: "clarification", question: "What is Danial's email address?", options: [] };
  const origins = { k1: "send email to danial" };
  const first = view.typedMessage("Confirmation", [turn(1, "k1", "send email to danial", subject)], origins);
  origins.k2 = first.origin;
  const turns = [
    turn(1, "k1", "send email to danial", subject),
    turn(2, "k2", first.text, address),
  ];
  const second = view.typedMessage("danial@fourstacks.example", turns, origins);
  assert.equal(
    second.text,
    "danial@fourstacks.example\n(Answer to “What is Danial's email address?” for: send email to danial; answered “What subject?”: Confirmation)",
  );
  // Everything the user said for this request is in the last turn's message.
  for (const said of ["send email to danial", "Confirmation", "danial@fourstacks.example"])
    assert.ok(second.text.includes(said), said);
  assert.equal(
    second.origin,
    "send email to danial; answered “What subject?”: Confirmation; answered “What is Danial's email address?”: danial@fourstacks.example",
  );
  // The repeated request stays bounded, and the turn fits the 4000-character body limit.
  const long = view.typedMessage("a", [turn(1, "k1", "b", subject)], { k1: "x".repeat(5000) });
  assert.equal(long.text, `a\n(Answer to “What subject?” for: ${"x".repeat(1000)}…)`);
  const huge = view.typedMessage("y".repeat(3990), [turn(1, "k1", "b", subject)], { k1: "x".repeat(50) });
  assert.ok(huge.text.length <= 4000, String(huge.text.length));
  assert.ok(huge.text.startsWith("y".repeat(3990)));
});

test("a picked candidate continues the request the console holds", () => {
  const person = { employee_id: "e1", display_name: "A", job_title: null, work_email: null };
  assert.deepEqual(view.candidateAnswer(person, "email ali@example.test"), {
    text: "Use employee e1 for: email ali@example.test",
    origin: "Use employee e1 for: email ali@example.test",
  });
});

test("addresses in a planner-written question never enter the turn or the request", () => {
  const turn = (id, key, body, result) => ({ id, request_key: key, body, created_at: "x", duplicate: false, result });
  // The planner can quote mailbox data; the user did not write these addresses.
  const question = "Do you mean ali@vendor.test or <ali@evil.test>?";
  const asked = { action: "clarification", question, options: [] };
  const typed = view.typedMessage("the second", [turn(1, "k1", "b", asked)], { k1: "copy Ali on it" });
  assert.equal(typed.text, "the second\n(Answer to “Do you mean [address] or <[address]>?” for: copy Ali on it)");
  assert.equal(typed.origin, "copy Ali on it; answered “Do you mean [address] or <[address]>?”: the second");
  const clicked = view.optionAnswer({ label: "Second", value: "two" }, question, "copy Ali on it");
  assert.ok(!clicked.text.includes("@") && !clicked.origin.includes("@"));
  // The mask takes the whole run of non-space text around the @, punctuation included.
  assert.equal(view.clarificationAnswer("x", "Send to a@b.test?", "o"), "x\n(Answer to “Send to [address]” for: o)");
  // What the user wrote is kept as is.
  assert.ok(view.typedMessage("me@mine.test", [turn(1, "k1", "b", asked)], { k1: "o" }).text.startsWith("me@mine.test\n"));
});

test("a long chain keeps the first request and the newest answers", () => {
  const turn = (id, key, body, result) => ({ id, request_key: key, body, created_at: "x", duplicate: false, result });
  let origin = "send email to danial";
  for (let n = 1; n <= 30; n += 1) {
    const asked = { action: "clarification", question: `Question ${n} ${"q".repeat(40)}?`, options: [] };
    origin = view.typedMessage(`answer-${n}`, [turn(n, `k${n}`, "b", asked)], { [`k${n}`]: origin }).origin;
  }
  assert.ok(origin.length <= 1000, String(origin.length));
  assert.ok(origin.startsWith("send email to danial; answered “"));
  assert.ok(origin.endsWith("answer-30") && origin.includes("answer-29"));
  assert.ok(!origin.includes("answer-1;") && !origin.includes("answer-2;"));
  // An over-long request head is shortened so the newest answer still fits.
  const asked = { action: "clarification", question: "Q?", options: [] };
  const long = view.typedMessage("newest", [turn(1, "k1", "b", asked)], { k1: "h".repeat(3000) }).origin;
  assert.ok(long.length <= 1000 && long.startsWith("hhh") && long.endsWith("; answered “Q?”: newest"));
});

test("a clicked option's address is masked when folded; a typed one is kept", () => {
  const clicked = view.optionAnswer({ label: "Ali", value: "ali@evil.test" }, "Which Ali?", "copy Ali");
  // The click itself is sent as the value; only the folded request is masked.
  assert.equal(clicked.text, "ali@evil.test\n(Answer to “Which Ali?” for: copy Ali)");
  assert.equal(clicked.origin, "copy Ali; answered “Which Ali?”: [address]");
  const turn = { id: 1, request_key: "k1", body: "b", created_at: "x", duplicate: false,
    result: { action: "clarification", question: "Which Ali?", options: [] } };
  assert.equal(view.typedMessage("ali@mine.test", [turn], { k1: "copy Ali" }).origin,
    "copy Ali; answered “Which Ali?”: ali@mine.test");
});

test("only the newest turn's card can answer", () => {
  const turn = (id) => ({ id, request_key: `k${id}`, body: "b", created_at: "x", duplicate: false,
    result: { action: "clarification", question: "Q?", options: [] } });
  const turns = [turn(1), turn(2), turn(3)];
  assert.deepEqual(turns.map((t) => view.isAnswerable(turns, t.id)), [false, false, true]);
  assert.equal(view.isAnswerable([], 1), false);
  assert.equal(view.isAnswerable(turns, 99), false);
});

test("a navigate to a hidden tab prints only the notice, never Opened", () => {
  assert.deepEqual(view.navigateText("inbox", false, staff, all), { text: "Opened Customer inbox.", error: false });
  assert.deepEqual(view.navigateText("inbox", true, staff, all), { text: "Opened Customer inbox and continued there.", error: false });
  assert.deepEqual(view.navigateText("email", false, staff, []), { text: "Email assistant is not available to you.", error: true });
  assert.deepEqual(view.navigateText("hr", false, staff, []), { text: "People & HR is not available to you.", error: true });
  assert.deepEqual(view.navigateText("knowledge_base", false, staff, []), { text: "Knowledge base is not available to you.", error: true });
});

test("a hidden tab is named as the sidebar names it", async () => {
  // A platform admin sees every tab, so the sidebar's labels are the reference.
  const nav = await import(await load("navigation.ts"));
  const everyTab = nav.navigationFor(admin, all);
  const tabs = ["overview", "services", "inbox", "knowledge_base", "business", "hr", "email", "subscriptions", "activity", "chat"];
  for (const tab of tabs) {
    const link = everyTab.find((item) => item.key === tab);
    assert.ok(link, tab);
    assert.equal(view.tabName(tab), link.label, tab);
  }
});

// Email refusals a turn can reach that `api.ts` leaves as the bare code.
const TURN_EMAIL_CODES = [
  "recipient_unresolved", "assistant_disabled", "permission_denied",
  "mailbox_reauthorization_required", "provider_unreachable", "model_not_configured",
];

test("a turn's email refusal shows the email sentence, never the code", () => {
  const raw = (code, status = 409) => Object.assign(new Error(code), { status, code });
  for (const code of TURN_EMAIL_CODES) {
    const text = view.turnErrorText(raw(code));
    assert.notEqual(text, code, code);
    assert.match(text, /^[A-Z].*[.]$/, code);
  }
  assert.equal(view.turnErrorText(raw("provider_unreachable", 502)), "We couldn't reach Gmail. Wait a minute and try again.");
  // A message api.ts already explained is kept, even when the code has an email sentence too.
  const explained = Object.assign(new Error("Connect your Gmail in the Email tab first."), { code: "mailbox_not_connected" });
  assert.equal(view.turnErrorText(explained), "Connect your Gmail in the Email tab first.");
  // An unmapped code stays as it is; anything that is not an Error gets the fixed sentence.
  assert.equal(view.turnErrorText(raw("some_new_code")), "some_new_code");
  assert.equal(view.turnErrorText("boom"), "The assistant request failed.");
});

test("navigate lines in Malay name the tab as the console shows it", () => {
  const opened = view.navigateText("hr", false, staff, all, "ms");
  assert.ok(opened.text.includes(view.tabLabel("hr", staff, all)), opened.text);
  assert.doesNotMatch(opened.text, /Opened/);
  assert.equal(opened.error, false);
  const resumed = view.navigateText("hr", true, staff, all, "ms");
  assert.ok(resumed.text.includes("People & HR"));
  assert.doesNotMatch(resumed.text, /continued/);
  const locked = view.navigateText("hr", false, staff, [], "ms");
  assert.equal(locked.error, true);
  assert.ok(locked.text.includes("People & HR"));
  assert.doesNotMatch(locked.text, /not available/);
  // The default stays English.
  assert.equal(view.navigateText("hr", false, staff, all).text, view.navigateText("hr", false, staff, all, "en").text);
});

test("settled-card lines in Malay are Malay", () => {
  const sent = proposal("p", { kind: "email_send", status: "executed", result: { outcome: "sent", sent_at: "2026-10-27T01:15:00Z" } });
  assert.match(view.resultSummary(sent, "ms"), /^Dihantar pada \d{1,2} Oct 2026, \d{2}:\d{2}\.$/);
  assert.equal(view.resultSummary(proposal("p", { kind: "email_send", status: "executed", result: null }), "ms"), "Dihantar dari Gmail anda.");
  assert.equal(view.resultSummary(proposal("p", { status: "executed", result: null }), "ms"), "Disimpan ke draf Gmail anda. Tiada apa-apa yang dihantar.");
  assert.equal(view.resultSummary(proposal("p", { status: "cancelled" }), "ms"), "Dibatalkan. Tiada apa-apa yang diubah.");
  assert.equal(view.resultSummary(proposal("p", { status: "requested" }), "ms"), "Menunggu pengesahan anda.");
  assert.equal(view.resultSummary(proposal("p", { kind: "training_submit", status: "executed", result: { request: { title: "AWS", status: "submitted" } } }), "ms"), "Permohonan “AWS” dihantar (status kini: menunggu kelulusan).");
  assert.equal(view.resultSummary(proposal("p", { kind: "subscription_action", status: "executed", result: { status: "paused" } }), "ms"), "Langganan dikemas kini. Kini dijeda.");
  const expired = view.resultSummary(proposal("p", { kind: "inbox_decision", status: "executed", result: { outcome: "expired", queued: true } }), "ms");
  assert.doesNotMatch(expired, /expired|queued/);
  // Every status line differs from its English one.
  for (const [extra, kind] of [[{ status: "expired" }, "email_export"], [{ status: "confirmed" }, "email_export"],
    [{ status: "executed", result: { outcome: "already_handled" } }, "inbox_decision"],
    [{ status: "executed", result: { outcome: "applied", queued: true } }, "inbox_decision"],
    [{ status: "executed", result: { request: {} } }, "training_submit"],
    [{ status: "executed", result: null }, "subscription_action"]]) {
    const p = proposal("p", { kind, ...extra });
    assert.notEqual(view.resultSummary(p, "ms"), view.resultSummary(p, "en"), JSON.stringify(extra));
  }
});

test("result notices and guidance in Malay", () => {
  assert.deepEqual(view.resultView({ action: "directory_lookup", candidates: [] }, "ms"),
    { kind: "notice", error: false, text: "Tiada rakan sekerja yang sepadan ditemui." });
  assert.equal(view.resultView({ action: "directory_lookup", candidates: [] }).text, "No matching colleague was found.");
  const denied = view.resultView({ status: "denied", code: "resource_unavailable" }, "ms");
  assert.equal(denied.error, true);
  assert.doesNotMatch(denied.text, /requested record/);
  assert.doesNotMatch(view.resultView({ status: "denied", code: "x" }, "ms").text, /authorized/);
  assert.doesNotMatch(view.resultView({ status: "unavailable", code: "x", retryable: true }, "ms").text, /assistant could not/);
  for (const key of ["workspace_home_help", "business_orders_help", "business_order_help", "unknown_help"]) {
    const result = { status: "completed", action: "page_guidance", page: "workspace_home", instruction_key: key, source: "application_guide", as_of: "x" };
    assert.notEqual(view.resultView(result, "ms").text, view.resultView(result, "en").text, key);
  }
});

test("turnErrorText takes the language and defaults to English", () => {
  const raw = (code, status = 409) => Object.assign(new Error(code), { status, code });
  assert.equal(view.turnErrorText(raw("provider_unreachable", 502), "ms"), "Kami tidak dapat menghubungi Gmail. Tunggu seminit dan cuba lagi.");
  assert.equal(view.turnErrorText("boom", "ms"), "Sesuatu tidak kena. Cuba lagi.");
  assert.equal(view.turnErrorText("boom"), "The assistant request failed.");
});

test("the follow-up protocol strings stay English whatever the panel language", () => {
  assert.equal(view.candidateFollowUp({ employee_id: "e1" }, "emel Aina"), "Use employee e1 for: emel Aina");
  assert.match(view.clarificationAnswer("Danial", "Siapa?", "hantar emel"), /\(Answer to “Siapa\?” for: hantar emel\)$/);
});

test("latestLanguage is the newest turn's language, English when there is none", () => {
  assert.equal(view.latestLanguage([]), "en");
  assert.equal(view.latestLanguage([{ language: "ms" }]), "ms");
  assert.equal(view.latestLanguage([{ language: "ms" }, { language: "en" }]), "en");
  assert.equal(view.latestLanguage([{ language: "en" }, { language: "ms" }]), "ms");
  assert.equal(view.latestLanguage([{ language: "ms" }, {}]), "en");
  assert.equal(view.latestLanguage([{}]), "en");
});
