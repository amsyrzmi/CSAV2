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
  primaryAction, snoozePresets, actionErrorText, bulkSummary, initialsOf, withoutChip,
  priorityOrderAllowed, tabOrder, bulkFailureLines, selectAllText, BULK_LIMIT_TEXT,
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

test("search text with reserved or non-ASCII characters is encoded (moved from inboxListQuery)", () => {
  assert.equal(listFor("all", { q: "50% off" }), `${BASE}?order=activity&q=50%25%20off`);
  assert.equal(listFor("all", { q: "@aisyah" }), `${BASE}?order=activity&q=%40aisyah`);
  assert.equal(listFor("all", { q: "a&b=c" }), `${BASE}?order=activity&q=a%26b%3Dc`);
  assert.equal(listFor("all", { q: "Zoë 你好" }), `${BASE}?order=activity&q=${encodeURIComponent("Zoë 你好")}`);
  assert.equal(listFor("all", { tenantQuery: "client_id=abc", q: "a" }), `${BASE}?client_id=abc&order=activity`);
});

// ---- withoutChip / priorityOrderAllowed / tabOrder ----

test("removing a chip puts that value back to the tab's own and leaves the rest", () => {
  const filters = {
    ...tabFilters("needs_us"), assignee: "u-9", priorityMin: "high", reason: ["escalation", "manual"],
    topic: ["payment"], channel: "telegram", sender: "new", overdue: true, waitingOver: 60,
  };
  const keys = activeFilterChips(filters, "needs_us").map((chip) => chip.key);
  assert.deepEqual(keys, [
    "assignee:u-9", "priority", "reason:escalation", "reason:manual", "topic:payment", "channel", "sender",
    "overdue", "waiting_over",
  ]);
  assert.equal(withoutChip(filters, "needs_us", "assignee:u-9").assignee, "");
  assert.equal(withoutChip(filters, "needs_us", "priority").priorityMin, "");
  assert.deepEqual(withoutChip(filters, "needs_us", "reason:escalation").reason, ["manual"]);
  assert.deepEqual(withoutChip(filters, "needs_us", "topic:payment").topic, []);
  assert.equal(withoutChip(filters, "needs_us", "channel").channel, "");
  assert.equal(withoutChip(filters, "needs_us", "sender").sender, "");
  assert.equal(withoutChip(filters, "needs_us", "overdue").overdue, false);
  assert.equal(withoutChip(filters, "needs_us", "waiting_over").waitingOver, null);
  // Every chip removed one by one leaves exactly the tab's own filters.
  const cleared = keys.reduce((current, key) => withoutChip(current, "needs_us", key), filters);
  assert.deepEqual(cleared, tabFilters("needs_us"));
  assert.deepEqual(activeFilterChips(cleared, "needs_us"), []);
  // The state is never a chip, and an unknown key changes nothing.
  assert.equal(withoutChip(filters, "needs_us", "nonsense"), filters);
});

test("a chip on a tab that sets the same filter goes back to the tab's value", () => {
  const mine = { ...tabFilters("mine"), assignee: "none" };
  assert.deepEqual(activeFilterChips(mine, "mine").map((chip) => chip.key), ["assignee:none"]);
  assert.equal(withoutChip(mine, "mine", "assignee:none").assignee, "me");
});

test("priority order only when the state is exactly needs_staff, and each tab's own order", () => {
  assert.equal(priorityOrderAllowed(tabFilters("needs_us")), true);
  assert.equal(priorityOrderAllowed(tabFilters("unassigned")), true);
  assert.equal(priorityOrderAllowed(tabFilters("mine")), false);
  assert.equal(priorityOrderAllowed(tabFilters("waiting")), false);
  assert.equal(priorityOrderAllowed(tabFilters("all")), false);
  for (const t of TABS) assert.equal(tabOrder(t.id), t.order);
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

test("bulkFailureLines names each item that did not apply, grouped as the summary orders them", () => {
  const names = { w: "Aina Demo", h: "Unknown sender on Telegram" };
  const lines = bulkFailureLines([
    r("d", "done"), r("w", "customer_wrote"), r("x", "not_found"), r("h", "held_reply_pending"),
    r("a", "already_handled"),
  ], (id) => names[id]);
  assert.deepEqual(lines, [
    "Unknown sender on Telegram: a held reply needs a decision first.",
    "Aina Demo: the customer wrote after you opened the list.",
    "A conversation no longer in this list: it is not available any more.",
  ]);
  assert.deepEqual(bulkFailureLines([r("d", "done")], () => "x"), []);
});

test("selectAllText says the loaded rows only, and the ceiling when it bites", () => {
  assert.equal(selectAllText(12), "Selected all 12 conversations loaded so far.");
  assert.equal(selectAllText(50), "Selected all 50 conversations loaded so far.");
  assert.equal(selectAllText(55),
    "Selected the first 50 of the 55 conversations loaded so far. You can select up to 50 conversations at a time.");
  assert.equal(BULK_LIMIT_TEXT, "You can select up to 50 conversations at a time.");
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
  const n = (action, outcome, detail = {}) => queueActionNotice(action, outcome, detail, NOW);
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
  // More than six days ahead the date is part of it (2026-11-25T01:00:00Z is Wed 25 Nov, 09:00).
  assert.equal(n("snooze", "applied", { until: "2026-11-25T01:00:00Z" }), "Snoozed until Wed 25 Nov 09:00.");
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

test("a settings error is a plain sentence, a version conflict says the latest values are shown", () => {
  const fallback = "Could not save.";
  assert.equal(settingsErrorText(409, "version_conflict", fallback),
    "Someone else changed these. The latest values are shown; check them and save again.");
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

// ---- the row: clock, unread emphasis and the due label ----

test("rowClock is the wait in the four queue tabs and the last message time elsewhere, with the absolute time in +08:00", () => {
  const { rowClock } = queue;
  // NOW is 12:00 in +08:00; the clock started 2 h 10 m ago, the last message 3 minutes ago.
  const row = item({
    needs_staff_since: ago(2 * HOUR + 10 * MIN),
    last_message: { preview: "Hello", at: ago(3 * MIN) },
  });
  for (const id of ["needs_us", "mine", "unassigned", "overdue"]) {
    assert.deepEqual(rowClock(row, id, NOW), {
      kind: "wait", text: "2h 10m", title: "Waiting since 17 Nov 2026, 09:50",
    }, id);
  }
  for (const id of ["waiting", "snoozed", "resolved", "all"]) {
    assert.deepEqual(rowClock(row, id, NOW), {
      kind: "last", at: ago(3 * MIN), title: "Last message 17 Nov 2026, 11:57",
    }, id);
  }
});

test("rowClock falls back to the last message (or the opening time) when there is no wait clock", () => {
  const { rowClock } = queue;
  // A waiting-on-customer item in Mine has no needs_staff_since.
  const waiting = item({
    queue_status: "waiting_customer", needs_staff_since: null,
    last_message: { preview: "Thanks", at: "2026-11-16T15:59:00Z" },
  });
  assert.deepEqual(rowClock(waiting, "mine", NOW), {
    kind: "last", at: "2026-11-16T15:59:00Z", title: "Last message 16 Nov 2026, 23:59",
  });
  // A row from an older server has no queue fields at all.
  const bare = { ...item({ last_message: null }), needs_staff_since: undefined };
  assert.deepEqual(rowClock(bare, "needs_us", NOW), {
    kind: "last", at: ago(5 * HOUR), title: "Last message 17 Nov 2026, 07:00",
  });
  // An unreadable time keeps the clock but gives no title.
  assert.deepEqual(rowClock(item({ last_message: { preview: "", at: "nope" } }), "all", NOW), {
    kind: "last", at: "nope", title: "",
  });
});

test("isUnread: the customer wrote last and nobody has answered", () => {
  const { isUnread } = queue;
  assert.equal(isUnread(item({ last_customer_at: ago(HOUR), last_staff_reply_at: null })), true);
  assert.equal(isUnread(item({ last_customer_at: ago(HOUR), last_staff_reply_at: ago(2 * HOUR) })), true);
  assert.equal(isUnread(item({ last_customer_at: ago(2 * HOUR), last_staff_reply_at: ago(HOUR) })), false);
  assert.equal(isUnread(item({ last_customer_at: ago(HOUR), last_staff_reply_at: ago(HOUR) })), false);
  assert.equal(isUnread(item({ last_customer_at: null, last_staff_reply_at: null })), false);
  assert.equal(isUnread(item({ last_customer_at: null, last_staff_reply_at: ago(HOUR) })), false);
  // Rows without the queue fields (older servers, other screens' stubs) are never emphasised.
  assert.equal(isUnread({ ...item(), last_customer_at: undefined, last_staff_reply_at: undefined }), false);
  assert.equal(isUnread(item({ last_customer_at: "nope", last_staff_reply_at: null })), false);
});

test("dueLabel: neutral minutes, Due soon in the last quarter, Overdue in words in the error tone", () => {
  const { dueLabel } = queue;
  const due = (over) => dueLabel(rowMeta(item(over), NOW, me).due);
  assert.deepEqual(due({ needs_staff_since: ago(HOUR), due_at: ahead(30 * MIN) }), {
    tone: "neutral", text: "Due in 30m",
  });
  assert.deepEqual(due({ needs_staff_since: ago(HOUR), due_at: ahead(10 * MIN) }), {
    tone: "attention", text: "Due soon",
  });
  assert.deepEqual(due({ needs_staff_since: ago(3 * HOUR), due_at: ago(HOUR) }), {
    tone: "error", text: "Overdue 1h",
  });
  assert.equal(dueLabel(null), null);
  // Not a needs_staff row: rowMeta has no due part, so there is no label.
  assert.equal(due({ queue_status: "waiting_customer", queue_reason: null }), null);
});

test("rowMeta and the row helpers tolerate a row with no queue fields at all", () => {
  const { rowClock, isUnread } = queue;
  const bare = {
    id: "c9", channel: "whatsapp", status: "open", owned_by: null, ai_muted_until: null,
    human_muted: false, inbox_version: 1, opened_at: ago(HOUR), contact: null,
    last_message: { preview: "Hi", at: ago(MIN) }, pending_approvals: 0,
  };
  assert.deepEqual(rowMeta(bare, NOW, me), {
    state: null, due: null, priority: null, reason: null, assignee: null,
  });
  assert.equal(isUnread(bare), false);
  assert.equal(rowClock(bare, "needs_us", NOW).kind, "last");
});

// ---- the thread: snooze time, banner, failures, custom time, the actions offered ----

test("snoozeUntilText: weekday and time up to six days ahead, the date beyond", () => {
  const { snoozeUntilText } = queue;
  // NOW is Tue 17 Nov 12:00 in +08:00.
  assert.equal(snoozeUntilText("2026-11-18T01:00:00Z", NOW), "Wed 09:00");
  assert.equal(snoozeUntilText(ahead(6 * 24 * HOUR), NOW), "Mon 12:00");
  assert.equal(snoozeUntilText(ahead(6 * 24 * HOUR + MIN), NOW), "Mon 23 Nov 12:01");
  assert.equal(snoozeUntilText("2026-12-31T16:30:00Z", NOW), "Fri 1 Jan 00:30");
  assert.equal(snoozeUntilText(null, NOW), "");
  assert.equal(snoozeUntilText("not a time", NOW), "");
});

test("the row's snooze label and the snooze notice use the same words", () => {
  const until = "2026-11-25T01:00:00Z";
  const label = rowMeta(item({ queue_status: "snoozed", queue_reason: null, snoozed_until: until }), NOW, me);
  assert.equal(label.state.text, "Snoozed until Wed 25 Nov 09:00");
  assert.equal(queueActionNotice("snooze", "applied", { until }, NOW), "Snoozed until Wed 25 Nov 09:00.");
});

test("waitBannerText: how long it has waited and when it is due, from the server's times", () => {
  const { waitBannerText } = queue;
  const banner = (over) => waitBannerText(item(over), NOW);
  assert.equal(banner({ needs_staff_since: ago(200 * MIN), due_at: ago(HOUR) }), "Waiting on us for 3h 20m, due 1h ago");
  assert.equal(banner({ needs_staff_since: ago(20 * MIN), due_at: ahead(40 * MIN) }), "Waiting on us for 20m, due in 40m");
  assert.equal(banner({ needs_staff_since: ago(30_000), due_at: ahead(30_000) }), "Waiting on us for under 1m, due in under 1m");
  assert.equal(banner({ needs_staff_since: ago(HOUR), due_at: ago(30_000) }), "Waiting on us for 1h, due just now");
  assert.equal(banner({ needs_staff_since: ago(HOUR), due_at: null }), "Waiting on us for 1h");
  assert.equal(banner({ needs_staff_since: null }), "");
  assert.equal(banner({ queue_status: "waiting_customer", queue_reason: null }), "");
});

test("assignedText: someone else's claim, with or without a name; nothing for mine or unclaimed", () => {
  const { assignedText } = queue;
  assert.equal(assignedText(item({ assignee_id: "u-2", assignee: { id: "u-2", display_name: "Aina Demo" } }), me),
    "Assigned to Aina Demo");
  assert.equal(assignedText(item({ assignee_id: "u-2", assignee: { id: "u-2", display_name: null } }), me),
    "Assigned to someone outside the team list");
  assert.equal(assignedText(item({ assignee_id: "u-2", assignee: null }), me), "Assigned to someone outside the team list");
  assert.equal(assignedText(item({ assignee_id: "u-me" }), me), "");
  assert.equal(assignedText(item(), me), "");
  assert.equal(assignedText({ id: "c9" }, me), "");
});

test("queueFailureText: an unanswered or 5xx request is uncertain; the team sentence is for assign only", () => {
  const { queueFailureText } = queue;
  const uncertain = "Could not confirm that. Retry sends the same request again.";
  assert.equal(queueFailureText("resolve", null, null), uncertain);
  assert.equal(queueFailureText("claim", 500, null), uncertain);
  assert.equal(queueFailureText("topic", 503, null, "member directory unavailable"), uncertain);
  assert.equal(queueFailureText("assign", 503, null, "member directory unavailable"),
    "The team list is unavailable right now. Try again in a moment.");
  // A 503 without the server's detail (a proxy) may have been applied.
  assert.equal(queueFailureText("assign", 503, null), uncertain);
  assert.equal(queueFailureText("assign", 502, null), uncertain);
  assert.equal(queueFailureText("resolve", 409, "held_reply_pending"), "A held reply needs a decision first.");
  assert.equal(queueFailureText("claim", 409, null), "This conversation changed. Refreshed, so look again before acting.");
  assert.equal(queueFailureText("claim", 403, null), "You do not have permission to do that.");
  assert.equal(queueFailureText("assign", 422, null), "That could not be done. Refreshed: look again before acting.");
});

test("queueFailureIsFinal: 4xx and assign's directory 503 are settled; no answer and other 5xx are kept", () => {
  const { queueFailureIsFinal } = queue;
  const down = "member directory unavailable";
  for (const status of [400, 403, 404, 409, 422]) assert.equal(queueFailureIsFinal("claim", status, null), true);
  assert.equal(queueFailureIsFinal("assign", 503, down), true);
  assert.equal(queueFailureIsFinal("assign", 503, "Request failed (503)"), false);
  assert.equal(queueFailureIsFinal("assign", 503, null), false);
  assert.equal(queueFailureIsFinal("topic", 503, down), false);
  assert.equal(queueFailureIsFinal("assign", 500, down), false);
  assert.equal(queueFailureIsFinal("resolve", null, null), false);
});

test("customSnoozeUntil reads the field in +08:00 and says in words what is wrong", () => {
  const { customSnoozeUntil } = queue;
  // NOW is 2026-11-17 12:00 in +08:00.
  assert.deepEqual(customSnoozeUntil("2026-11-18T09:30", NOW), { until: "2026-11-18T09:30:00+08:00" });
  assert.deepEqual(customSnoozeUntil("2026-11-18T09:30:15", NOW), { until: "2026-11-18T09:30:00+08:00" });
  assert.deepEqual(customSnoozeUntil("2026-11-17T12:00", NOW), { error: "Pick a time in the future." });
  assert.deepEqual(customSnoozeUntil("2026-11-16T09:00", NOW), { error: "Pick a time in the future." });
  // 60 days ahead is allowed, a minute more is not.
  assert.deepEqual(customSnoozeUntil("2027-01-16T12:00", NOW), { until: "2027-01-16T12:00:00+08:00" });
  assert.deepEqual(customSnoozeUntil("2027-01-16T12:01", NOW), { error: "Pick a time within the next 60 days." });
  assert.deepEqual(customSnoozeUntil("", NOW), { error: "Pick a date and time." });
  assert.deepEqual(customSnoozeUntil("tomorrow", NOW), { error: "Pick a date and time." });
});

test("threadActions: what the header offers per item and role", () => {
  const { threadActions } = queue;
  const staff = { userId: "u-me", role: "tenant_staff" };
  const admin = { userId: "u-me", role: "tenant_admin" };
  const platform = { userId: "u-me", role: "platform_admin" };
  const other = { assignee_id: "u-2", assignee: { id: "u-2", display_name: "Aina Demo" } };
  // The assistant has it, or an older server sent no queue fields: the old Take over block.
  assert.equal(threadActions(item({ queue_status: "bot", queue_reason: null }), admin), null);
  assert.equal(threadActions({ id: "c9", human_muted: false }, admin), null);

  const open = threadActions(item(), staff);
  assert.deepEqual(open, {
    primary: { kind: "take_it" }, snooze: true, assign: false, unclaim: false, priority: true, topic: true,
    takeOver: true, handBack: true,
  });
  assert.equal(threadActions(item(), admin).assign, true);
  assert.equal(threadActions(item(), platform).assign, true);

  // Someone else holds it: staff may resolve but neither take it nor give it up; admins may both.
  const theirs = threadActions(item(other), staff);
  assert.deepEqual([theirs.primary, theirs.unclaim, theirs.assign], [{ kind: "resolve" }, false, false]);
  const taken = threadActions(item(other), admin);
  assert.deepEqual([taken.primary, taken.unclaim, taken.assign], [{ kind: "take_from", name: "Aina" }, true, true]);
  assert.equal(threadActions(item({ assignee_id: "u-me" }), staff).unclaim, true);

  // A person already holds the chat: no Take over, Hand back stays.
  const muted = threadActions(item({ human_muted: true }), staff);
  assert.deepEqual([muted.takeOver, muted.handBack], [false, true]);

  for (const state of ["waiting_customer", "snoozed"]) {
    const entry = threadActions(item({ queue_status: state, queue_reason: null }), admin);
    assert.deepEqual([entry.primary.kind, entry.snooze, entry.assign], ["resolve", true, true], state);
  }
  // Resolved: nothing to resolve, snooze or assign; priority and topic stay; Hand back only if a person holds it.
  const resolved = threadActions(item({ queue_status: "resolved", queue_reason: null }), admin);
  assert.deepEqual(resolved, {
    primary: { kind: "none" }, snooze: false, assign: false, unclaim: false, priority: true, topic: true,
    takeOver: true, handBack: false,
  });
  assert.equal(threadActions(item({ queue_status: "resolved", queue_reason: null, human_muted: true }), admin).handBack, true);
});

test("viewKey ignores the order lists were picked in and tells filters and order apart", () => {
  const { viewKey } = queue;
  const a = { ...tabFilters("all"), state: ["needs_staff", "waiting_customer"], topic: ["payment", "complaint"] };
  const b = { ...tabFilters("all"), state: ["waiting_customer", "needs_staff"], topic: ["complaint", "payment"] };
  assert.equal(viewKey(a, "due"), viewKey(b, "due"));
  assert.notEqual(viewKey(a, "due"), viewKey(a, "activity"));
  assert.notEqual(viewKey(a, "due"), viewKey({ ...a, channel: "telegram" }, "due"));
});

test("viewChoice opens a view on the most specific tab whose own filters it holds", () => {
  const { viewChoice, viewKey } = queue;
  const mk = (filters) => viewChoice({ filters });
  // Each tab's own filters and order come back as that tab, with nothing extra.
  for (const t of TABS) {
    const choice = mk(filtersToView(tabFilters(t.id), t.order));
    assert.equal(choice.supported, true, t.id);
    assert.equal(choice.tab, t.id, t.id);
    assert.deepEqual(choice.filters, tabFilters(t.id), t.id);
    assert.equal(choice.order, t.order, t.id);
    assert.deepEqual(activeFilterChips(choice.filters, choice.tab), [], t.id);
  }
  // Another order keeps the tab; the extra filters are the tab's chips.
  const urgent = mk({ state: ["needs_staff"], priority_min: "urgent", order: "priority" });
  assert.deepEqual([urgent.tab, urgent.order], ["needs_us", "priority"]);
  assert.deepEqual(activeFilterChips(urgent.filters, urgent.tab).map((c) => c.key), ["priority"]);
  // Mine's state in another order is still Mine, and its query is Mine's own plus the chip.
  const mine = mk({ state: ["waiting_customer", "needs_staff"], assignee: "me", channel: "telegram" });
  assert.equal(mine.tab, "mine");
  assert.deepEqual(mine.filters.state, ["needs_staff", "waiting_customer"]);
  assert.equal(mine.order, "activity"); // the server's default for that state set
  // A tie between Unassigned and Overdue goes to the first in tab order.
  const late = mk({ state: ["needs_staff"], assignee: "none", overdue: true, order: "due" });
  assert.equal(late.tab, "unassigned");
  assert.deepEqual(activeFilterChips(late.filters, late.tab).map((c) => c.key), ["overdue"]);
  // Overdue without a state, or nothing but a topic, is All with chips.
  assert.equal(mk({ overdue: true }).tab, "all");
  assert.equal(mk({ topic: ["complaint"], order: "waiting" }).tab, "all");
  // The key is the key of what the screen shows.
  assert.equal(late.key, viewKey(late.filters, late.order));
  // Nothing a tab can show: a state set no tab has, an unknown key, priority outside needs us alone.
  assert.deepEqual(mk({ state: ["bot"] }), { supported: false });
  assert.deepEqual(mk({ state: ["needs_staff", "snoozed"] }), { supported: false });
  assert.deepEqual(mk({ state: ["needs_staff", "waiting_customer"], assignee: "none" }), { supported: false });
  assert.deepEqual(mk({ needs_review: true }), { supported: false });
  assert.deepEqual(mk({ state: ["resolved"], order: "priority" }), { supported: false });
  // The server's full shape (defaults present) reads the same as the stored one.
  const full = mk({
    state: ["needs_staff"], assignee: null, priority_min: null, reason: [], topic: [], overdue: false,
    waiting_over: null, channel: null, sender: null, order: null,
  });
  assert.deepEqual([full.tab, full.order], ["needs_us", "due"]);
});

// ---- Task 9: each tab's empty sentence and whether the counts show any work ----

test("each tab has its own empty sentence, the design's words where it gives them", () => {
  const { TAB_EMPTY } = queue;
  assert.deepEqual(TAB_EMPTY, {
    needs_us: "Nothing needs your team right now",
    mine: "Nothing is assigned to you",
    unassigned: "Nothing is waiting for someone to take it",
    overdue: "Nothing is overdue",
    waiting: "No one is waiting on a customer",
    snoozed: "Nothing is snoozed",
    resolved: "No resolved conversations yet",
    all: "No conversations yet",
  });
  for (const t of TABS) assert.ok(TAB_EMPTY[t.id], t.id);
});

test("the counts show work only when one of them is above zero; unknown or zero is not proof", () => {
  const { countsShowWork } = queue;
  const zero = { needs_us: 0, mine: 0, unassigned: 0, overdue: 0, waiting_customer: 0, snoozed: 0,
    oldest_waiting_at: null };
  assert.equal(countsShowWork(null), false);
  assert.equal(countsShowWork(zero), false);
  for (const key of ["needs_us", "mine", "unassigned", "overdue", "waiting_customer", "snoozed"])
    assert.equal(countsShowWork({ ...zero, [key]: 1 }), true, key);
  // The oldest wait is a time, not a count.
  assert.equal(countsShowWork({ ...zero, oldest_waiting_at: "2026-11-17T03:00:00Z" }), false);
});

test("the company-wide probe asks the All list for one row", () => {
  const { anyConversationQuery } = queue;
  assert.equal(anyConversationQuery(""), `${BASE}?order=activity&limit=1`);
  assert.equal(anyConversationQuery("client_id=t1"), `${BASE}?client_id=t1&order=activity&limit=1`);
});
