/**
 * Pure rules for the Subscriptions page: the summary row figures, the customer chips, times in
 * the company's time zone and the plain sentence that opens the review step. Nothing here
 * invents a figure: a count is a number from rows that were loaded, with a "+" where a further
 * page exists, and a read that failed gives no figures at all.
 */
import {
  formatDateTime,
  formatDay,
  money,
  type ActionIn,
  type RequestOut,
  type SubscriptionOut,
} from "@/lib/subscriptions-api";

type EndingFields = Pick<SubscriptionOut, "status" | "cancel_at_period_end">;

/** An active customer who is set to cancel at the end of the period. Paused and cancelled never are. */
export function isEndingSoon(row: EndingFields): boolean {
  return row.status === "active" && row.cancel_at_period_end;
}

export interface SummaryCounts {
  active: string;
  endingSoon: string;
  requestsWaiting: string;
}

/**
 * The three figures over the pages that were loaded. A figure reads "50+" where a further page
 * exists. `null` (a read failed or has not arrived) hides the whole row: a failure is never
 * drawn as zero.
 */
export function summaryCounts(
  customers: EndingFields[] | null | undefined,
  requests: Pick<RequestOut, "id">[] | null | undefined,
  hasMoreCustomers: boolean,
  hasMoreRequests: boolean,
  // Kept for the agreed signature: "ending soon" no longer depends on the date.
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _now: Date,
): SummaryCounts | null {
  if (!customers || !requests) return null;
  const text = (count: number, more: boolean) => `${count}${more ? "+" : ""}`;
  return {
    active: text(customers.filter((row) => row.status === "active").length, hasMoreCustomers),
    endingSoon: text(customers.filter((row) => isEndingSoon(row)).length, hasMoreCustomers),
    requestsWaiting: text(requests.length, hasMoreRequests),
  };
}

/** The zone if the browser knows it; null for an empty, unknown or malformed name. */
export function validTimeZone(zone: string | null | undefined): string | null {
  if (!zone || !zone.trim()) return null;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: zone });
    return zone;
  } catch {
    return null;
  }
}

/** "27 Oct 2026, 09:15" in the company's zone; the browser's zone while that is unknown or invalid. */
export function formatInTenantZone(iso: string, timeZone: string | null): string {
  return formatDateTime(iso, validTimeZone(timeZone) ?? undefined);
}

// ---- Customer chips (client side, over the rows that were loaded) ----

export type CustomerView = "all" | "active" | "paused" | "cancelled" | "ending";

const VIEWS: { value: CustomerView; label: string }[] = [
  { value: "all", label: "All" },
  { value: "active", label: "Active" },
  { value: "paused", label: "Paused" },
  { value: "cancelled", label: "Cancelled" },
  { value: "ending", label: "Ending soon" },
];

/** Whether one loaded row belongs to a chip. */
export function inView(row: EndingFields, view: CustomerView): boolean {
  switch (view) {
    case "all":
      return true;
    case "ending":
      return isEndingSoon(row);
    default:
      return row.status === view;
  }
}

export function filterCustomers<T extends EndingFields>(rows: T[], view: CustomerView): T[] {
  return view === "all" ? rows : rows.filter((row) => inView(row, view));
}

/**
 * The chips with a figure each. On a paged list the figures would describe only the loaded
 * page, so none are drawn (`count: null`).
 */
export function customerChipOptions(
  rows: EndingFields[],
  paged: boolean,
): { value: CustomerView; label: string; count: number | null }[] {
  return VIEWS.map(({ value, label }) => ({
    value,
    label,
    count: paged ? null : rows.filter((row) => inView(row, value)).length,
  }));
}

// ---- The plain sentence that opens the review step ----

export interface ReviewContext {
  customer: string;
  currentPlan: string;
  /** The subscription's next renewal as the server sent it (shown in the company's zone). */
  renewsAt: string | null;
  timeZone: string | null;
  planName: (code: string) => string;
  /** "RM 99.00 a month" for a plan code, or null when the plan is not known. */
  planPrice: (code: string) => string | null;
}

/**
 * What the change will do, in one plain sentence. Days the person picked in the form were
 * turned into instants on this browser's calendar, so they are written back in the browser's
 * zone; the stored renewal date is written in the company's zone.
 */
export function reviewSentence(body: ActionIn, ctx: ReviewContext): string {
  const who = ctx.customer;
  const picked = (value: string | null | undefined) => formatDay(value);
  switch (body.action) {
    case "pause":
      return `This will pause ${who}'s subscription until ${
        body.resume_at ? picked(body.resume_at) : "someone resumes it"}.`;
    case "resume":
      return `This will resume ${who}'s subscription now.`;
    case "change_plan": {
      const code = body.plan_code ?? "";
      const later = Boolean(body.effective_at);
      const when = later ? `starting on ${picked(body.effective_at)}` : "starting now";
      // A change planned for a later day takes the plan's price on that day (the server says so).
      const price = later
        ? "at the plan's price on that day"
        : body.price_override != null && body.price_override !== ""
          ? `at a special price of ${money(body.price_override)}`
          : ctx.planPrice(code) ? `at ${ctx.planPrice(code)}` : "";
      return `This will move ${who} from ${ctx.currentPlan} to ${ctx.planName(code)}, ${when}${
        price ? `, ${price}` : ""}.`;
    }
    case "cancel_at_period_end": {
      const end = ctx.renewsAt ? ` on ${formatDay(ctx.renewsAt, ctx.timeZone ?? undefined)}` : "";
      return `This will cancel ${who}'s subscription when the current period ends${end}. They keep it until then.`;
    }
    case "cancel_now":
      return `This will cancel ${who}'s subscription straight away. A cancelled subscription cannot be started again.`;
    case "extend_renewal":
      return `This will move the next renewal for ${who} from ${
        formatDay(ctx.renewsAt, ctx.timeZone ?? undefined)} to ${picked(body.renews_at)}.`;
  }
}
