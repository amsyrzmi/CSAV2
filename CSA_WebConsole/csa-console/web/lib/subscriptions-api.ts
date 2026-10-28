import { api, patch, post } from "./api";
import type { ProposalOut } from "./workspace-api";

/** Mirrors of csa/subscriptions/contracts.py. Decimals arrive as JSON strings. */
export type SubscriptionStatus = "active" | "paused" | "cancelled";
export type BillingInterval = "month" | "year";
type Amount = string | number;

export interface PlanOut {
  code: string;
  name: string;
  price: Amount;
  currency: string;
  billing_interval: BillingInterval;
  features: string[];
  sort_order: number;
  is_active: boolean;
}

export interface PlanIn {
  code: string;
  name: string;
  price: Amount;
  currency?: string;
  billing_interval?: BillingInterval;
  features?: string[];
  sort_order?: number;
}

export interface PlanPatch {
  name?: string | null;
  price?: Amount | null;
  billing_interval?: BillingInterval | null;
  features?: string[] | null;
  sort_order?: number | null;
  is_active?: boolean | null;
}

export interface CustomerRef {
  id: string;
  full_name: string | null;
  phone: string | null;
  email: string | null;
}

export interface SubscriptionOut {
  id: string;
  customer: CustomerRef;
  plan: string;
  plan_name: string | null;
  status: SubscriptionStatus;
  price: Amount;
  currency: string;
  started_at: string;
  renews_at: string | null;
  paused_at: string | null;
  cancelled_at: string | null;
  resume_at: string | null;
  pending_plan: string | null;
  pending_effective_at: string | null;
  cancel_at_period_end: boolean;
  version: number;
}

export interface SubscriptionPage {
  items: SubscriptionOut[];
  next_cursor: string | null;
}

export interface SubscriptionEventOut {
  id: number;
  actor: string;
  action: string;
  from_status: string | null;
  to_status: string | null;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface SubscriptionDetail {
  subscription: SubscriptionOut;
  events: SubscriptionEventOut[];
}

export interface SubscriptionCreateIn {
  contact_id: string;
  plan_code: string;
  started_at?: string | null;
  renews_at?: string | null;
}

export type SubscriptionAction =
  | "pause"
  | "resume"
  | "change_plan"
  | "cancel_at_period_end"
  | "cancel_now"
  | "extend_renewal";

export interface ActionIn {
  action: SubscriptionAction;
  resume_at?: string | null;
  plan_code?: string | null;
  effective_at?: string | null;
  renews_at?: string | null;
  price_override?: Amount | null;
  reason?: string | null;
}

export type RequestAction = "request_pause" | "request_cancel" | "request_change";

export interface RequestOut {
  id: string;
  /** Null when the customer's request named no subscription: it can only be declined. */
  subscription_id: string | null;
  customer: CustomerRef;
  action: RequestAction;
  plan_code: string | null;
  reason: string | null;
  requested_at: string;
  decision: "approved" | "declined" | null;
  decided_at: string | null;
  decided_by: string | null;
  note: string | null;
  proposal_id: string | null;
}

export interface RequestPage {
  items: RequestOut[];
  next_cursor: string | null;
}

export interface DecideIn {
  decision: "approve" | "decline";
  note?: string | null;
  resume_at?: string | null;
}

export interface SubscriptionQuery {
  q?: string;
  status?: SubscriptionStatus | "";
  plan?: string;
  before?: string | null;
}

const BASE = "/console/v1/subscriptions";

/**
 * `tenantId` is null for a tenant member (the session names the tenant) and the target
 * tenant for a platform admin, who must always send `client_id`. Empty values are left out.
 */
export function subscriptionsPath(
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

export function listPlans(tenantId: string | null, includeArchived: boolean): Promise<PlanOut[]> {
  return api(subscriptionsPath(tenantId, "/plans", { include_archived: includeArchived ? "true" : null }));
}

export function createPlan(tenantId: string | null, body: PlanIn): Promise<PlanOut> {
  return post(subscriptionsPath(tenantId, "/plans"), body);
}

export function updatePlan(tenantId: string | null, code: string, body: PlanPatch): Promise<PlanOut> {
  return patch(subscriptionsPath(tenantId, `/plans${id(code)}`), body);
}

export function searchSubscriptions(
  tenantId: string | null,
  { q, status, plan, before }: SubscriptionQuery,
): Promise<SubscriptionPage> {
  return api(subscriptionsPath(tenantId, "", { q: q?.trim(), status, plan, before }));
}

export function getSubscription(tenantId: string | null, subscriptionId: string): Promise<SubscriptionDetail> {
  return api(subscriptionsPath(tenantId, id(subscriptionId)));
}

/** The tenant's customers by name, email or phone, to put one on a plan (everyone, by name, without `q`). */
export function searchCustomers(tenantId: string | null, q: string, limit?: number): Promise<CustomerRef[]> {
  return api(subscriptionsPath(tenantId, "/customers", { q: q.trim(), limit: limit ? String(limit) : null }));
}

export function createSubscription(tenantId: string | null, body: SubscriptionCreateIn): Promise<SubscriptionOut> {
  return post(subscriptionsPath(tenantId), body);
}

/** Proposes the change; nothing happens until the same login confirms the returned card. */
export function proposeAction(tenantId: string | null, subscriptionId: string, body: ActionIn): Promise<ProposalOut> {
  return post(subscriptionsPath(tenantId, `${id(subscriptionId)}/actions`), body);
}

export function listRequests(
  tenantId: string | null,
  status: "open" | "decided",
  before?: string | null,
): Promise<RequestPage> {
  return api(subscriptionsPath(tenantId, "/requests", { status, before }));
}

/** Declines, or approves by proposing and confirming the customer's change in one call. */
export function decideRequest(tenantId: string | null, requestId: string, body: DecideIn): Promise<RequestOut> {
  return post(subscriptionsPath(tenantId, `/requests${id(requestId)}/decide`), body);
}

// ---- Display helpers (docs/WRITING_GUIDE.md: "RM 59.90", "27 Oct 2026, 09:15") ----

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;

function amount(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

/** Prices may arrive as JSON strings (Decimal) or numbers (job payloads); both format. */
export function money(value: unknown, currency = "MYR"): string {
  const parsed = amount(value);
  if (parsed === null || (typeof value === "string" && !value.trim())) return "—";
  const text = parsed.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `${currency === "MYR" ? "RM" : currency} ${text}`;
}

function parts(value: string, timeZone?: string) {
  const plain = DATE_ONLY.exec(value);
  if (plain) return { year: plain[1], month: Number(plain[2]), day: Number(plain[3]), hour: "00", minute: "00" };
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  const found = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone, year: "numeric", month: "numeric", day: "numeric",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(date).map((part) => [part.type, part.value]),
  );
  return {
    year: found.year, month: Number(found.month), day: Number(found.day),
    hour: found.hour.padStart(2, "0"), minute: found.minute.padStart(2, "0"),
  };
}

/** "27 Oct 2026"; a date-only value is shown as written, a timestamp in `timeZone`. */
export function formatDay(value: unknown, timeZone?: string): string {
  const found = typeof value === "string" ? parts(value, timeZone) : null;
  return found ? `${found.day} ${MONTHS[found.month - 1]} ${found.year}` : "—";
}

/** "27 Oct 2026, 09:15" (24-hour). */
export function formatDateTime(value: unknown, timeZone?: string): string {
  const found = typeof value === "string" ? parts(value, timeZone) : null;
  return found ? `${found.day} ${MONTHS[found.month - 1]} ${found.year}, ${found.hour}:${found.minute}` : "—";
}

export const STATUS_LABELS: Record<SubscriptionStatus, string> = {
  active: "Active",
  paused: "Paused",
  cancelled: "Cancelled",
};

// Mirrors csa/subscriptions/executor.py ALLOWED_FROM; the server refuses anything else.
const ALLOWED_FROM: Record<SubscriptionAction, SubscriptionStatus[]> = {
  pause: ["active"],
  resume: ["paused"],
  change_plan: ["active", "paused"],
  cancel_at_period_end: ["active"],
  cancel_now: ["active", "paused"],
  extend_renewal: ["active"],
};

/**
 * The actions the subscription's current state allows, in button order. A period-end cancel
 * needs a renewal date (the period never ends without one) and is offered once.
 */
export function availableActions(
  subscription: Pick<SubscriptionOut, "status" | "cancel_at_period_end" | "renews_at">,
): SubscriptionAction[] {
  return (Object.keys(ALLOWED_FROM) as SubscriptionAction[]).filter(
    (action) =>
      ALLOWED_FROM[action].includes(subscription.status) &&
      !(action === "cancel_at_period_end" && (subscription.cancel_at_period_end || !subscription.renews_at)),
  );
}

/** Tomorrow's date as "YYYY-MM-DD" on this browser's calendar: the earliest resume date. */
export function dayAfter(today: Date = new Date()): string {
  const next = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${next.getFullYear()}-${pad(next.getMonth() + 1)}-${pad(next.getDate())}`;
}

const SYSTEM_ACTOR = "system:subscriptions_due";

function str(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

/**
 * One plain sentence for a timeline event, from its action and payload. Covers the
 * executor's past-tense names and the due job's (`cancelled_at_period_end`, `plan_changed`,
 * `pending_change_skipped`, `resumed`, `renewed`). `planName` turns a plan code into its name.
 */
export function eventSentence(
  event: SubscriptionEventOut,
  planName: (code: string) => string = (code) => code,
  timeZone?: string,
): string {
  const p = event.payload ?? {};
  const plan = (key: string) => {
    const code = str(p[key]);
    return code ? planName(code) : "another plan";
  };
  const day = (key: string) => formatDay(p[key], timeZone);
  const automatic = event.actor === SYSTEM_ACTOR;
  let sentence: string;
  switch (event.action) {
    case "created":
      sentence = `Subscription started on the ${plan("plan")} plan.`;
      break;
    case "paused":
      sentence = str(p.resume_at) ? `Paused until ${day("resume_at")}.` : "Paused until someone resumes it.";
      break;
    case "resumed": {
      const renews = str(p.new_renews_at) ? day("new_renews_at") : str(p.renews_at) ? day("renews_at") : null;
      sentence = `${automatic ? "Resumed automatically on the planned date." : "Resumed."}${
        renews ? ` Next renewal on ${renews}.` : ""}`;
      break;
    }
    case "plan_changed": {
      const from = `${plan("old_plan")} (${money(p.old_price)})`;
      if (automatic) sentence = `Scheduled plan change applied: ${from} to ${plan("new_plan")} (${money(p.new_price)}).`;
      else if (p.price_override === true)
        sentence = `Plan changed from ${from} to ${plan("new_plan")} at a special price of ${money(p.new_price)}.`;
      else sentence = `Plan changed from ${from} to ${plan("new_plan")} (${money(p.new_price)}).`;
      break;
    }
    case "plan_change_scheduled":
      sentence = `Plan change from ${plan("old_plan")} to ${plan("new_plan")} scheduled for ${day("effective_at")}.`;
      break;
    case "pending_change_skipped":
      sentence = `The scheduled change to ${plan("plan")} was not applied. The plan is archived or the subscription had ended.`;
      break;
    case "cancel_scheduled":
      sentence = str(p.renews_at)
        ? `Set to cancel when the period ends on ${day("renews_at")}.`
        : "Set to cancel at the end of the period.";
      break;
    case "cancelled_at_period_end":
      sentence = "Cancelled at the end of the period.";
      break;
    case "cancelled":
      sentence = `Cancelled straight away.${
        str(p.cleared_pending_plan) ? ` The scheduled change to ${plan("cleared_pending_plan")} was dropped.` : ""}`;
      break;
    case "renewal_extended":
      sentence = `Renewal moved from ${day("old_renews_at")} to ${day("new_renews_at")}.`;
      break;
    case "renewed":
      sentence = `Renewed for another period. Next renewal on ${day("renews_at")}.`;
      break;
    default:
      sentence = "Subscription updated.";
  }
  const reason = str(p.reason);
  return reason ? `${sentence} Reason: ${reason}` : sentence;
}

/** What the customer asked for, in plain words. */
export function requestSentence(request: Pick<RequestOut, "action" | "plan_code">, planName: (code: string) => string = (code) => code): string {
  switch (request.action) {
    case "request_pause":
      return "Pause the subscription";
    case "request_cancel":
      return "Cancel at the end of the period";
    case "request_change":
      return request.plan_code ? `Change to the ${planName(request.plan_code)} plan` : "Change plan";
  }
}
