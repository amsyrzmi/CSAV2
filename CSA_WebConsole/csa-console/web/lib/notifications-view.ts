/**
 * Pure rules and English copy behind the notification bell (no React, no I/O).
 *
 * Every value that comes from the server is untrusted here: a param may be missing or of the
 * wrong type, a key may be one this version does not know. Nothing below ever lets "undefined",
 * "NaN" or "[object Object]" reach the screen, and no message key or id is ever shown.
 */

export interface NotificationItem {
  id: string;
  kind: string;
  priority: "low" | "normal" | "high" | "urgent" | string;
  message_key: string;
  params: Record<string, unknown>;
  link: Record<string, unknown> | null;
  read_at: string | null;
  created_at: string;
}

export interface NotificationPage {
  items: NotificationItem[];
  unread: number;
}

const MINUTE_MS = 60_000;
const HOUR_MS = 60 * MINUTE_MS;
const DAY_MS = 24 * HOUR_MS;

const UUID_SHAPE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function plural(count: number, unit: string): string {
  return `${count} ${unit}${count === 1 ? "" : "s"}`;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

/**
 * A length of time the way the staff chat writes it. Whole minutes only (fractions round
 * down); under one minute, negative, non-finite or non-number gives null. Under 60 minutes:
 * "45 minutes"; under 48 hours: hours then leftover minutes ("1 hour 59 minutes", "2 hours");
 * from 48 hours: whole days, rounded down ("2 days").
 */
export function durationText(minutes: unknown): string | null {
  if (typeof minutes !== "number" || !Number.isFinite(minutes)) return null;
  const whole = Math.floor(minutes);
  if (whole < 1) return null;
  if (whole < 60) return plural(whole, "minute");
  if (whole < 48 * 60) {
    const hours = Math.floor(whole / 60);
    const rest = whole % 60;
    return rest === 0
      ? plural(hours, "hour")
      : `${plural(hours, "hour")} ${plural(rest, "minute")}`;
  }
  return plural(Math.floor(whole / (24 * 60)), "day");
}

/** The unread badge text: a positive whole number, capped at "99+"; anything else is null. */
export function badgeText(count: unknown): string | null {
  if (typeof count !== "number" || !Number.isInteger(count) || count < 1) return null;
  return count > 99 ? "99+" : String(count);
}

/**
 * How long ago `iso` was, from a fixed `now`. All steps are elapsed time (24 h = one day), not
 * calendar days, so the answer does not depend on the machine's time zone:
 * under 1 minute (or in the future) "just now"; under 60 minutes "N minute(s) ago"; under
 * 24 hours "N hour(s) ago" (rounded down); 24 to under 48 hours "yesterday"; 48 hours or more
 * "N days ago" (rounded down). An unparsable date gives "".
 */
export function agoText(now: Date, iso: string): string {
  if (typeof iso !== "string" || !(now instanceof Date)) return "";
  const then = Date.parse(iso);
  const nowMs = now.getTime();
  if (!Number.isFinite(then) || !Number.isFinite(nowMs)) return "";
  const elapsed = nowMs - then;
  if (elapsed < MINUTE_MS) return "just now";
  if (elapsed < HOUR_MS) return `${plural(Math.floor(elapsed / MINUTE_MS), "minute")} ago`;
  if (elapsed < DAY_MS) return `${plural(Math.floor(elapsed / HOUR_MS), "hour")} ago`;
  if (elapsed < 2 * DAY_MS) return "yesterday";
  return `${plural(Math.floor(elapsed / DAY_MS), "day")} ago`;
}

/** The customer's display name, or null when it is not a usable string. */
function customerName(params: Record<string, unknown>): string | null {
  const value = params.customer;
  if (typeof value !== "string") return null;
  const name = value.trim();
  return name === "" ? null : name;
}

const GENERIC = {
  title: "New notification",
  detail: "Open the console to see the details.",
};

/**
 * The words for one notification. A customer who is missing or blank reads "A customer" (the
 * name always opens the sentence, so the fallback is capitalised there). An unknown key gives
 * the generic pair. Priority and Reminder sentences belong to inbox.overdue only.
 */
export function renderNotification(item: {
  message_key?: unknown;
  params?: unknown;
}): { title: string; detail: string } {
  const params = asRecord(item?.params);
  const who = customerName(params) ?? "A customer";
  const waited = durationText(params.waited_minutes);
  switch (item?.message_key) {
    case "inbox.overdue": {
      let detail = waited ? `${who} has waited ${waited}.` : `${who} is waiting for your team.`;
      const priority = typeof params.priority === "string" ? params.priority.trim().toLowerCase() : "";
      if (priority === "high" || priority === "urgent") detail += ` Priority: ${priority}.`;
      const repeat = params.repeat;
      if (typeof repeat === "number" && Number.isInteger(repeat) && repeat > 1) {
        detail += ` Reminder ${repeat}.`;
      }
      return { title: "Waiting for your team", detail };
    }
    case "inbox.snooze_due":
      return { title: "Snooze ended", detail: `${who} is back in Needs us.` };
    case "inbox.stale_claim":
      return {
        title: "Claimed, no reply yet",
        detail: waited
          ? `${who} was claimed but has had no reply. Waiting ${waited}.`
          : `${who} was claimed but has had no reply.`,
      };
    case "inbox.follow_up":
      return {
        title: "Follow up or resolve",
        detail: waited
          ? `${who} has not answered for ${waited}. Follow up or resolve it.`
          : `${who} has not answered. Follow up or resolve it.`,
      };
    case "inbox.handback":
      return {
        title: "Hand it back?",
        detail: waited
          ? `${who} has not answered for ${waited}. You can hand the chat back to the assistant.`
          : `${who} has not answered. You can hand the chat back to the assistant.`,
      };
    case "inbox.assigned":
      return { title: "Assigned to you", detail: `${who} was assigned to you.` };
    default:
      return { ...GENERIC };
  }
}

/**
 * Where a notification opens: only the Inbox, and only for a UUID-shaped conversation id.
 * Anything else (another route, a missing or odd id, junk types) is null, and the row stays
 * plain text.
 */
export function notificationLink(item: {
  link?: unknown;
}): { route: "inbox"; conversation: string } | null {
  const link = item?.link;
  if (typeof link !== "object" || link === null || Array.isArray(link)) return null;
  const { route, conversation } = link as Record<string, unknown>;
  if (route !== "inbox") return null;
  if (typeof conversation !== "string" || !UUID_SHAPE.test(conversation)) return null;
  return { route: "inbox", conversation };
}

/** Unread unless `read_at` holds a time. */
export function isUnread(item: { read_at?: string | null }): boolean {
  return typeof item?.read_at !== "string" || item.read_at === "";
}

/**
 * The list after marking `ids` (or everything) read at `at`: only unread rows change, an
 * already-read row keeps its first read time. Returns a new array; untouched rows keep their
 * identity. Used for the optimistic update.
 */
export function applyRead(
  items: NotificationItem[],
  ids: string[] | "all",
  at: string,
): NotificationItem[] {
  const wanted = ids === "all" ? null : new Set(ids);
  return items.map((item) =>
    isUnread(item) && (wanted === null || wanted.has(item.id)) ? { ...item, read_at: at } : item,
  );
}

/**
 * The unread number after marking. "all" is zero, whatever the loaded page holds. For ids it
 * subtracts the loaded unread rows that match (each id once); never below zero, and an unusable
 * starting number counts as zero.
 */
export function unreadAfter(
  unread: number,
  items: NotificationItem[],
  ids: string[] | "all",
): number {
  if (ids === "all") return 0;
  const start = Number.isFinite(unread) ? Math.max(0, Math.floor(unread)) : 0;
  const wanted = new Set(ids);
  const cleared = items.filter((item) => isUnread(item) && wanted.has(item.id)).length;
  return Math.max(0, start - cleared);
}
