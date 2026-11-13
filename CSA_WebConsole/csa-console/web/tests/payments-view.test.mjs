import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

// A picked day is turned into an instant on the viewer's calendar; pin the zone.
process.env.TZ = "Asia/Kuala_Lumpur";

function moduleUrl(source) {
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const apiUrl = moduleUrl(await readFile(new URL("../lib/api.ts", import.meta.url), "utf8"));
const paySource = await readFile(new URL("../lib/payments-api.ts", import.meta.url), "utf8");
const payUrl = moduleUrl(paySource.replace('"./api"', JSON.stringify(apiUrl)));
const viewSource = await readFile(new URL("../lib/payments-view.ts", import.meta.url), "utf8");
const view = await import(moduleUrl(viewSource.replace('"@/lib/payments-api"', JSON.stringify(payUrl))));

const ZONE = "Asia/Kuala_Lumpur";

function invoice(id, status = "issued", changes = {}) {
  return {
    id, reference: `K7QX-${id.slice(-3)}`, status, amount: "59.90", currency: "MYR",
    issued_at: "2026-11-01T01:00:00Z", due_at: "2026-11-08T01:00:00Z", paid_at: null,
    period_start: null, period_end: null, subscription_id: null, plan_name: "Basic",
    contact: { id: "77777777-7777-4777-8777-777777777777", full_name: "Aisyah Rahman", channels: ["telegram"] },
    claim_note: null, claimed_at: null, method_kind: null, void_reason: null, voided_at: null,
    reminded_at: null, ...changes,
  };
}

test("the status chips map to the list filter values in order", () => {
  assert.deepEqual(view.filterChips(), [
    { value: "open", label: "Open" },
    { value: "overdue", label: "Overdue" },
    { value: "proof_submitted", label: "Customer says paid" },
    { value: "paid", label: "Paid" },
    { value: "void", label: "Void" },
    { value: "all", label: "All" },
  ]);
});

test("needs attention groups by kind in a fixed order, whatever order the items arrive in", () => {
  const items = [
    { kind: "reminder_failed", invoice: invoice("inv_003", "overdue"), since: "2026-11-10T01:00:00Z" },
    { kind: "needs_nudge", invoice: invoice("inv_002"), since: "2026-11-09T01:00:00Z" },
    { kind: "proof_submitted", invoice: invoice("inv_001", "proof_submitted"), since: "2026-11-08T01:00:00Z" },
    { kind: "reminder_failed", invoice: invoice("inv_001", "proof_submitted"), since: "2026-11-11T01:00:00Z" },
  ];
  const groups = view.attentionGroups(items);
  assert.deepEqual(groups.map((g) => [g.kind, g.label, g.items.map((i) => i.invoice.id)]), [
    ["proof_submitted", "Customer says paid", ["inv_001"]],
    ["needs_nudge", "Needs a nudge", ["inv_002"]],
    ["reminder_failed", "Reminder failed", ["inv_003", "inv_001"]],
  ]);
  // The badge counts invoices, not rows: inv_001 is in two groups.
  assert.equal(view.attentionCount(items), 3);
  assert.deepEqual(view.attentionGroups([]).map((g) => g.items.length), [0, 0, 0]);
  assert.equal(view.attentionCount([]), 0);
});

test("a reminder reads as its channel and day, never an address", () => {
  const r = (channel, status, sent_at = null, created_at = "2026-11-11T20:00:00Z") =>
    ({ step: 1, channel, status, created_at, sent_at });
  // 12 Nov in Kuala Lumpur (04:00 local), though it is still 11 Nov in UTC at creation.
  assert.equal(view.reminderLine(r("telegram", "sent", "2026-11-11T20:00:00Z"), ZONE), "Telegram · sent 12 Nov");
  assert.equal(view.reminderLine(r("whatsapp", "sent", "2026-11-03T02:00:00Z"), ZONE), "WhatsApp · sent 3 Nov");
  assert.equal(view.reminderLine(r("email", "sent", null), ZONE), "Email · sent 12 Nov");
  assert.equal(view.reminderLine(r("none", "no_channel"), ZONE), "No channel: message them yourself");
  assert.equal(view.reminderLine(r("telegram", "failed"), ZONE), "Telegram · failed 12 Nov");
  assert.equal(view.reminderLine(r("whatsapp", "queued"), ZONE), "WhatsApp · waiting to send");
  assert.equal(view.reminderLine(r("whatsapp", "sending"), ZONE), "WhatsApp · waiting to send");
  assert.equal(view.reminderLine(r("telegram", "dropped"), ZONE), "Telegram · not sent");
  assert.equal(view.reminderLine(r("telegram", "skipped"), ZONE), "Telegram · not sent");
});

test("amounts are formatted from the string, never through a JS number", () => {
  assert.equal(view.formatAmount("59.90", "MYR"), "RM 59.90");
  assert.equal(view.formatAmount("59.9", "MYR"), "RM 59.90");
  assert.equal(view.formatAmount("1234.5", "MYR"), "RM 1,234.50");
  assert.equal(view.formatAmount("10", "USD"), "USD 10.00");
  // Past a double's precision: a Number would round this.
  assert.equal(view.formatAmount("12345678901234567.89", "MYR"), "RM 12,345,678,901,234,567.89");
  assert.equal(view.formatAmount("0.05", "MYR"), "RM 0.05");
  for (const bad of ["", "abc", "1e3", null, undefined, 59.9]) assert.equal(view.formatAmount(bad, "MYR"), "—");
});

test("the payment day becomes an instant: now for today, midday for an earlier day", () => {
  const now = new Date("2026-11-13T03:30:00Z"); // 11:30 in Kuala Lumpur
  assert.equal(view.todayInput(now), "2026-11-13");
  assert.equal(view.receivedAt("2026-11-13", now), "2026-11-13T03:30:00.000Z");
  assert.equal(view.receivedAt("2026-11-10", now), "2026-11-10T04:00:00.000Z");
});

test("methods, the confirm question and the void reason", () => {
  assert.deepEqual(view.METHOD_OPTIONS, [
    { value: "transfer", label: "Bank transfer" },
    { value: "qr", label: "DuitNow" },
    { value: "cash", label: "Cash" },
  ]);
  assert.equal(view.methodLabel("qr"), "DuitNow");
  assert.equal(view.methodLabel(null), "—");
  assert.equal(view.markPaidQuestion("K7QX-4MF"),
    "Mark invoice K7QX-4MF paid? This starts the subscription if it is waiting.");
  assert.deepEqual(view.voidBody("  Sent twice  "), { reason: "Sent twice" });
  assert.equal(view.voidBody("   "), null);
  assert.equal(view.voidBody("x".repeat(301)), null);
});

test("only open invoices can be marked paid; paid and void ones cannot be voided", () => {
  for (const status of ["issued", "overdue", "proof_submitted", "cash_pending"]) {
    assert.equal(view.canMarkPaid(status), true, status);
    assert.equal(view.canVoid(status), true, status);
  }
  for (const status of ["paid", "void", "draft"]) assert.equal(view.canMarkPaid(status), false, status);
  for (const status of ["paid", "void"]) assert.equal(view.canVoid(status), false, status);
});

test("an invoice is named by its reference and its customer by name", () => {
  assert.equal(view.invoiceName(invoice("inv_001")), "K7QX-001");
  assert.equal(view.invoiceName(invoice("inv_001", "issued", { reference: null })), "Invoice without a reference");
  assert.equal(view.customerLabel(invoice("inv_001")), "Aisyah Rahman");
  assert.equal(view.customerLabel(invoice("inv_001", "issued", { contact: null })), "No customer");
  assert.equal(view.customerLabel(invoice("inv_001", "issued", {
    contact: { id: "x", full_name: null, channels: [] } })), "Unnamed customer");
});

test("Load more belongs to the list it was read for: a late page for an old filter is dropped", () => {
  const open = view.listKey(null, "open", "", 0);
  const paid = view.listKey(null, "paid", "", 0);
  assert.notEqual(open, paid);
  assert.notEqual(view.listKey(null, "open", "ben", 0), open);
  assert.notEqual(view.listKey(null, "open", "", 1), open);
  assert.notEqual(view.listKey("tenant-a", "open", "", 0), open);

  const first = view.firstPage(open, { items: [invoice("inv_0003"), invoice("inv_0002")], next_before: "inv_0002" });
  assert.deepEqual(first.rows.map((row) => row.id), ["inv_0003", "inv_0002"]);
  assert.equal(view.moreCursor(first, open), "inv_0002");
  // Under a new chip the old cursor is not offered, before or after the new first page.
  assert.equal(view.moreCursor(first, paid), null);
  assert.equal(view.moreCursor(null, open), null);

  const page2 = { items: [invoice("inv_0001")], next_before: null };
  const more = view.appendPage(first, open, page2);
  assert.deepEqual(more.rows.map((row) => row.id), ["inv_0003", "inv_0002", "inv_0001"]);
  assert.equal(view.moreCursor(more, open), null);

  // The chip changed while Load more was out: the new list gets none of the old rows.
  const paidFirst = view.firstPage(paid, { items: [invoice("inv_0009", "paid")], next_before: null });
  const late = view.appendPage(paidFirst, open, page2);
  assert.equal(late, paidFirst);
  assert.deepEqual(late.rows.map((row) => row.id), ["inv_0009"]);
  // Late, before the new first page arrived: dropped too.
  assert.equal(view.appendPage(first, paid, page2), first);
  assert.equal(view.appendPage(null, open, page2), null);
});
