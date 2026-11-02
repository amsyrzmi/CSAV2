/**
 * Pure rules for the Email assistant page: the filters over the emails already loaded, and the
 * one honest note about how far back they reach. The server returns only its cache (the last
 * 90 days, at most 200 threads, newest first) and accepts no filters, so every filter here is
 * applied in the browser to that cache and nothing more.
 */
import { toInstants, type DayRange } from "@/lib/date-range";

export interface EmailFilters {
  unread: boolean;
  hasDraft: boolean;
  category: string | null;
  priority: string | null;
  range: DayRange;
}

/** The slice of a thread these rules read (the page passes full `Thread` rows). */
export interface FilterableThread {
  thread_id: string;
  unread: boolean;
  category: string | null;
  priority: string | null;
  received_at: string | null;
}

/** The category labels in the order the page lists them. */
export const categoryLabel: Record<string, string> = {
  customer_request: "Customer request", order_or_delivery: "Order / delivery", complaint: "Complaint",
  billing: "Billing", vendor_or_partner: "Vendor / partner", internal: "Internal",
  newsletter_or_promotion: "Newsletter / promotion", spam_or_phishing: "Spam / phishing", other: "Other",
};
const PRIORITY_ORDER = ["high", "normal", "low"];

export function noFilters(): EmailFilters {
  return { unread: false, hasDraft: false, category: null, priority: null, range: { from: null, to: null } };
}

export function anyFilterOn(filters: EmailFilters): boolean {
  return filters.unread || filters.hasDraft || filters.category !== null || filters.priority !== null ||
    filters.range.from !== null || filters.range.to !== null;
}

/**
 * The threads that pass every filter that is on, in their original order. The day range is read
 * in the browser's own days against the thread's last activity (`received_at` is its latest
 * message); a thread with no time cannot be placed in a range, so it drops out once days are chosen.
 */
export function filterThreads<T extends FilterableThread>(
  threads: T[], filters: EmailFilters, draftThreadIds: Set<string>,
): T[] {
  const { from, to } = toInstants(filters.range);
  const fromMs = from === null ? null : Date.parse(from);
  const toMs = to === null ? null : Date.parse(to);
  const dated = fromMs !== null || toMs !== null;
  return threads.filter(thread => {
    if (filters.unread && !thread.unread) return false;
    if (filters.hasDraft && !draftThreadIds.has(thread.thread_id)) return false;
    if (filters.category !== null && thread.category !== filters.category) return false;
    if (filters.priority !== null && thread.priority !== filters.priority) return false;
    if (dated) {
      if (thread.received_at === null) return false;
      const at = Date.parse(thread.received_at);
      if (Number.isNaN(at)) return false;
      if (fromMs !== null && at < fromMs) return false;
      if (toMs !== null && at >= toMs) return false;
    }
    return true;
  });
}

/** The window the list covers. The count is accepted for the call site; the window does not change. */
export function cacheNote(count: number): string {
  void count;
  return "Showing the last 90 days, up to 200 emails.";
}

/** The categories (page order) or priorities (most urgent first) that appear in the loaded threads. */
export function presentValues(threads: FilterableThread[], key: "category" | "priority"): string[] {
  const seen = new Set<string>();
  for (const thread of threads) {
    const value = thread[key];
    if (value) seen.add(value);
  }
  const order = key === "category" ? Object.keys(categoryLabel) : PRIORITY_ORDER;
  const known = order.filter(value => seen.has(value));
  const unknown = [...seen].filter(value => !order.includes(value)).sort();
  return [...known, ...unknown];
}

/** The threads that have at least one draft, from the loaded drafts. */
export function draftThreadIds(drafts: { thread_id: string | null }[]): Set<string> {
  const ids = new Set<string>();
  for (const draft of drafts) if (draft.thread_id) ids.add(draft.thread_id);
  return ids;
}

/**
 * The short date in a thread row, in the viewer's own zone: the time for today, "28 Oct" for
 * this year and "28 Oct 2025" before that. Empty when the thread has no time.
 */
export function listDate(iso: string | null, now: Date = new Date()): string {
  if (iso === null || Number.isNaN(Date.parse(iso))) return "";
  const at = new Date(iso);
  const parts = (date: Date) => new Intl.DateTimeFormat("en-GB", {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(date);
  const pick = (list: Intl.DateTimeFormatPart[], type: string) => list.find(part => part.type === type)?.value ?? "";
  const then = parts(at);
  const today = parts(now);
  const sameYear = pick(then, "year") === pick(today, "year");
  if (sameYear && pick(then, "month") === pick(today, "month") && pick(then, "day") === pick(today, "day")) {
    return `${pick(then, "hour")}:${pick(then, "minute")}`;
  }
  const day = `${pick(then, "day")} ${pick(then, "month")}`;
  return sameYear ? day : `${day} ${pick(then, "year")}`;
}
