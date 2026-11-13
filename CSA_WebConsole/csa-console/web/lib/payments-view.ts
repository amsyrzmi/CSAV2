/**
 * Pure rules for the Payments page: the status chips, the Needs attention groups, the plain
 * line for a reminder, amounts written from their decimal string, and the mark-paid and void
 * bodies. Money is never turned into a JS number here: the API sends it as a string.
 */
import type {
  AttentionKind,
  InvoiceFilter,
  InvoiceOut,
  InvoicePage,
  InvoiceStatus,
  MethodKind,
  PaymentAttentionItem,
  PaymentMethod,
  PaymentMethodIn,
  PaymentMethodPatch,
  PaymentSettings,
  PaymentSettingsIn,
  QrDetails,
  ReminderChannel,
  ReminderOut,
  TransferDetails,
  VoidIn,
} from "@/lib/payments-api";

const CHIPS: { value: InvoiceFilter; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "overdue", label: "Overdue" },
  { value: "proof_submitted", label: "Customer says paid" },
  { value: "paid", label: "Paid" },
  { value: "void", label: "Void" },
  { value: "all", label: "All" },
];

/** The Invoices status chips; each value is the list route's `status` filter. */
export function filterChips(): { value: InvoiceFilter; label: string }[] {
  return CHIPS.map((chip) => ({ ...chip }));
}

const GROUPS: { kind: AttentionKind; label: string }[] = [
  { kind: "proof_submitted", label: "Customer says paid" },
  { kind: "needs_nudge", label: "Needs a nudge" },
  { kind: "reminder_failed", label: "Reminder failed" },
];

export interface AttentionGroup {
  kind: AttentionKind;
  label: string;
  items: PaymentAttentionItem[];
}

/** The three Needs attention groups in their fixed order; each keeps the server's order inside. */
export function attentionGroups(items: PaymentAttentionItem[]): AttentionGroup[] {
  return GROUPS.map(({ kind, label }) => ({ kind, label, items: items.filter((item) => item.kind === kind) }));
}

/** The badge figure: invoices that need attention (one invoice can be in two groups). */
export function attentionCount(items: PaymentAttentionItem[]): number {
  return new Set(items.map((item) => item.invoice.id)).size;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "12 Nov" in `timeZone` (the browser's when left out); empty for a value that is not a time. */
function shortDay(value: string | null, timeZone?: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const found = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", { timeZone, month: "numeric", day: "numeric" })
      .formatToParts(date).map((part) => [part.type, part.value]),
  );
  return `${Number(found.day)} ${MONTHS[Number(found.month) - 1]}`;
}

const CHANNELS: Record<ReminderChannel, string> = {
  telegram: "Telegram",
  whatsapp: "WhatsApp",
  email: "Email",
  none: "No channel",
};

/** One reminder in plain words: its channel and what happened, never the address it went to. */
export function reminderLine(reminder: ReminderOut, timeZone?: string): string {
  if (reminder.status === "no_channel") return "No channel: message them yourself";
  const channel = CHANNELS[reminder.channel];
  switch (reminder.status) {
    case "sent":
      return `${channel} · sent ${shortDay(reminder.sent_at ?? reminder.created_at, timeZone)}`;
    case "failed":
      return `${channel} · failed ${shortDay(reminder.created_at, timeZone)}`;
    case "queued":
    case "sending":
      return `${channel} · waiting to send`;
    case "dropped":
    case "skipped":
      return `${channel} · not sent`;
  }
}

const DECIMAL = /^(\d+)(?:\.(\d+))?$/;

/**
 * "RM 1,234.50" from the decimal string, digit by digit (a JS number would round a large
 * amount). MYR is written "RM" as on the Subscriptions page; other currencies by their code.
 */
export function formatAmount(amount: unknown, currency: string): string {
  const found = typeof amount === "string" ? DECIMAL.exec(amount.trim()) : null;
  if (!found) return "—";
  const whole = found[1].replace(/^0+(?=\d)/, "").replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const cents = (found[2] ?? "").padEnd(2, "0");
  return `${currency === "MYR" ? "RM" : currency} ${whole}.${cents}`;
}

const pad = (value: number) => String(value).padStart(2, "0");

/** Today on this browser's calendar as "YYYY-MM-DD": the date field's default and its maximum. */
export function todayInput(now: Date = new Date()): string {
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/**
 * The `received_at` for a picked day: now for today (never ahead of the server's clock), else
 * midday of that day on this browser's calendar.
 */
export function receivedAt(day: string, now: Date = new Date()): string {
  if (day === todayInput(now)) return now.toISOString();
  const [year, month, date] = day.split("-").map(Number);
  return new Date(year, month - 1, date, 12, 0).toISOString();
}

export const METHOD_OPTIONS: { value: MethodKind; label: string }[] = [
  { value: "transfer", label: "Bank transfer" },
  { value: "qr", label: "DuitNow" },
  { value: "cash", label: "Cash" },
];

export function methodLabel(kind: MethodKind | null | undefined): string {
  return METHOD_OPTIONS.find((option) => option.value === kind)?.label ?? "—";
}

/** The confirm step's question before a payment is recorded. */
export function markPaidQuestion(reference: string): string {
  return `Mark invoice ${reference} paid? This starts the subscription if it is waiting.`;
}

/** The void body: the trimmed reason (1 to 300 characters), or null while it is not valid. */
export function voidBody(reason: string): VoidIn | null {
  const trimmed = reason.trim();
  return trimmed && trimmed.length <= 300 ? { reason: trimmed } : null;
}

const OPEN: InvoiceStatus[] = ["issued", "overdue", "proof_submitted", "cash_pending"];

/** Only an invoice the company still waits for can be marked paid. */
export function canMarkPaid(status: InvoiceStatus): boolean {
  return OPEN.includes(status);
}

/** Anything not paid and not already void can be voided. */
export function canVoid(status: InvoiceStatus): boolean {
  return status !== "paid" && status !== "void";
}

export function invoiceName(invoice: Pick<InvoiceOut, "reference">): string {
  return invoice.reference || "Invoice without a reference";
}

export function customerLabel(invoice: Pick<InvoiceOut, "contact">): string {
  if (!invoice.contact) return "No customer";
  return invoice.contact.full_name || "Unnamed customer";
}

/** The invoice list as read, tagged with the filter (`listKey`) it was read for. */
export interface InvoiceList {
  key: string;
  rows: InvoiceOut[];
  next: string | null;
}

/** Names one list: a page read for another key never joins it. */
export function listKey(tenantId: string | null, status: InvoiceFilter, q: string, revision: number): string {
  return JSON.stringify([tenantId, status, q, revision]);
}

export function firstPage(key: string, page: InvoicePage): InvoiceList {
  return { key, rows: page.items, next: page.next_before };
}

/** Adds a "Load more" page, unless the filter changed while it was out: then it is dropped. */
export function appendPage(current: InvoiceList | null, key: string, page: InvoicePage): InvoiceList | null {
  if (!current || current.key !== key) return current;
  return { key, rows: [...current.rows, ...page.items], next: page.next_before };
}

/** The cursor "Load more" may send now: none while the rows shown belong to another filter. */
export function moreCursor(current: InvoiceList | null, key: string): string | null {
  return current && current.key === key ? current.next : null;
}

// ---------------------------------------------------------------- Settings and methods

export type PaymentsView = "invoices" | "settings";

/** The page's view from `?view=`: Settings only when asked for by name. */
export function paymentsView(value: string | null | undefined): PaymentsView {
  return value === "settings" ? "settings" : "invoices";
}

const dayWord = (count: number) => (count === 1 ? "day" : "days");

/** "3", "3 and 7", "1, 3 and 7". */
function joinWithAnd(numbers: number[]): string {
  if (numbers.length < 2) return numbers.join("");
  return `${numbers.slice(0, -1).join(", ")} and ${numbers[numbers.length - 1]}`;
}

/** The days in plain words, earliest first: "7 days before, on the due day, 3 and 7 days after". */
export function reminderDaysLabel(days: number[]): string {
  const sorted = [...new Set(days)].sort((a, b) => a - b);
  const before = sorted.filter((day) => day < 0).map((day) => -day);
  const after = sorted.filter((day) => day > 0);
  const parts: string[] = [];
  if (before.length) parts.push(`${joinWithAnd(before)} ${dayWord(before.length === 1 ? before[0] : 2)} before`);
  if (sorted.includes(0)) parts.push("on the due day");
  if (after.length) parts.push(`${joinWithAnd(after)} ${dayWord(after.length === 1 ? after[0] : 2)} after`);
  return parts.join(", ") || "no reminders";
}

/** One reminder chip: "7 days before", "On the due day", "1 day after". */
export function reminderDayLabel(day: number): string {
  if (day === 0) return "On the due day";
  const count = Math.abs(day);
  return `${count} ${dayWord(count)} ${day < 0 ? "before" : "after"}`;
}

/** The sentence under the switch; the number is the saved lead days. */
export function leadSentence(leadDays: number): string {
  return `When this is on, invoices are made ${leadDays} ${dayWord(leadDays)} before each renewal and customers are reminded automatically.`;
}

/** The lead days the switch sentence names: the typed value while it is valid, else the saved one. */
export function shownLeadDays(typed: string, saved: number): number {
  return wholeIn(typed, 1, 30) ?? saved;
}

/** The pause setting as staff read it. */
export function pauseLine(days: number | null): string {
  if (days === null) return "Subscriptions are not paused when an invoice is overdue.";
  return `Pause a subscription when an invoice is overdue for ${days} ${dayWord(days)}`;
}

const MAX_REMINDERS = 6;
const REMINDER_RANGE = "Choose a whole number from -30 to 60.";

/** Adds one reminder day typed by the admin (csa/payments/contracts.py `ReminderDay`), kept in order. */
export function addReminderDay(days: number[], text: string): { days: number[] } | { error: string } {
  const typed = text.trim();
  if (!/^[+-]?\d{1,3}$/.test(typed)) return { error: REMINDER_RANGE };
  const day = Number(typed);
  if (day < -30 || day > 60) return { error: REMINDER_RANGE };
  if (days.includes(day)) return { error: "That day is already on the list." };
  if (days.length >= MAX_REMINDERS) return { error: `Up to ${MAX_REMINDERS} reminder days.` };
  return { days: [...days, day].sort((a, b) => a - b) };
}

/** The settings form as typed: numbers stay text until the save checks them. */
export interface SettingsDraft {
  enabled: boolean;
  lead_days: string;
  reminder_days: number[];
  /** Blank is off: subscriptions are never paused. */
  suspend_after_days: string;
}

export type SettingsErrors = Partial<Record<"lead_days" | "suspend_after_days", string>>;

export function settingsDraft(settings: PaymentSettings): SettingsDraft {
  return {
    enabled: settings.enabled,
    lead_days: String(settings.lead_days),
    reminder_days: [...settings.reminder_days],
    suspend_after_days: settings.suspend_after_days === null ? "" : String(settings.suspend_after_days),
  };
}

/** A whole number from `low` to `high`, or null. */
function wholeIn(text: string, low: number, high: number): number | null {
  const typed = text.trim();
  if (!/^\d{1,3}$/.test(typed)) return null;
  const value = Number(typed);
  return value >= low && value <= high ? value : null;
}

/** The PUT body (csa/payments/contracts.py `PaymentSettingsIn`) with the version read, or what to fix. */
export function settingsBody(
  draft: SettingsDraft,
  version: number,
): { body: PaymentSettingsIn } | { errors: SettingsErrors } {
  const errors: SettingsErrors = {};
  const leadDays = wholeIn(draft.lead_days, 1, 30);
  if (leadDays === null) errors.lead_days = "Choose a whole number from 1 to 30.";
  const pause = draft.suspend_after_days.trim() ? wholeIn(draft.suspend_after_days, 1, 90) : null;
  if (draft.suspend_after_days.trim() && pause === null) {
    errors.suspend_after_days = "Leave it blank, or choose a whole number from 1 to 90.";
  }
  if (leadDays === null || errors.suspend_after_days) return { errors };
  return {
    body: {
      enabled: draft.enabled,
      lead_days: leadDays,
      reminder_days: [...draft.reminder_days].sort((a, b) => a - b),
      suspend_after_days: pause,
      version,
    },
  };
}

/** The kinds an admin can add. Cash is Payments 2 (it is paid through a booking). */
export const NEW_METHOD_KINDS: { value: "transfer" | "qr"; label: string }[] = [
  { value: "transfer", label: "Bank transfer" },
  { value: "qr", label: "DuitNow" },
];

/** Whether payments can be switched on: an active bank transfer or DuitNow method (cash does not count). */
export function hasPayableMethod(methods: PaymentMethod[]): boolean {
  return methods.some((method) => method.is_active && (method.kind === "transfer" || method.kind === "qr"));
}

/**
 * The customer's line for one method, built by the same rule as the server's English
 * `method_line` (csa/payments/copy.py): the details, then the instructions after one space.
 */
export function methodPreview(method: Pick<PaymentMethod, "kind" | "label" | "details" | "instructions">): string {
  const details = method.details;
  let line: string;
  if (method.kind === "transfer") {
    line = `Bank transfer: ${details.bank_name}, account ${details.account_number}, name ${details.account_name}.`;
  } else if (method.kind === "qr") {
    line = `DuitNow: ${details.duitnow_id}, name ${details.account_name}.`;
  } else {
    line = `Cash: ${method.label}.`;
  }
  const instructions = method.instructions.trim();
  return instructions ? `${line} ${instructions}` : line;
}

/** The add or edit form as typed. Only the fields of its kind are sent. */
export interface MethodDraft {
  kind: MethodKind;
  label: string;
  bank_name: string;
  account_name: string;
  account_number: string;
  duitnow_id: string;
  instructions: string;
  is_active: boolean;
}

export type MethodField = "label" | "bank_name" | "account_name" | "account_number" | "duitnow_id" | "instructions";
export type MethodErrors = Partial<Record<MethodField, string>>;

export function emptyMethodDraft(kind: MethodKind): MethodDraft {
  return {
    kind, label: "", bank_name: "", account_name: "", account_number: "", duitnow_id: "", instructions: "",
    is_active: true,
  };
}

export function methodDraft(method: PaymentMethod): MethodDraft {
  const details = method.details;
  return {
    kind: method.kind,
    label: method.label,
    bank_name: details.bank_name ?? "",
    account_name: details.account_name ?? "",
    account_number: details.account_number ?? "",
    duitnow_id: details.duitnow_id ?? "",
    instructions: method.instructions,
    is_active: method.is_active,
  };
}

/** Characters as the server counts them (code points, not UTF-16 units). */
const length = (text: string) => [...text].length;
const between = (text: string, low: number, high: number) => length(text) >= low && length(text) <= high;

/** The draft's details for its kind, trimmed: transfer and DuitNow fields never mix. */
function draftDetails(draft: MethodDraft): TransferDetails | QrDetails | Record<string, never> {
  if (draft.kind === "transfer") {
    return {
      bank_name: draft.bank_name.trim(),
      account_name: draft.account_name.trim(),
      account_number: draft.account_number.trim(),
    };
  }
  if (draft.kind === "qr") return { duitnow_id: draft.duitnow_id.trim(), account_name: draft.account_name.trim() };
  return {};
}

/**
 * The same limits as csa/payments/contracts.py (`Label`, `TransferDetails`, `QrDetails`,
 * `Instructions`). The server stays the authority: its 422 `invalid_details` is shown too.
 */
export function checkMethod(draft: MethodDraft): MethodErrors {
  const errors: MethodErrors = {};
  if (!between(draft.label.trim(), 1, 60)) errors.label = "Write a label of up to 60 characters.";
  const accountName = "Write the account name, up to 80 characters.";
  if (draft.kind === "transfer") {
    if (!between(draft.bank_name.trim(), 1, 60)) errors.bank_name = "Write the bank's name, up to 60 characters.";
    if (!between(draft.account_name.trim(), 1, 80)) errors.account_name = accountName;
    if (!/^[0-9]{6,20}$/.test(draft.account_number.trim())) errors.account_number = "Use digits only, 6 to 20 of them.";
  } else if (draft.kind === "qr") {
    if (!/^[0-9A-Za-z-]{4,40}$/.test(draft.duitnow_id.trim())) {
      errors.duitnow_id = "Use 4 to 40 letters, digits or dashes.";
    }
    if (!between(draft.account_name.trim(), 1, 80)) errors.account_name = accountName;
  }
  if (length(draft.instructions.trim()) > 500) errors.instructions = "Keep the instructions to 500 characters.";
  return errors;
}

/** The POST body for a new method, or null while a field needs fixing. */
export function newMethodBody(draft: MethodDraft): PaymentMethodIn | null {
  if (Object.keys(checkMethod(draft)).length) return null;
  return {
    kind: draft.kind,
    label: draft.label.trim(),
    details: draftDetails(draft),
    instructions: draft.instructions.trim(),
    is_active: draft.is_active,
  };
}

/** The PATCH body: only what changed (details whole), with the version read; null when nothing did. */
export function methodChanges(draft: MethodDraft, method: PaymentMethod): PaymentMethodPatch | null {
  const changes: Omit<PaymentMethodPatch, "version"> = {};
  const label = draft.label.trim();
  if (label !== method.label) changes.label = label;
  if (draft.kind !== "cash") {
    const details = draftDetails(draft) as TransferDetails | QrDetails;
    const changed = Object.entries(details).some(([key, value]) => method.details[key] !== value);
    if (changed) changes.details = details;
  }
  const instructions = draft.instructions.trim();
  if (instructions !== method.instructions.trim()) changes.instructions = instructions;
  if (draft.is_active !== method.is_active) changes.is_active = draft.is_active;
  return Object.keys(changes).length ? { ...changes, version: method.version } : null;
}

/** The preview for a form, from its trimmed fields. */
export function draftPreview(draft: MethodDraft): string {
  return methodPreview({
    kind: draft.kind,
    label: draft.label.trim(),
    details: draftDetails(draft) as Record<string, string>,
    instructions: draft.instructions,
  });
}
