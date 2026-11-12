import { api, patch, post } from "./api";
import type { ProposalOut } from "./workspace-api";

/** Mirrors of csa/subscriptions/contracts.py. Decimals arrive as JSON strings. */
export type SubscriptionStatus = "pending" | "active" | "paused" | "cancelled";
/** How a sign-up for a plan is decided: staff approve it, or it is approved at once. */
export type SignupMode = "staff" | "auto";
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
  signup_mode: SignupMode;
  /** False: customers cannot sign up for the plan in the chat. */
  is_signup_open: boolean;
}

export interface PlanIn {
  code: string;
  name: string;
  price: Amount;
  currency?: string;
  billing_interval?: BillingInterval;
  features?: string[];
  sort_order?: number;
  signup_mode?: SignupMode;
  is_signup_open?: boolean;
}

export interface PlanPatch {
  name?: string | null;
  price?: Amount | null;
  billing_interval?: BillingInterval | null;
  features?: string[] | null;
  sort_order?: number | null;
  is_active?: boolean | null;
  signup_mode?: SignupMode | null;
  is_signup_open?: boolean | null;
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
  | "activate"
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

export type SignupStatus = "pending" | "approved" | "declined" | "expired";

/** Mirrors SignupOut: `full_name` and `phone` are what the customer typed; `contact` is the record as it stands. */
export interface SignupOut {
  id: string;
  contact: CustomerRef;
  plan_code: string;
  plan_name: string | null;
  full_name: string;
  phone: string;
  /** The contact's phone was verified by its channel and is the typed number. */
  phone_verified: boolean;
  status: SignupStatus;
  decided_via: "staff" | "auto" | null;
  /** Staff only: never sent to the customer. */
  decline_reason: string | null;
  subscription_id: string | null;
  created_at: string;
  decided_at: string | null;
  source_channel: string | null;
}

export interface SignupPage {
  items: SignupOut[];
  next_cursor: string | null;
}

export interface DeclineIn {
  reason?: string | null;
}

/** Mirrors csa/contacts/contracts.py. A contact as the link screens show it: no email, no Telegram account. */
export interface LinkedContact {
  id: string;
  full_name: string | null;
  phone: string | null;
  /** Channel codes (telegram, whatsapp, ...), including those a not-yet-undone link moved off the loser. */
  channels: string[];
}

export type LinkReason = "verified_phone" | "staff";
export type ReviewReason =
  | "several_matches"
  | "telegram_conflict"
  | "same_channel_identity"
  | "table_conflict"
  | "previously_unlinked";

export interface LinkIn {
  contact_a: string;
  contact_b: string;
}

export interface LinkOut {
  id: string;
  survivor: LinkedContact;
  loser: LinkedContact;
  reason: LinkReason;
  created_at: string;
  unlinked_at: string | null;
  /** Not undone, within 30 days, and no later active link on either contact. */
  can_unlink: boolean;
}

export interface UnlinkResultOut {
  /** Contact columns the unlink left alone, as "role.column" (for example "loser.telegram_id"). */
  skipped: string[];
}

export type ReviewResolution = "linked" | "dismissed";

export interface ReviewOut {
  id: string;
  contact_a: LinkedContact;
  contact_b: LinkedContact;
  reason: ReviewReason;
  status: "open" | "resolved";
  resolution: ReviewResolution | null;
  created_at: string;
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

export interface SubscriptionSettings {
  /** The tenant's IANA time zone (clients.timezone). */
  timezone: string;
  /** Days a pending subscription waits before it expires; 0 means never. */
  pending_expiry_days: number;
  /** 0 until the company has saved settings; sent back on the save. */
  version: number;
}

export interface SettingsIn {
  pending_expiry_days: number;
  version: number;
}

export function getSettings(tenantId: string | null): Promise<SubscriptionSettings> {
  return api(subscriptionsPath(tenantId, "/settings"));
}

/** Saves the settings as a whole. Send the version that was read (0 before the first save). */
export function putSettings(tenantId: string | null, body: SettingsIn): Promise<SubscriptionSettings> {
  return api(subscriptionsPath(tenantId, "/settings"), { method: "PUT", body: JSON.stringify(body) });
}

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

export function listSignups(
  tenantId: string | null,
  status: "pending" | "decided",
  before?: string | null,
): Promise<SignupPage> {
  return api(subscriptionsPath(tenantId, "/signups", { status, before }));
}

/** Approving makes one pending subscription; the route takes no body. */
export function approveSignup(tenantId: string | null, signupId: string): Promise<SignupOut> {
  return api(subscriptionsPath(tenantId, `/signups${id(signupId)}/approve`), { method: "POST" });
}

export function declineSignup(tenantId: string | null, signupId: string, body: DeclineIn): Promise<SignupOut> {
  return post(subscriptionsPath(tenantId, `/signups${id(signupId)}/decline`), body);
}

/** Links of one customer, newest first: those where the contact is the survivor or the loser. */
export function listLinks(tenantId: string | null, contactId: string): Promise<LinkOut[]> {
  return api(subscriptionsPath(tenantId, "/links", { contact_id: contactId }));
}

/** How many links the Sign-ups tab lists; the route allows up to 200. */
export const RECENT_LINKS_LIMIT = 20;

/** Every link of the company, newest first, whoever the contacts are (the Sign-ups tab list). */
export function listRecentLinks(tenantId: string | null, limit = RECENT_LINKS_LIMIT): Promise<LinkOut[]> {
  return api(subscriptionsPath(tenantId, "/links", { limit: String(limit) }));
}

export function linkContacts(tenantId: string | null, body: LinkIn): Promise<LinkOut> {
  return post(subscriptionsPath(tenantId, "/links"), body);
}

/** Undoes a link; the route takes no body and names what it could not put back in `skipped`. */
export function unlinkContacts(tenantId: string | null, linkId: string): Promise<UnlinkResultOut> {
  return api(subscriptionsPath(tenantId, `/links${id(linkId)}/unlink`), { method: "POST" });
}

/** Pairs of contacts the system could not link by itself, newest first. */
export function listReviews(tenantId: string | null, status: "open" | "resolved" = "open"): Promise<ReviewOut[]> {
  return api(subscriptionsPath(tenantId, "/link-reviews", { status }));
}

/** `linked` links the pair as staff; `dismissed` closes the review and links nothing. */
export function resolveReview(
  tenantId: string | null,
  reviewId: string,
  resolution: ReviewResolution,
): Promise<ReviewOut> {
  return post(subscriptionsPath(tenantId, `/link-reviews${id(reviewId)}/resolve`), { resolution });
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
  pending: "Waiting to start",
  active: "Active",
  paused: "Paused",
  cancelled: "Cancelled",
};

// Mirrors csa/subscriptions/executor.py ALLOWED_FROM; the server refuses anything else.
const ALLOWED_FROM: Record<SubscriptionAction, SubscriptionStatus[]> = {
  activate: ["pending"],
  pause: ["active"],
  resume: ["paused"],
  change_plan: ["active", "paused"],
  cancel_at_period_end: ["active"],
  cancel_now: ["active", "paused", "pending"],
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
const SIGNUP_AUTO_ACTOR = "system:signup_auto";

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
    case "signup_approved":
      sentence = `Sign-up approved ${event.actor === SIGNUP_AUTO_ACTOR ? "automatically" : "by staff"} for the ${
        plan("plan")} plan. Waiting to start.`;
      break;
    case "activated":
      sentence = "Subscription started.";
      break;
    case "pending_expired":
      sentence = "Expired before it started, so it was cancelled.";
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
