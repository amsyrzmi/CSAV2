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
const queue = await import(moduleUrl(
  await readFile(new URL("../lib/inbox-queue.ts", import.meta.url), "utf8"),
));
const {
  TABS, tabFilters, queueListQuery, activeFilterChips, waitText, dueState, dueText, rowMeta,
  primaryAction, snoozePresets, actionErrorText, bulkSummary, initialsOf,
} = queue;

const BASE = "/console/v1/inbox/conversations";
const NOW = Date.parse("2026-11-17T04:00:00Z"); // Tuesday 12:00 in +08:00
const MIN = 60_000;
const HOUR = 60 * MIN;
const ago = (ms) => new Date(NOW - ms).toISOString();
const ahead = (ms) => new Date(NOW + ms).toISOString();
const tab = (id) => TABS.find((t) => t.id === id);

function item(over = {}) {
  return {
    id: "c1", channel: "telegram", status: "escalated", owned_by: null, ai_muted_until: null,
    human_muted: false, inbox_version: 3, opened_at: ago(5 * HOUR), contact: null,
    last_message: null, pending_approvals: 0,
    queue_status: "needs_staff", queue_reason: "escalation", topic: null, priority: "normal",
    assignee_id: null, assignee: null, claimed_at: null, needs_staff_since: ago(HOUR),
    due_at: ahead(HOUR), snoozed_until: null, last_customer_at: ago(HOUR), last_staff_reply_at: null,
    ...over,
  };
}

// ---- tabs ----

test("the tabs are the eight in the design, in order, with sentence-case labels and test ids", () => {
  assert.deepEqual(
    TABS.map((t) => [t.id, t.label, t.testId]),
    [
      ["needs_us", "Needs us", "inbox-tab-needs_us"],
      ["mine", "Mine", "inbox-tab-mine"],
      ["unassigned", "Unassigned", "inbox-tab-unassigned"],
      ["overdue", "Overdue", "inbox-tab-overdue"],
      ["waiting", "Waiting on customer", "inbox-tab-waiting"],
      ["snoozed", "Snoozed", "inbox-tab-snoozed"],
      ["resolved", "Resolved", "inbox-tab-resolved"],
      ["all", "All", "inbox-tab-all"],
    ],
  );
});

test("each tab carries its server filters, default order and count key", () => {
  const table = {
    needs_us: [{ state: ["needs_staff"] }, "due", "needs_us"],
    mine: [{ assignee: "me", state: ["needs_staff", "waiting_customer"] }, "due", "mine"],
    unassigned: [{ state: ["needs_staff"], assignee: "none" }, "due", "unassigned"],
    overdue: [{ state: ["needs_staff"], overdue: true }, "due", "overdue"],
    waiting: [{ state: ["waiting_customer"] }, "activity", "waiting_customer"],
    snoozed: [{ state: ["snoozed"] }, "activity", "snoozed"],
    resolved: [{ state: ["resolved"] }, "activity", null],
    all: [{}, "activity", null],
  };
  for (const [id, [filters, order, count]] of Object.entries(table)) {
    assert.deepEqual(tab(id).filters, filters, id);
    assert.equal(tab(id).order, order, id);
    assert.equal(tab(id).count, count, id);
  }
});

test("tabFilters lays the tab's own filters over empty ones", () => {
  assert.deepEqual(tabFilters("all"), {
    state: [], assignee: "", priorityMin: "", reason: [], topic: [], channel: "", sender: "",
    overdue: false, waitingOver: null,
  });
  assert.deepEqual(tabFilters("mine"), {
    state: ["needs_staff", "waiting_customer"], assignee: "me", priorityMin: "", reason: [], topic: [],
    channel: "", sender: "", overdue: false, waitingOver: null,
  });
  assert.equal(tabFilters("overdue").overdue, true);
  // Each call returns fresh arrays, so a caller cannot change the tab definition.
  tabFilters("needs_us").state.push("snoozed");
  assert.deepEqual(tabFilters("needs_us").state, ["needs_staff"]);
});

// ---- queueListQuery ----

const none = { tenantQuery: "", q: "" };
const listFor = (id, extra = {}) => queueListQuery({
  ...none, filters: tabFilters(id), order: tab(id).order, ...extra,
});

test("the list query for each tab", () => {
  assert.equal(listFor("needs_us"), `${BASE}?state=needs_staff&order=due`);
  assert.equal(listFor("mine"), `${BASE}?state=needs_staff&state=waiting_customer&assignee=me&order=due`);
  assert.equal(listFor("unassigned"), `${BASE}?state=needs_staff&assignee=none&order=due`);
  assert.equal(listFor("overdue"), `${BASE}?state=needs_staff&overdue=true&order=due`);
  assert.equal(listFor("waiting"), `${BASE}?state=waiting_customer&order=activity`);
  assert.equal(listFor("snoozed"), `${BASE}?state=snoozed&order=activity`);
  assert.equal(listFor("resolved"), `${BASE}?state=resolved&order=activity`);
  assert.equal(listFor("all"), `${BASE}?order=activity`);
});

test("the tenant query comes first and the other parameters follow in a fixed order", () => {
  assert.equal(
    listFor("needs_us", { tenantQuery: "client_id=abc" }),
    `${BASE}?client_id=abc&state=needs_staff&order=due`,
  );
  const filters = {
    state: ["needs_staff", "snoozed"], assignee: "u-9", priorityMin: "high",
    reason: ["escalation", "manual"], topic: ["complaint", "payment"], channel: "whatsapp",
    sender: "new", overdue: true, waitingOver: 90,
  };
  assert.equal(
    queueListQuery({ tenantQuery: "client_id=abc", filters, order: "waiting", q: "ali" }),
    `${BASE}?client_id=abc&state=needs_staff&state=snoozed&assignee=u-9&priority_min=high`
      + "&reason=escalation&reason=manual&topic=complaint&topic=payment&channel=whatsapp&sender=new"
      + "&overdue=true&waiting_over=90&order=waiting&q=ali",
  );
});

test("search under two characters is dropped, two or more is sent trimmed and encoded", () => {
  assert.equal(listFor("all", { q: "a" }), `${BASE}?order=activity`);
  assert.equal(listFor("all", { q: " a " }), `${BASE}?order=activity`);
  assert.equal(listFor("all", { q: "   " }), `${BASE}?order=activity`);
  assert.equal(listFor("all", { q: "ab" }), `${BASE}?order=activity&q=ab`);
  assert.equal(listFor("all", { q: " Aina Demo " }), `${BASE}?order=activity&q=Aina%20Demo`);
  // Two code points count as two characters even when they are astral.
  assert.equal(listFor("all", { q: "😀😀" }), `${BASE}?order=activity&q=${encodeURIComponent("😀😀")}`);
  assert.equal(listFor("all", { q: "😀" }), `${BASE}?order=activity`);
});

test("values are URL-encoded and needs_review is never sent", () => {
  const url = queueListQuery({
    ...none, order: "activity", filters: { ...tabFilters("all"), assignee: "a b&c" },
  });
  assert.equal(url, `${BASE}?assignee=a%20b%26c&order=activity`);
  assert.ok(!url.includes("needs_review"));
});

// ---- waitText / dueText / dueState ----

test("waitText boundaries", () => {
  const at = (ms) => waitText(ago(ms), NOW);
  assert.equal(at(59_000), "just now");
  assert.equal(at(0), "just now");
  assert.equal(at(MIN), "1m");
  assert.equal(at(35 * MIN), "35m");
  assert.equal(at(59 * MIN), "59m");
  assert.equal(at(HOUR), "1h");
  assert.equal(at(2 * HOUR + 10 * MIN), "2h 10m");
  assert.equal(at(2 * HOUR), "2h");
  assert.equal(at(23 * HOUR + 59 * MIN), "23h 59m");
  assert.equal(at(24 * HOUR), "1d");
  assert.equal(at(3 * 24 * HOUR + 7 * HOUR), "3d");
  assert.equal(waitText(ahead(5 * MIN), NOW), "just now");
  assert.equal(waitText(null, NOW), "");
  assert.equal(waitText("not a date", NOW), "");
});

test("dueText says how long until or past the due time", () => {
  const due = (ms) => dueText(item({ due_at: new Date(NOW + ms).toISOString() }), NOW);
  assert.equal(due(20 * MIN), "Due in 20m");
  assert.equal(due(2 * HOUR + 10 * MIN), "Due in 2h 10m");
  assert.equal(due(3 * 24 * HOUR), "Due in 3d");
  assert.equal(due(-HOUR), "Overdue 1h");
  assert.equal(due(-35 * MIN), "Overdue 35m");
  assert.equal(due(-(2 * HOUR + 10 * MIN)), "Overdue 2h 10m");
  assert.equal(due(-30_000), "Overdue just now");
  assert.equal(due(30_000), "Due in under 1m");
  assert.equal(dueText(item({ due_at: null }), NOW), "");
});

test("dueState: none without dates, soon in the last quarter of the window, overdue at and after the due time", () => {
  const window = 100 * MIN;
  const at = (percentElapsed) => dueState(
    { needs_staff_since: new Date(NOW - (window * percentElapsed) / 100).toISOString(),
      due_at: new Date(NOW + (window * (100 - percentElapsed)) / 100).toISOString() },
    NOW,
  );
  assert.equal(at(0), "ok");
  assert.equal(at(74), "ok");
  assert.equal(at(75), "soon"); // exactly 25 percent remaining
  assert.equal(at(99), "soon");
  assert.equal(at(100), "overdue"); // 0 percent remaining
  assert.equal(at(120), "overdue");
  assert.equal(dueState({ needs_staff_since: null, due_at: ahead(HOUR) }, NOW), "none");
  assert.equal(dueState({ needs_staff_since: ago(HOUR), due_at: null }, NOW), "none");
  assert.equal(dueState({ needs_staff_since: null, due_at: null }, NOW), "none");
});

// ---- initialsOf ----

test("initialsOf", () => {
  assert.equal(initialsOf("Aina Demo"), "AD");
  assert.equal(initialsOf("aina"), "A");
  assert.equal(initialsOf("  aina   binti demo "), "AB");
  assert.equal(initialsOf(null), "?");
  assert.equal(initialsOf(""), "?");
  assert.equal(initialsOf("   "), "?");
});

// ---- rowMeta ----

const me = { userId: "u-me" };

test("rowMeta for a needs_staff item shows state, due, reason and nothing else when unclaimed and normal", () => {
  const meta = rowMeta(item(), NOW, me);
  assert.deepEqual(meta.state, { tone: "attention", text: "Needs us" });
  assert.deepEqual(meta.due, { state: "ok", text: "Due in 1h" });
  assert.equal(meta.priority, null);
  assert.equal(meta.reason, "Escalated");
  assert.equal(meta.assignee, null);
});

test("rowMeta reasons in plain words", () => {
  const reason = (queue_reason) => rowMeta(item({ queue_reason }), NOW, me).reason;
  assert.equal(reason("escalation"), "Escalated");
  assert.equal(reason("held_reply"), "Held reply");
  assert.equal(reason("customer_message"), "Customer wrote");
  assert.equal(reason("manual"), "Taken over");
  assert.equal(reason(null), null);
});

test("rowMeta shows priority only for high and urgent", () => {
  const priority = (p) => rowMeta(item({ priority: p }), NOW, me).priority;
  assert.equal(priority("low"), null);
  assert.equal(priority("normal"), null);
  assert.deepEqual(priority("high"), { text: "High" });
  assert.deepEqual(priority("urgent"), { text: "Urgent" });
});

test("rowMeta due state follows the window and shows overdue in words", () => {
  const meta = rowMeta(item({ needs_staff_since: ago(3 * HOUR), due_at: ago(HOUR) }), NOW, me);
  assert.deepEqual(meta.due, { state: "overdue", text: "Overdue 1h" });
  const soon = rowMeta(item({ needs_staff_since: ago(HOUR), due_at: ahead(10 * MIN) }), NOW, me);
  assert.deepEqual(soon.due, { state: "soon", text: "Due in 10m" });
});

test("rowMeta for the other states", () => {
  const waiting = rowMeta(item({
    queue_status: "waiting_customer", queue_reason: null, needs_staff_since: null, due_at: null,
  }), NOW, me);
  assert.deepEqual(waiting.state, { tone: "neutral", text: "Waiting on customer" });
  assert.equal(waiting.due, null);
  assert.equal(waiting.reason, null);

  const snoozed = rowMeta(item({
    queue_status: "snoozed", queue_reason: null, needs_staff_since: null, due_at: null,
    snoozed_until: "2026-11-19T01:00:00Z",
  }), NOW, me);
  assert.deepEqual(snoozed.state, { tone: "info", text: "Snoozed until Thu 09:00" });
  assert.equal(snoozed.due, null);

  const resolved = rowMeta(item({
    queue_status: "resolved", queue_reason: null, needs_staff_since: null, due_at: null,
  }), NOW, me);
  assert.deepEqual(resolved.state, { tone: "success", text: "Resolved" });

  const bot = rowMeta(item({
    queue_status: "bot", queue_reason: null, needs_staff_since: null, due_at: null,
  }), NOW, me);
  assert.equal(bot.state, null);
  assert.equal(bot.due, null);
  assert.equal(bot.reason, null);
});

test("rowMeta snooze time is read in +08:00 whatever the UTC day", () => {
  const snoozed = (snoozed_until) => rowMeta(item({
    queue_status: "snoozed", queue_reason: null, snoozed_until,
  }), NOW, me).state.text;
  assert.equal(snoozed("2026-11-17T16:30:00Z"), "Snoozed until Wed 00:30");
  assert.equal(snoozed("2026-11-20T23:59:00Z"), "Snoozed until Sat 07:59");
  assert.equal(snoozed(null), "Snoozed");
});

test("rowMeta assignee: You, a first name, or Someone when the directory has no name", () => {
  const owned = (assignee_id, display_name) => rowMeta(
    item({ assignee_id, assignee: { id: assignee_id, display_name } }), NOW, me,
  ).assignee;
  assert.deepEqual(owned("u-me", "Aina Demo"), { initials: "AD", text: "You" });
  assert.deepEqual(owned("u-other", "Ben Sample"), { initials: "BS", text: "Ben" });
  assert.deepEqual(owned("u-other", null), { initials: "?", text: "Someone" });
  // The assignee object itself can be missing while the id is set.
  assert.deepEqual(
    rowMeta(item({ assignee_id: "u-other", assignee: null }), NOW, me).assignee,
    { initials: "?", text: "Someone" },
  );
  assert.deepEqual(owned("u-me", null), { initials: "?", text: "You" });
});

// ---- primaryAction ----

const admin = { userId: "u-me", role: "tenant_admin" };
const platform = { userId: null, role: "platform_admin" };
const staff = { userId: "u-me", role: "tenant_staff" };
const held = (name) => ({ assignee_id: "u-other", assignee: { id: "u-other", display_name: name } });

test("primaryAction for a bot item is take_over", () => {
  assert.deepEqual(primaryAction(item({ queue_status: "bot", queue_reason: null }), staff), { kind: "take_over" });
});

test("primaryAction for needs_staff: take it, resolve, take it from", () => {
  assert.deepEqual(primaryAction(item(), staff), { kind: "take_it" });
  assert.deepEqual(primaryAction(item(), admin), { kind: "take_it" });
  const mine = item({ assignee_id: "u-me", assignee: { id: "u-me", display_name: "Aina Demo" } });
  assert.deepEqual(primaryAction(mine, staff), { kind: "resolve" });
  assert.deepEqual(primaryAction(mine, admin), { kind: "resolve" });
});

test("primaryAction on an item someone else holds: admins may take it from them, staff may only resolve", () => {
  assert.deepEqual(primaryAction(item(held("Ben Sample")), admin), { kind: "take_from", name: "Ben" });
  assert.deepEqual(primaryAction(item(held("Ben Sample")), platform), { kind: "take_from", name: "Ben" });
  assert.deepEqual(primaryAction(item(held(null)), admin), { kind: "take_from", name: "someone" });
  assert.deepEqual(primaryAction(item({ assignee_id: "u-other", assignee: null }), admin), {
    kind: "take_from", name: "someone",
  });
  assert.deepEqual(primaryAction(item(held("Ben Sample")), staff), { kind: "resolve" });
});

test("primaryAction for waiting, snoozed and resolved items", () => {
  const status = (queue_status) => primaryAction(item({ queue_status, queue_reason: null }), staff);
  assert.deepEqual(status("waiting_customer"), { kind: "resolve" });
  assert.deepEqual(status("snoozed"), { kind: "resolve" });
  assert.deepEqual(status("resolved"), { kind: "none" });
});

// ---- snoozePresets ----

test("snoozePresets on a Tuesday noon", () => {
  const presets = snoozePresets(new Date(NOW));
  assert.deepEqual(presets, [
    { label: "Later today", until: "2026-11-17T15:00:00+08:00" },
    { label: "Tomorrow 09:00", until: "2026-11-18T09:00:00+08:00" },
    { label: "Next Monday 09:00", until: "2026-11-23T09:00:00+08:00" },
  ]);
});

test("snoozePresets at 23:30 in +08:00: Tomorrow is the next day, Later today crosses midnight", () => {
  const presets = snoozePresets(new Date("2026-11-17T15:30:00Z"));
  assert.equal(presets[0].until, "2026-11-18T02:30:00+08:00");
  assert.equal(presets[1].until, "2026-11-18T09:00:00+08:00");
  assert.equal(presets[2].until, "2026-11-23T09:00:00+08:00");
});

test("snoozePresets uses the +08:00 calendar day, not the UTC one", () => {
  // 01:00 on Wed 18 Nov in +08:00 is still Tue 17 Nov in UTC.
  const presets = snoozePresets(new Date("2026-11-17T17:00:00Z"));
  assert.equal(presets[1].until, "2026-11-19T09:00:00+08:00");
  assert.equal(presets[2].until, "2026-11-23T09:00:00+08:00");
});

test("snoozePresets on a Friday afternoon and on a Monday", () => {
  const friday = snoozePresets(new Date("2026-11-20T07:00:00Z")); // Fri 15:00 +08:00
  assert.equal(friday[0].until, "2026-11-20T18:00:00+08:00");
  assert.equal(friday[1].until, "2026-11-21T09:00:00+08:00");
  assert.equal(friday[2].until, "2026-11-23T09:00:00+08:00");
  const monday = snoozePresets(new Date("2026-11-23T04:00:00Z")); // Mon 12:00 +08:00
  assert.equal(monday[2].until, "2026-11-30T09:00:00+08:00");
  const sunday = snoozePresets(new Date("2026-11-22T04:00:00Z"));
  assert.equal(sunday[2].until, "2026-11-23T09:00:00+08:00");
});

test("snoozePresets crosses a month and a year end", () => {
  const presets = snoozePresets(new Date("2026-12-31T06:00:00Z")); // Thu 14:00 +08:00
  assert.equal(presets[1].until, "2027-01-01T09:00:00+08:00");
  assert.equal(presets[2].until, "2027-01-04T09:00:00+08:00");
});

test("every snooze preset parses and lies in the future", () => {
  const now = new Date("2026-11-17T15:30:00Z");
  for (const { until } of snoozePresets(now)) {
    assert.ok(Date.parse(until) > now.getTime(), until);
  }
});

// ---- actionErrorText ----

test("actionErrorText gives a plain sentence for each case", () => {
  const text = actionErrorText;
  assert.equal(text(409, null, "x"), "This conversation changed. Refreshed, so look again before acting.");
  assert.equal(text(409, "held_reply_pending", "x"), "A held reply needs a decision first.");
  assert.equal(text(409, "customer_wrote", "x"), "The customer wrote after you opened this. Reload to see it.");
  assert.equal(text(403, null, "x"), "You do not have permission to do that.");
  assert.equal(text(404, null, "x"), "This conversation is not available any more.");
  assert.equal(text(422, "until_in_past", "x"), "Pick a time in the future.");
  assert.equal(text(422, "until_too_far", "x"), "Pick a time within the next 60 days.");
  assert.equal(text(503, null, "x"), "The team list is unavailable right now. Try again in a moment.");
  assert.equal(
    text(null, null, "x"),
    "Could not reach the server. Nothing was changed that we know of: try again.",
  );
});

test("actionErrorText falls back for anything else and never shows a raw code", () => {
  assert.equal(actionErrorText(500, null, "Try again."), "Try again.");
  assert.equal(actionErrorText(422, "something_new", "Try again."), "Try again.");
  assert.equal(actionErrorText(409, "something_new", "Try again."), "Try again.");
  assert.equal(actionErrorText(400, null, "Pick a company first."), "Pick a company first.");
  for (const [status, code] of [[409, null], [409, "held_reply_pending"], [409, "customer_wrote"],
    [403, null], [404, null], [422, "until_in_past"], [422, "until_too_far"], [503, null], [null, null]]) {
    assert.ok(!/[a-z]+_[a-z]+/.test(actionErrorText(status, code, "x")), `${status} ${code}`);
  }
});

// ---- bulkSummary ----

const r = (id, outcome) => ({ id, outcome });

test("bulkSummary when everything is done", () => {
  assert.deepEqual(bulkSummary([r("a", "done"), r("b", "already_handled"), r("c", "done")]), {
    text: "3 done.", failedIds: [],
  });
  assert.deepEqual(bulkSummary([r("a", "done")]).text, "1 done.");
});

test("bulkSummary counts each refusal once per kind and keeps failed ids", () => {
  const summary = bulkSummary([
    ...Array.from({ length: 28 }, (_, i) => r(`d${i}`, "done")),
    r("w", "customer_wrote"),
    r("h", "held_reply_pending"),
  ]);
  assert.equal(
    summary.text,
    "28 done. 1 not done: a held reply needs a decision first. 1 not done: the customer wrote after you opened the list.",
  );
  assert.deepEqual(summary.failedIds, ["w", "h"]);
});

test("bulkSummary orders the kinds held reply, customer wrote, not allowed, not in queue, not found", () => {
  const summary = bulkSummary([
    r("1", "done"), r("2", "not_found"), r("3", "not_in_queue"), r("4", "not_allowed"),
    r("5", "customer_wrote"), r("6", "customer_wrote"), r("7", "held_reply_pending"),
  ]);
  assert.equal(
    summary.text,
    "1 done. 1 not done: a held reply needs a decision first."
      + " 2 not done: the customer wrote after you opened the list."
      + " 1 not done: it is assigned to someone else, and only an administrator can change that."
      + " 1 not done: it is not in the queue any more."
      + " 1 not done: it is not available any more.",
  );
  assert.deepEqual(summary.failedIds, ["2", "3", "4", "5", "6", "7"]);
});

test("bulkSummary when nothing was done", () => {
  assert.deepEqual(bulkSummary([r("a", "held_reply_pending"), r("b", "held_reply_pending")]), {
    text: "Nothing was done. 2 not done: a held reply needs a decision first.",
    failedIds: ["a", "b"],
  });
  assert.deepEqual(bulkSummary([]), { text: "Nothing was done.", failedIds: [] });
});

// ---- activeFilterChips ----

test("a tab's own filters make no chips", () => {
  for (const { id } of TABS) assert.deepEqual(activeFilterChips(tabFilters(id), id), [], id);
});

test("activeFilterChips lists what goes beyond the tab, in words", () => {
  const filters = {
    ...tabFilters("all"), assignee: "me", priorityMin: "high", reason: ["escalation", "customer_message"],
    topic: ["complaint"], channel: "telegram", sender: "known", overdue: true, waitingOver: 30,
  };
  assert.deepEqual(activeFilterChips(filters, "all"), [
    { key: "assignee:me", label: "Assigned to me" },
    { key: "priority", label: "Priority high or above" },
    { key: "reason:escalation", label: "Reason: Escalated" },
    { key: "reason:customer_message", label: "Reason: Customer wrote" },
    { key: "topic:complaint", label: "Topic: Complaint" },
    { key: "channel", label: "Channel: Telegram" },
    { key: "sender", label: "Customers" },
    { key: "overdue", label: "Overdue only" },
    { key: "waiting_over", label: "Waiting longer than 30m" },
  ]);
});

test("activeFilterChips word variants", () => {
  const chips = (extra, id = "all") => activeFilterChips({ ...tabFilters(id), ...extra }, id);
  assert.deepEqual(chips({ assignee: "none" }), [{ key: "assignee:none", label: "Unassigned" }]);
  assert.deepEqual(chips({ assignee: "u-9" }), [{ key: "assignee:u-9", label: "Assigned to someone" }]);
  assert.deepEqual(chips({ priorityMin: "urgent" }), [{ key: "priority", label: "Priority urgent" }]);
  assert.deepEqual(chips({ channel: "whatsapp" }), [{ key: "channel", label: "Channel: WhatsApp" }]);
  assert.deepEqual(chips({ sender: "new" }), [{ key: "sender", label: "New senders" }]);
  assert.deepEqual(chips({ waitingOver: 150 }), [{ key: "waiting_over", label: "Waiting longer than 2h 30m" }]);
});

test("activeFilterChips skips what the tab already says", () => {
  // Mine already says assignee me; Unassigned already says none; Overdue already says overdue.
  assert.deepEqual(activeFilterChips({ ...tabFilters("mine"), priorityMin: "high" }, "mine"), [
    { key: "priority", label: "Priority high or above" },
  ]);
  assert.deepEqual(activeFilterChips({ ...tabFilters("overdue"), channel: "telegram" }, "overdue"), [
    { key: "channel", label: "Channel: Telegram" },
  ]);
  // A different assignee on the Mine tab is beyond the tab's own filter.
  assert.deepEqual(activeFilterChips({ ...tabFilters("mine"), assignee: "none" }, "mine"), [
    { key: "assignee:none", label: "Unassigned" },
  ]);
});

// ---- saved views: filters <-> view ----

const { filtersToView, viewToFilters, queueActionNotice, viewErrorText, settingsErrorText } = queue;

test("filters become the API's view keys and leave out what is not set", () => {
  assert.deepEqual(filtersToView(tabFilters("needs_us"), "due"), { state: ["needs_staff"], order: "due" });
  const view = filtersToView({
    state: ["needs_staff", "waiting_customer"], assignee: "me", priorityMin: "high",
    reason: ["escalation"], topic: ["payment", "booking"], channel: "whatsapp", sender: "new",
    overdue: true, waitingOver: 90,
  }, "priority");
  assert.deepEqual(view, {
    state: ["needs_staff", "waiting_customer"], assignee: "me", priority_min: "high",
    reason: ["escalation"], topic: ["payment", "booking"], overdue: true, waiting_over: 90,
    channel: "whatsapp", sender: "new", order: "priority",
  });
  assert.deepEqual(filtersToView(tabFilters("all"), "activity"), { order: "activity" });
});

test("a view is never given the search text, a cursor or a limit", () => {
  const view = filtersToView({ ...tabFilters("all"), q: "ali", before: "x", limit: 5 }, "activity");
  assert.deepEqual(Object.keys(view).sort(), ["order"]);
});

test("a view reads back into the filters and order it was saved from", () => {
  for (const id of ["needs_us", "mine", "unassigned", "overdue", "waiting", "snoozed", "resolved", "all"]) {
    const t = tab(id);
    const back = viewToFilters({ id: "v", name: "n", created_at: "x", filters: filtersToView(tabFilters(id), t.order) });
    assert.equal(back.supported, true, id);
    assert.deepEqual(back.filters, tabFilters(id), id);
    assert.equal(back.order, t.order, id);
  }
  const full = {
    state: ["needs_staff"], assignee: "11111111-1111-1111-1111-111111111111", priorityMin: "urgent",
    reason: ["manual", "held_reply"], topic: ["complaint"], channel: "telegram", sender: "known",
    overdue: true, waitingOver: 30,
  };
  const back = viewToFilters({ id: "v", name: "n", created_at: "x", filters: filtersToView(full, "priority") });
  assert.deepEqual(back, { supported: true, filters: full, order: "priority" });
});

test("a stored view in the server's full shape reads back, nulls and empty lists meaning not set", () => {
  const back = viewToFilters({ id: "v", name: "n", created_at: "x", filters: {
    state: ["snoozed"], assignee: null, priority_min: null, reason: [], topic: [], overdue: false,
    waiting_over: null, channel: null, sender: null, order: null,
  } });
  assert.deepEqual(back, { supported: true, filters: tabFilters("snoozed"), order: "activity" });
});

test("with no stored order the view opens in the order the server would use", () => {
  const mk = (filters) => viewToFilters({ id: "v", name: "n", created_at: "x", filters });
  assert.equal(mk({ state: ["needs_staff"] }).order, "due");
  assert.equal(mk({ overdue: true }).order, "due");
  assert.equal(mk({ waiting_over: 5 }).order, "due");
  assert.equal(mk({ state: ["needs_staff", "snoozed"] }).order, "activity");
  assert.equal(mk({}).order, "activity");
});

test("a view with a key or a value this screen does not know is marked unsupported", () => {
  const mk = (filters) => viewToFilters({ id: "v", name: "n", created_at: "x", filters });
  assert.deepEqual(mk({ state: ["needs_staff"], colour: "red" }), { supported: false });
  assert.deepEqual(mk({ q: "ali" }), { supported: false });
  assert.deepEqual(mk({ before: "abc" }), { supported: false });
  assert.deepEqual(mk({ limit: 10 }), { supported: false });
  assert.deepEqual(mk({ state: ["archived"] }), { supported: false });
  assert.deepEqual(mk({ order: "random" }), { supported: false });
  assert.deepEqual(mk({ waiting_over: "soon" }), { supported: false });
  assert.deepEqual(mk({ overdue: "yes" }), { supported: false });
  assert.deepEqual(mk(null), { supported: false });
});

// ---- notices and error sentences ----

test("each applied action says what happened, and already_handled says nothing changed", () => {
  const n = (action, outcome, detail = {}) => queueActionNotice(action, outcome, detail);
  assert.equal(n("claim", "applied"), "You took this conversation.");
  assert.equal(n("unclaim", "applied"), "Gave up this item.");
  assert.equal(n("resolve", "applied"), "Resolved. The assistant has this conversation again.");
  assert.equal(n("priority", "applied", { priority: "urgent" }), "Priority set to Urgent.");
  assert.equal(n("topic", "applied", { topic: "payment" }), "Topic set to Payment.");
  assert.equal(n("assign", "applied", { assigneeName: "Aina Demo" }), "Assigned to Aina Demo.");
  assert.equal(n("assign", "applied", {}), "Assigned.");
  // 2026-11-18T01:00:00Z is Wednesday 09:00 in +08:00.
  assert.equal(n("snooze", "applied", { until: "2026-11-18T01:00:00Z" }), "Snoozed until Wed 09:00.");
  assert.equal(n("snooze", "applied", {}), "Snoozed.");
  for (const action of ["claim", "unclaim", "assign", "snooze", "resolve", "priority", "topic"]) {
    assert.equal(n(action, "already_handled"), "Already up to date.");
  }
});

test("saved-view errors are plain sentences and never a raw code", () => {
  const fallback = "Could not save the view.";
  assert.equal(viewErrorText(409, "you can keep at most 20 saved views", fallback),
    "You can keep at most 20 saved views. Delete one first.");
  assert.equal(viewErrorText(409, "you already have a saved view with this name", fallback),
    "You already have a view with that name.");
  assert.equal(viewErrorText(422, "Request failed (422)", fallback), "That name or filter is not allowed.");
  assert.equal(viewErrorText(403, "Request failed (403)", fallback), "You do not have permission to do that.");
  assert.equal(viewErrorText(null, null, fallback), actionErrorText(null, null, fallback));
  assert.equal(viewErrorText(500, "x", fallback), fallback);
  assert.equal(viewErrorText(409, "something else", fallback), fallback);
});

test("a settings error is a plain sentence, a version conflict says the settings were reloaded", () => {
  const fallback = "Could not save.";
  assert.equal(settingsErrorText(409, "version_conflict", fallback),
    "These settings changed while you were editing. They are reloaded: check them and save again.");
  assert.equal(settingsErrorText(403, "x", fallback), "You do not have permission to do that.");
  assert.equal(settingsErrorText(422, "x", fallback), "One of these values is out of range. Check them and save again.");
  assert.equal(settingsErrorText(null, null, fallback), actionErrorText(null, null, fallback));
  assert.equal(settingsErrorText(500, "x", fallback), fallback);
});

test("a failed assignee read is unavailable for 503 only; every other failure is `failed`, never plain loading", () => {
  const { assigneesFailure } = queue;
  assert.deepEqual(assigneesFailure(503), { unavailable: true, failed: false });
  for (const status of [null, 403, 404, 500, 502, 504]) {
    assert.deepEqual(assigneesFailure(status), { unavailable: false, failed: true }, String(status));
  }
});
