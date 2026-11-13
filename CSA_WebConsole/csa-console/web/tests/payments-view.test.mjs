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

// ---------------------------------------------------------------- Settings and methods (Task 13)

// The synthetic methods of tests/test_payment_copy.py (TRANSFER and QR), as the API returns them.
const TRANSFER = {
  id: "aaaaaaaa-0000-4000-8000-000000000011", kind: "transfer", label: "Bank",
  details: { bank_name: "Bank Contoh", account_number: "512000000012", account_name: "Kedai Contoh" },
  instructions: "", is_active: true, sort_order: 0, version: 1,
};
const QR = {
  id: "aaaaaaaa-0000-4000-8000-000000000012", kind: "qr", label: "DuitNow",
  details: { duitnow_id: "DN-5120000099", account_name: "Kedai Contoh Sdn Bhd" },
  instructions: "Any bank app can pay a DuitNow ID.", is_active: true, sort_order: 1, version: 1,
};

test("the method preview is the customer's line, word for word as the server's method_line (English)", () => {
  // Expected strings copied from tests/test_payment_copy.py test_method_lines_in_both_languages.
  assert.equal(view.methodPreview(TRANSFER), "Bank transfer: Bank Contoh, account 512000000012, name Kedai Contoh.");
  assert.equal(view.methodPreview(QR),
    "DuitNow: DN-5120000099, name Kedai Contoh Sdn Bhd. Any bank app can pay a DuitNow ID.");
  // Instructions follow after one space, trimmed; blank instructions add nothing.
  assert.equal(view.methodPreview({ ...TRANSFER, instructions: "  Put the reference.  " }),
    "Bank transfer: Bank Contoh, account 512000000012, name Kedai Contoh. Put the reference.");
  assert.equal(view.methodPreview({ ...QR, instructions: "   " }), "DuitNow: DN-5120000099, name Kedai Contoh Sdn Bhd.");
  // Cash (made elsewhere; never offered here) is listed by its label.
  assert.equal(view.methodPreview({ ...TRANSFER, kind: "cash", label: "At the shop", details: {} }), "Cash: At the shop.");
  // A draft previews the same way, from its trimmed fields.
  const draft = { ...view.methodDraft(TRANSFER), bank_name: "  Bank Contoh ", instructions: " Any bank. " };
  assert.equal(view.draftPreview(draft),
    "Bank transfer: Bank Contoh, account 512000000012, name Kedai Contoh. Any bank.");
});

test("reminder days read as one plain sentence", () => {
  assert.equal(view.reminderDaysLabel([-7, 0, 3, 7]), "7 days before, on the due day, 3 and 7 days after");
  assert.equal(view.reminderDaysLabel([7, -7, 3, 0]), "7 days before, on the due day, 3 and 7 days after");
  assert.equal(view.reminderDaysLabel([-1]), "1 day before");
  assert.equal(view.reminderDaysLabel([1]), "1 day after");
  assert.equal(view.reminderDaysLabel([0]), "on the due day");
  assert.equal(view.reminderDaysLabel([-14, -3, 1, 2, 30]), "14 and 3 days before, 1, 2 and 30 days after");
  assert.equal(view.reminderDaysLabel([]), "no reminders");
  // The form's sentence: an empty list says so plainly instead of "Reminders go out no reminders."
  assert.equal(view.remindersSentence([-7, 0, 3, 7]), "Reminders go out 7 days before, on the due day, 3 and 7 days after.");
  assert.equal(view.remindersSentence([1]), "Reminders go out 1 day after.");
  assert.equal(view.remindersSentence([]), "No reminders are sent.");
  assert.equal(view.reminderDayLabel(-7), "7 days before");
  assert.equal(view.reminderDayLabel(0), "On the due day");
  assert.equal(view.reminderDayLabel(1), "1 day after");
});

test("the switch sentence follows the lead days", () => {
  assert.equal(view.leadSentence(7),
    "When this is on, invoices are made 7 days before each renewal and customers are reminded automatically.");
  assert.equal(view.leadSentence(1),
    "When this is on, invoices are made 1 day before each renewal and customers are reminded automatically.");
  assert.equal(view.shownLeadDays(" 5 ", 7), 5);
  for (const typed of ["", "0", "31", "x"]) assert.equal(view.shownLeadDays(typed, 7), 7, typed);
  assert.equal(view.pauseLine(14), "Pause a subscription when an invoice is overdue for 14 days.");
  assert.equal(view.pauseLine(1), "Pause a subscription when an invoice is overdue for 1 day.");
  assert.equal(view.pauseLine(null), "Subscriptions are not paused when an invoice is overdue.");
});

test("an unsaved switch says what is saved", () => {
  assert.equal(view.switchSavedLine(true, true), null);
  assert.equal(view.switchSavedLine(false, false), null);
  assert.equal(view.switchSavedLine(true, false), "Not saved yet: payments are currently off.");
  assert.equal(view.switchSavedLine(false, true), "Not saved yet: payments are currently on.");
});

test("reminder chips: up to 6 distinct whole days from -30 to 60, kept in order", () => {
  assert.deepEqual(view.addReminderDay([-7, 0, 3, 7], "14"), { days: [-7, 0, 3, 7, 14] });
  assert.deepEqual(view.addReminderDay([0, 3], " -3 "), { days: [-3, 0, 3] });
  assert.deepEqual(view.addReminderDay([], "+5"), { days: [5] });
  for (const bad of ["", "abc", "1.5", "-31", "61", "1e1"]) {
    assert.deepEqual(view.addReminderDay([0], bad), { error: "Choose a whole number from -30 to 60." }, bad);
  }
  assert.deepEqual(view.addReminderDay([-7, 0, 3], "3"), { error: "That day is already on the list." });
  assert.deepEqual(view.addReminderDay([1, 2, 3, 4, 5, 6], "7"), { error: "Up to 6 reminder days." });
  assert.deepEqual(view.addReminderDay([-30], "60"), { days: [-30, 60] });
});

test("the settings form makes the PUT body or says what to fix", () => {
  const saved = { enabled: false, lead_days: 7, reminder_days: [-7, 0, 3, 7], suspend_after_days: null, version: 0 };
  const draft = view.settingsDraft(saved);
  assert.deepEqual(draft, { enabled: false, lead_days: "7", reminder_days: [-7, 0, 3, 7], suspend_after_days: "" });
  // Blank pause is off (null); the version read is sent back.
  assert.deepEqual(view.settingsBody({ ...draft, enabled: true }, 0), {
    body: { enabled: true, lead_days: 7, reminder_days: [-7, 0, 3, 7], suspend_after_days: null, version: 0 },
  });
  assert.deepEqual(view.settingsBody({ ...draft, lead_days: " 30 ", suspend_after_days: "14", reminder_days: [3, -1] }, 4), {
    body: { enabled: false, lead_days: 30, reminder_days: [-1, 3], suspend_after_days: 14, version: 4 },
  });
  assert.deepEqual(view.settingsDraft({ ...saved, suspend_after_days: 21 }).suspend_after_days, "21");
  for (const bad of ["0", "31", "", "2.5", "x"]) {
    assert.deepEqual(view.settingsBody({ ...draft, lead_days: bad }, 0),
      { errors: { lead_days: "Choose a whole number from 1 to 30." } }, bad);
  }
  for (const bad of ["0", "91", "-1", "x"]) {
    assert.deepEqual(view.settingsBody({ ...draft, suspend_after_days: bad }, 0),
      { errors: { suspend_after_days: "Leave it blank, or choose a whole number from 1 to 90." } }, bad);
  }
});

test("only Bank transfer and DuitNow can be added; Cash waits for Payments 2", () => {
  assert.deepEqual(view.NEW_METHOD_KINDS, [
    { value: "transfer", label: "Bank transfer" },
    { value: "qr", label: "DuitNow" },
  ]);
  assert.equal(view.hasPayableMethod([TRANSFER]), true);
  assert.equal(view.hasPayableMethod([{ ...TRANSFER, is_active: false }, { ...QR, is_active: false }]), false);
  assert.equal(view.hasPayableMethod([{ ...TRANSFER, kind: "cash", details: {} }]), false);
  assert.equal(view.hasPayableMethod([]), false);
});

test("a new method's body has only the kind's details, trimmed; bad fields are named", () => {
  const transfer = {
    ...view.emptyMethodDraft("transfer"),
    label: "  Main account ", bank_name: " Bank Contoh ", account_name: " Kedai Contoh ",
    account_number: " 512000000012 ", duitnow_id: "left over", instructions: "  Put the reference.  ",
  };
  assert.deepEqual(view.checkMethod(transfer), {});
  assert.deepEqual(view.newMethodBody(transfer), {
    kind: "transfer", label: "Main account",
    details: { bank_name: "Bank Contoh", account_name: "Kedai Contoh", account_number: "512000000012" },
    instructions: "Put the reference.", is_active: true,
  });
  const qr = { ...view.emptyMethodDraft("qr"), label: "DuitNow", duitnow_id: "DN-5120000099", account_name: "Kedai Contoh" };
  assert.deepEqual(view.newMethodBody(qr), {
    kind: "qr", label: "DuitNow", details: { duitnow_id: "DN-5120000099", account_name: "Kedai Contoh" },
    instructions: "", is_active: true,
  });
  assert.deepEqual(view.checkMethod(view.emptyMethodDraft("transfer")), {
    label: "Write a label of up to 60 characters.",
    bank_name: "Write the bank's name, up to 60 characters.",
    account_name: "Write the account name, up to 80 characters.",
    account_number: "Use digits only, 6 to 20 of them.",
  });
  assert.equal(view.newMethodBody(view.emptyMethodDraft("transfer")), null);
  assert.deepEqual(view.checkMethod(view.emptyMethodDraft("qr")), {
    label: "Write a label of up to 60 characters.",
    duitnow_id: "Use 4 to 40 letters, digits or dashes.",
    account_name: "Write the account name, up to 80 characters.",
  });
  const tooLong = { ...transfer, label: "x".repeat(61), bank_name: "b".repeat(61), account_name: "n".repeat(81),
    account_number: "5120 0000 0012", instructions: "i".repeat(501) };
  assert.deepEqual(Object.keys(view.checkMethod(tooLong)).sort(),
    ["account_name", "account_number", "bank_name", "instructions", "label"]);
  assert.equal(view.checkMethod(tooLong).instructions, "Keep the instructions to 500 characters.");
  // A pasted 600-character instruction is refused whole, never cut to 500 (the text box has no maxLength).
  assert.equal(view.checkMethod({ ...transfer, instructions: "i".repeat(600) }).instructions,
    "Keep the instructions to 500 characters.");
  assert.deepEqual(view.checkMethod({ ...transfer, instructions: "i".repeat(500) }), {});
  for (const id of ["DN1", "DN 512", "x".repeat(41), "DN_512"]) {
    assert.ok(view.checkMethod({ ...qr, duitnow_id: id }).duitnow_id, id);
  }
  assert.deepEqual(view.checkMethod({ ...transfer, account_number: "123456" }), {});
  assert.ok(view.checkMethod({ ...transfer, account_number: "12345" }).account_number);
  assert.ok(view.checkMethod({ ...transfer, account_number: "1".repeat(21) }).account_number);
  // A pasted number with spaces around it is checked whole: 21 digits are refused, never cut to 20.
  assert.equal(view.checkMethod({ ...transfer, account_number: `  ${"5".repeat(21)} ` }).account_number,
    "Use digits only, 6 to 20 of them.");
  assert.deepEqual(view.checkMethod({ ...transfer, account_number: `  ${"5".repeat(20)} ` }), {});
});

test("an edit sends only what changed, with the version it was read at", () => {
  const draft = view.methodDraft(TRANSFER);
  assert.deepEqual(draft, {
    kind: "transfer", label: "Bank", bank_name: "Bank Contoh", account_name: "Kedai Contoh",
    account_number: "512000000012", duitnow_id: "", instructions: "", is_active: true,
  });
  assert.equal(view.methodChanges(draft, TRANSFER), null);
  assert.deepEqual(view.methodChanges({ ...draft, is_active: false }, TRANSFER), { is_active: false, version: 1 });
  assert.deepEqual(view.methodChanges({ ...draft, label: " Main ", instructions: "Thanks." }, { ...TRANSFER, version: 4 }),
    { label: "Main", instructions: "Thanks.", version: 4 });
  // Details are replaced whole.
  assert.deepEqual(view.methodChanges({ ...draft, account_number: "512000000099" }, TRANSFER), {
    details: { bank_name: "Bank Contoh", account_name: "Kedai Contoh", account_number: "512000000099" }, version: 1,
  });
  assert.deepEqual(view.methodChanges({ ...view.methodDraft(QR), duitnow_id: "DN-5120000098" }, QR), {
    details: { duitnow_id: "DN-5120000098", account_name: "Kedai Contoh Sdn Bhd" }, version: 1,
  });
  // A cash method (made elsewhere) keeps no details to change.
  const cash = { ...TRANSFER, kind: "cash", label: "At the shop", details: {} };
  assert.deepEqual(view.checkMethod(view.methodDraft(cash)), {});
  assert.deepEqual(view.methodChanges({ ...view.methodDraft(cash), label: "Shop" }, cash), { label: "Shop", version: 1 });
});

test("the page view comes from ?view=", () => {
  assert.equal(view.paymentsView("settings"), "settings");
  for (const other of [null, undefined, "", "invoices", "SETTINGS", "x"]) assert.equal(view.paymentsView(other), "invoices");
});

test("a saved method replaces its row, or joins the end of the list", () => {
  const other = { ...QR, id: "99999999-9999-4999-8999-999999999999" };
  const renamed = { ...TRANSFER, label: "Renamed", version: TRANSFER.version + 1 };
  const list = [TRANSFER, other];
  assert.deepEqual(view.upsertMethod(list, renamed), [renamed, other]);
  assert.deepEqual(list, [TRANSFER, other], "the list it was given is not changed");
  const added = { ...TRANSFER, id: "88888888-8888-4888-8888-888888888888", label: "New" };
  assert.deepEqual(view.upsertMethod(list, added), [TRANSFER, other, added]);
  assert.deepEqual(view.upsertMethod([], added), [added]);
});
