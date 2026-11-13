import { api, ApiError, explainedMessage, post } from "./api";

/**
 * Mirrors of csa/payments/contracts.py (docs/CONSOLE_API.md "Payments"). Money (`amount`) is a
 * decimal string with two places and stays a string here: it is never turned into a number.
 */
export type InvoiceStatus = "draft" | "issued" | "proof_submitted" | "paid" | "overdue" | "void" | "cash_pending";
export type InvoiceFilter = "open" | "overdue" | "proof_submitted" | "paid" | "void" | "all";
export type MethodKind = "qr" | "transfer" | "cash";
export type AttentionKind = "proof_submitted" | "needs_nudge" | "reminder_failed";
export type ReminderChannel = "telegram" | "whatsapp" | "email" | "none";
export type ReminderStatus = "queued" | "sending" | "sent" | "failed" | "dropped" | "skipped" | "no_channel";

/** The invoice's customer: no phone, email or address, only the channels they are on. */
export interface ContactBrief {
  id: string;
  full_name: string | null;
  channels: string[];
}

/** An invoice as staff see it. `void_reason` and `voided_at` are staff-only. */
export interface InvoiceOut {
  id: string;
  reference: string | null;
  status: InvoiceStatus;
  amount: string;
  currency: string;
  issued_at: string;
  due_at: string | null;
  paid_at: string | null;
  period_start: string | null;
  period_end: string | null;
  subscription_id: string | null;
  /** The first line item's description, frozen at issue. */
  plan_name: string | null;
  contact: ContactBrief | null;
  /** What the customer typed when they said they paid: plain text, never markup. */
  claim_note: string | null;
  claimed_at: string | null;
  method_kind: MethodKind | null;
  void_reason: string | null;
  voided_at: string | null;
  reminded_at: string | null;
}

export interface PaymentOut {
  id: string;
  amount: string;
  currency: string;
  received_at: string;
  method_kind: MethodKind | null;
  source: "statement" | "staff" | "cash";
  matched_by: "auto" | "staff";
  /** The console user who recorded it; null for the system. */
  created_by_name: string | null;
  /** Staff-only. */
  note: string | null;
  created_at: string;
}

/** One reminder step. The address it went to is never in any response. */
export interface ReminderOut {
  step: number;
  channel: ReminderChannel;
  status: ReminderStatus;
  created_at: string;
  sent_at: string | null;
}

export interface InvoiceDetail extends InvoiceOut {
  payment: PaymentOut | null;
  /** Oldest first. */
  reminders: ReminderOut[];
}

export interface InvoicePage {
  items: InvoiceOut[];
  /** Send back unchanged as `before` for the next page; null on the last page. */
  next_before: string | null;
}

export interface MarkPaidIn {
  /** The same key again returns the first answer and records nothing. */
  request_key: string;
  method_kind: MethodKind;
  /** With a time zone; the server uses now when it is left out. */
  received_at?: string | null;
  note?: string | null;
}

export interface VoidIn {
  reason: string;
}

export interface PaymentAttentionItem {
  kind: AttentionKind;
  invoice: InvoiceOut;
  since: string;
}

export interface PaymentAttentionPage {
  items: PaymentAttentionItem[];
}

export interface InvoiceQuery {
  status?: InvoiceFilter;
  q?: string;
  before?: string | null;
  limit?: number;
}

const BASE = "/console/v1/payments";

/**
 * `tenantId` is null for a tenant member (the session names the tenant) and the target tenant
 * for a platform admin, who must always send `client_id`. Empty values are left out.
 */
export function paymentsPath(
  tenantId: string | null,
  rest = "",
  params: Record<string, string | null | undefined> = {},
): string {
  const query = new URLSearchParams();
  if (tenantId) query.set("client_id", tenantId);
  for (const [key, value] of Object.entries(params)) if (value) query.set(key, value);
  return `${BASE}${rest}${query.size ? `?${query}` : ""}`;
}

const id = (value: string) => `/${encodeURIComponent(value)}`;

/** Newest issued first. `status` defaults to `open` on the server when it is left out. */
export function listInvoices(
  tenantId: string | null,
  { status, q, before, limit }: InvoiceQuery,
): Promise<InvoicePage> {
  return api(paymentsPath(tenantId, "/invoices", {
    status, q: q?.trim(), before, limit: limit ? String(limit) : null,
  }));
}

export function getInvoice(tenantId: string | null, invoiceId: string): Promise<InvoiceDetail> {
  return api(paymentsPath(tenantId, `/invoices${id(invoiceId)}`));
}

/** Records the payment; the body carries no amount (the server uses the invoice's). */
export function markPaid(tenantId: string | null, invoiceId: string, body: MarkPaidIn): Promise<InvoiceDetail> {
  return post(paymentsPath(tenantId, `/invoices${id(invoiceId)}/mark-paid`), body);
}

export function voidInvoice(tenantId: string | null, invoiceId: string, body: VoidIn): Promise<InvoiceDetail> {
  return post(paymentsPath(tenantId, `/invoices${id(invoiceId)}/void`), body);
}

export function listAttention(tenantId: string | null): Promise<PaymentAttentionPage> {
  return api(paymentsPath(tenantId, "/attention"));
}

/** One opened "Mark paid" dialog: every submit from it sends the same request key. */
export interface MarkPaidSession {
  readonly requestKey: string;
  submit(body: Omit<MarkPaidIn, "request_key">): Promise<InvoiceDetail>;
}

/**
 * Call once when the dialog opens. A retry after a failure, or a second click, replays the
 * same `request_key`, so the server records the payment at most once; opening the dialog
 * again is a new attempt with a new key.
 */
export function openMarkPaid(
  tenantId: string | null,
  invoiceId: string,
  newKey: () => string = () => crypto.randomUUID(),
): MarkPaidSession {
  const requestKey = newKey();
  return {
    requestKey,
    submit: (body) => markPaid(tenantId, invoiceId, { ...body, request_key: requestKey }),
  };
}

export const STATUS_LABELS: Record<InvoiceStatus, string> = {
  issued: "Waiting for payment",
  overdue: "Overdue",
  proof_submitted: "Customer says paid",
  paid: "Paid",
  void: "Void",
  cash_pending: "Cash at meeting",
  draft: "Draft",
};

/** The 403 details from csa/payments/api.py `require_payments`. */
const ROLE_REFUSED = "payments access requires an administrator or staff role";
const TENANT_REFUSED = "payments tenant is not authorized";

/** Payments refusals (csa/payments/api.py and invoices.py) in plain words. */
export const PAYMENT_ERRORS: Record<string, string> = {
  invoice_already_paid: "Someone already marked this invoice paid.",
  invoice_not_payable: "This invoice can't be paid: it is void.",
  invoice_paid: "A paid invoice can't be voided.",
  received_in_future: "The payment date can't be in the future.",
  invoice_not_found: "That invoice no longer exists. Refresh the page.",
  invoice_void: "This invoice is already void.",
  request_key_reused: "This payment was already recorded on another invoice. Close the form and open it again.",
  invalid_note: "The note is too long. Keep it to 300 characters.",
  invalid_reason: "Write a reason of up to 300 characters.",
  // A platform admin on a company they cannot reach (csa/payments/api.py require_payments).
  [TENANT_REFUSED]: "You can't open Payments for this company.",
};

const GENERIC = "The request failed. Try again.";
const SERVER_FAILED = "Something went wrong on our side. Try again.";

/**
 * The sentence for a failed Payments call: a payments code, else a code another page explains,
 * else the server-failure sentence for a 5xx, else the generic sentence (never a raw code).
 */
export function paymentMessage(cause: unknown): string {
  if (!(cause instanceof ApiError)) return GENERIC;
  if (Object.hasOwn(PAYMENT_ERRORS, cause.code)) return PAYMENT_ERRORS[cause.code];
  const explained = explainedMessage(cause.code);
  if (explained) return explained;
  // A 5xx with a JSON detail carries that raw detail as its message; never show it.
  return cause.status >= 500 ? SERVER_FAILED : GENERIC;
}

/** The 403 for this login's role: the page says Payments is for admins and staff. */
export function isNoAccess(cause: unknown): boolean {
  return cause instanceof ApiError && cause.status === 403 && cause.code === ROLE_REFUSED;
}

/**
 * Any other 403 (a company this login cannot reach, an inactive package): retrying will not
 * help, so the page shows `paymentMessage` in place of the lists.
 */
export function isRefusal(cause: unknown): boolean {
  return cause instanceof ApiError && cause.status === 403 && cause.code !== ROLE_REFUSED;
}

/** Codes that mean the invoice changed under the form: someone else paid or voided it first. */
const STALE = new Set(["invoice_already_paid", "invoice_not_payable", "invoice_paid", "invoice_void"]);

/**
 * The sentence for a refusal that means the invoice changed under the form, or null. The drawer
 * reads the invoice again and keeps this sentence on show, since the form that got it closes.
 */
export function staleMessage(cause: unknown): string | null {
  return cause instanceof ApiError && STALE.has(cause.code) ? paymentMessage(cause) : null;
}
