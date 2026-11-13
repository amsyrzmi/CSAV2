/**
 * Pure rules for the Payments page: the status chips, the Needs attention groups, the plain
 * line for a reminder, amounts written from their decimal string, and the mark-paid and void
 * bodies. Money is never turned into a JS number here: the API sends it as a string.
 */
import type {
  AttentionKind,
  InvoiceFilter,
  InvoiceOut,
  InvoiceStatus,
  MethodKind,
  PaymentAttentionItem,
  ReminderChannel,
  ReminderOut,
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
