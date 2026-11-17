/**
 * The Inbox work queue as the screen sees it: tabs and their server filters, the list query, the
 * wording for waits, due times and rows, the primary action for an item and the plain sentences for
 * action errors and bulk results. Pure: types only are imported, so node tests load this file directly.
 * Times are shown in the company timezone, a fixed +08:00 (Asia/Kuala_Lumpur).
 */
import type { StatusTone } from "@/components/console/fs/StatusLabel";
import type { ConsoleRole } from "@/lib/identity-contracts";
import type {
  BulkOutcome,
  ChannelFilter,
  Conversation,
  Priority,
  QueueActionName,
  QueueCounts,
  QueueState,
  Reason,
  SavedView,
  SenderFilter,
  Topic,
} from "@/lib/inbox-api";

export type QueueTabId =
  | "needs_us"
  | "mine"
  | "unassigned"
  | "overdue"
  | "waiting"
  | "snoozed"
  | "resolved"
  | "all";
export type QueueOrder = "activity" | "waiting" | "due" | "priority";

export interface QueueFilters {
  state: QueueState[];
  /** "" (anyone), "me", "none" or a user id. */
  assignee: "" | "me" | "none" | string;
  priorityMin: "" | Priority;
  reason: Reason[];
  topic: Topic[];
  channel: ChannelFilter;
  sender: SenderFilter;
  overdue: boolean;
  /** Minutes a `needs_staff` item has waited at least. */
  waitingOver: number | null;
}

export interface QueueTab {
  id: QueueTabId;
  label: string;
  testId: string;
  filters: Partial<QueueFilters>;
  order: QueueOrder;
  count: keyof QueueCounts | null;
}

const INBOX_CONVERSATIONS = "/console/v1/inbox/conversations";

/** Each tab is a filter set plus the order it opens with; the screen always sends the order. */
export const TABS: readonly QueueTab[] = [
  {
    id: "needs_us", label: "Needs us", testId: "inbox-tab-needs_us",
    filters: { state: ["needs_staff"] }, order: "due", count: "needs_us",
  },
  {
    id: "mine", label: "Mine", testId: "inbox-tab-mine",
    filters: { assignee: "me", state: ["needs_staff", "waiting_customer"] }, order: "due", count: "mine",
  },
  {
    id: "unassigned", label: "Unassigned", testId: "inbox-tab-unassigned",
    filters: { state: ["needs_staff"], assignee: "none" }, order: "due", count: "unassigned",
  },
  {
    id: "overdue", label: "Overdue", testId: "inbox-tab-overdue",
    filters: { state: ["needs_staff"], overdue: true }, order: "due", count: "overdue",
  },
  {
    id: "waiting", label: "Waiting on customer", testId: "inbox-tab-waiting",
    filters: { state: ["waiting_customer"] }, order: "activity", count: "waiting_customer",
  },
  {
    id: "snoozed", label: "Snoozed", testId: "inbox-tab-snoozed",
    filters: { state: ["snoozed"] }, order: "activity", count: "snoozed",
  },
  {
    id: "resolved", label: "Resolved", testId: "inbox-tab-resolved",
    filters: { state: ["resolved"] }, order: "activity", count: null,
  },
  {
    id: "all", label: "All", testId: "inbox-tab-all",
    filters: {}, order: "activity", count: null,
  },
];

function emptyFilters(): QueueFilters {
  return {
    state: [], assignee: "", priorityMin: "", reason: [], topic: [], channel: "", sender: "",
    overdue: false, waitingOver: null,
  };
}

/** The tab's own filters laid over empty ones. Arrays are fresh, so callers can change them. */
export function tabFilters(tab: QueueTabId): QueueFilters {
  const own = TABS.find((t) => t.id === tab)?.filters ?? {};
  const base = emptyFilters();
  return {
    ...base,
    ...own,
    state: [...(own.state ?? base.state)],
    reason: [...(own.reason ?? base.reason)],
    topic: [...(own.topic ?? base.topic)],
  };
}

/**
 * Full path and query for the conversation list. Only set values are sent, the tenant query comes
 * first and `order` always goes, because the server's default differs by filter (Mine would fall
 * back to activity). The server answers 422 for a one-character search, so it is never sent.
 */
export function queueListQuery(o: {
  tenantQuery: string;
  filters: QueueFilters;
  order: QueueOrder;
  q: string;
}): string {
  const f = o.filters;
  const enc = encodeURIComponent;
  const parts: string[] = [];
  if (o.tenantQuery) parts.push(o.tenantQuery);
  for (const state of f.state) parts.push(`state=${enc(state)}`);
  if (f.assignee) parts.push(`assignee=${enc(f.assignee)}`);
  if (f.priorityMin) parts.push(`priority_min=${enc(f.priorityMin)}`);
  for (const reason of f.reason) parts.push(`reason=${enc(reason)}`);
  for (const topic of f.topic) parts.push(`topic=${enc(topic)}`);
  if (f.channel) parts.push(`channel=${enc(f.channel)}`);
  if (f.sender) parts.push(`sender=${enc(f.sender)}`);
  if (f.overdue) parts.push("overdue=true");
  if (f.waitingOver !== null) parts.push(`waiting_over=${enc(String(f.waitingOver))}`);
  parts.push(`order=${enc(o.order)}`);
  const q = o.q.trim();
  if ([...q].length >= 2) parts.push(`q=${enc(q)}`);
  return `${INBOX_CONVERSATIONS}?${parts.join("&")}`;
}

// ---- words ----

export const REASON_LABELS: Record<Reason, string> = {
  escalation: "Escalated",
  held_reply: "Held reply",
  customer_message: "Customer wrote",
  manual: "Taken over",
};

export const TOPIC_LABELS: Record<Topic, string> = {
  purchase: "Purchase",
  question: "Question",
  complaint: "Complaint",
  payment: "Payment",
  booking: "Booking",
  subscription: "Subscription",
  other: "Other",
};

export const PRIORITY_LABELS: Record<Priority, string> = {
  low: "Low",
  normal: "Normal",
  high: "High",
  urgent: "Urgent",
};

/** "35m", "2h 10m", "2h", "3d" from a length in milliseconds (at least one minute). */
function duration(ms: number): string {
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    const rest = minutes % 60;
    return rest ? `${hours}h ${rest}m` : `${hours}h`;
  }
  return `${Math.floor(hours / 24)}d`;
}

function parseTime(at: string | null): number | null {
  if (!at) return null;
  const ms = Date.parse(at);
  return Number.isNaN(ms) ? null : ms;
}

/** How long an item has waited on us: "just now", "35m", "2h 10m", "3d". Empty without a start time. */
export function waitText(since: string | null, now: number): string {
  const start = parseTime(since);
  if (start === null) return "";
  const elapsed = now - start;
  // A start in the future (clock skew) and anything under a minute both read as just now.
  if (elapsed < 60_000) return "just now";
  return duration(elapsed);
}

type Clock = Pick<Conversation, "needs_staff_since" | "due_at">;

/** `soon` is the last quarter of the window from the clock start to the due time. */
export function dueState(item: Clock, now: number): "none" | "ok" | "soon" | "overdue" {
  const due = parseTime(item.due_at);
  const start = parseTime(item.needs_staff_since);
  if (due === null || start === null) return "none";
  if (now >= due) return "overdue";
  return (due - now) * 4 <= due - start ? "soon" : "ok";
}

/** "Due in 20m", "Overdue 1h". Empty when there is no due time. */
export function dueText(item: Pick<Conversation, "due_at">, now: number): string {
  const due = parseTime(item.due_at);
  if (due === null) return "";
  const diff = due - now;
  if (diff > 0) return diff < 60_000 ? "Due in under 1m" : `Due in ${duration(diff)}`;
  return -diff < 60_000 ? "Overdue just now" : `Overdue ${duration(-diff)}`;
}

/** "AD" for "Aina Demo", the first letter for one word, "?" for no name. At most two letters. */
export function initialsOf(name: string | null): string {
  const words = (name ?? "").split(/\s+/).filter(Boolean);
  if (!words.length) return "?";
  return words.slice(0, 2).map((w) => [...w][0]).join("").toUpperCase();
}

function firstName(name: string | null): string | null {
  const word = (name ?? "").split(/\s+/).find(Boolean);
  return word ?? null;
}

// ---- company time (+08:00) ----

const OFFSET_MS = 8 * 3_600_000;
const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const pad = (n: number) => String(n).padStart(2, "0");

/** The +08:00 calendar fields of an instant, read from a shifted UTC date so no Intl is involved. */
function companyFields(ms: number) {
  const d = new Date(ms + OFFSET_MS);
  return {
    year: d.getUTCFullYear(), month: d.getUTCMonth(), day: d.getUTCDate(),
    hour: d.getUTCHours(), minute: d.getUTCMinutes(), weekday: d.getUTCDay(),
  };
}

function isoAtCompanyTime(year: number, month: number, day: number, hour: number, minute: number): string {
  // Date.UTC normalises an overflowing day or month, so adding days across a month end is safe.
  const d = new Date(Date.UTC(year, month, day, hour, minute));
  return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`
    + `T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:00+08:00`;
}

/** "Thu 09:00" in company time. */
function weekdayTime(ms: number): string {
  const f = companyFields(ms);
  return `${WEEKDAYS[f.weekday]} ${pad(f.hour)}:${pad(f.minute)}`;
}

const DAY_MS = 24 * 3_600_000;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * When a snooze ends, in company time: "Thu 09:00", or "Wed 25 Nov 09:00" when it is more than six
 * days after `now` (a weekday alone would then be ambiguous). "" for a missing or unreadable time.
 * The row's label and the snooze notice both use it.
 */
export function snoozeUntilText(until: string | null | undefined, now: number): string {
  const ms = parseTime(until ?? null);
  if (ms === null) return "";
  if (ms - now <= 6 * DAY_MS) return weekdayTime(ms);
  const f = companyFields(ms);
  return `${WEEKDAYS[f.weekday]} ${f.day} ${MONTHS[f.month]} ${pad(f.hour)}:${pad(f.minute)}`;
}

/** Later today (+3 h), Tomorrow 09:00 and Next Monday 09:00 (strictly after today), all in +08:00. */
export function snoozePresets(now: Date): { label: string; until: string }[] {
  const t = now.getTime();
  const later = companyFields(t + 3 * 3_600_000);
  const today = companyFields(t);
  const toMonday = (1 - today.weekday + 7) % 7 || 7;
  return [
    {
      label: "Later today",
      until: isoAtCompanyTime(later.year, later.month, later.day, later.hour, later.minute),
    },
    {
      label: "Tomorrow 09:00",
      until: isoAtCompanyTime(today.year, today.month, today.day + 1, 9, 0),
    },
    {
      label: "Next Monday 09:00",
      until: isoAtCompanyTime(today.year, today.month, today.day + toMonday, 9, 0),
    },
  ];
}

// ---- rows ----

export interface RowMeta {
  state: { tone: StatusTone; text: string } | null;
  due: { state: "none" | "ok" | "soon" | "overdue"; text: string } | null;
  priority: { text: "Urgent" | "High" } | null;
  reason: string | null;
  assignee: { initials: string; text: string } | null;
}

/** What a row's meta line shows. Each part is null when it does not apply. */
export function rowMeta(item: Conversation, now: number, me: { userId: string | null }): RowMeta {
  let state: RowMeta["state"] = null;
  switch (item.queue_status) {
    case "needs_staff":
      state = { tone: "attention", text: "Needs us" };
      break;
    case "waiting_customer":
      state = { tone: "neutral", text: "Waiting on customer" };
      break;
    case "snoozed": {
      const until = snoozeUntilText(item.snoozed_until, now);
      state = { tone: "info", text: until ? `Snoozed until ${until}` : "Snoozed" };
      break;
    }
    case "resolved":
      state = { tone: "success", text: "Resolved" };
      break;
    default:
      break; // bot rows have no state label
  }

  const needsUs = item.queue_status === "needs_staff";
  const dueLabel = needsUs ? dueText(item, now) : "";
  const due = needsUs && dueLabel ? { state: dueState(item, now), text: dueLabel } : null;
  const priority = item.priority === "urgent" || item.priority === "high"
    ? { text: item.priority === "urgent" ? ("Urgent" as const) : ("High" as const) }
    : null;
  const reason = needsUs && item.queue_reason ? REASON_LABELS[item.queue_reason] : null;

  let assignee: RowMeta["assignee"] = null;
  if (item.assignee_id) {
    const name = item.assignee?.display_name ?? null;
    assignee = {
      initials: initialsOf(name),
      text: me.userId !== null && item.assignee_id === me.userId ? "You" : (firstName(name) ?? "Someone"),
    };
  }
  return { state, due, priority, reason, assignee };
}

/** The tabs whose rows show how long each item has waited on us instead of the last message time. */
const WAIT_TABS: readonly QueueTabId[] = ["needs_us", "mine", "unassigned", "overdue"];

/** "17 Nov 2026, 09:50" in company time, or "" for an unreadable time. */
export function companyDateTime(at: string | null | undefined): string {
  const ms = parseTime(at ?? null);
  if (ms === null) return "";
  const f = companyFields(ms);
  return `${f.day} ${MONTHS[f.month]} ${f.year}, ${pad(f.hour)}:${pad(f.minute)}`;
}

/**
 * A row's clock. In the queue tabs it is the wait since the server's `needs_staff_since` (never
 * the time the page opened); elsewhere, and for a row without a wait clock (a waiting-on-customer
 * item in Mine, or a row from an older server), it is the last message time, which the caller
 * words with `listTime`. The title is the absolute time in company time.
 */
export function rowClock(
  item: Pick<Conversation, "needs_staff_since" | "last_message" | "opened_at">,
  tab: QueueTabId,
  now: number,
): { kind: "wait"; text: string; title: string } | { kind: "last"; at: string; title: string } {
  const since = item.needs_staff_since ?? null;
  if (WAIT_TABS.includes(tab) && parseTime(since) !== null) {
    return { kind: "wait", text: waitText(since, now), title: `Waiting since ${companyDateTime(since)}` };
  }
  const at = item.last_message?.at ?? item.opened_at;
  const absolute = companyDateTime(at);
  return { kind: "last", at, title: absolute ? `Last message ${absolute}` : "" };
}

/** The newest message is the customer's and nobody has answered it. False when either time is unknown or unreadable. */
export function isUnread(item: Pick<Conversation, "last_customer_at" | "last_staff_reply_at">): boolean {
  const customer = parseTime(item.last_customer_at ?? null);
  if (customer === null) return false;
  const staff = parseTime(item.last_staff_reply_at ?? null);
  return staff === null || customer > staff;
}

/** The due part of a row in words and a tone: minutes (neutral), "Due soon" (attention) or "Overdue 1h" (error). */
export function dueLabel(due: RowMeta["due"]): { tone: StatusTone; text: string } | null {
  if (!due) return null;
  if (due.state === "overdue") return { tone: "error", text: due.text };
  if (due.state === "soon") return { tone: "attention", text: "Due soon" };
  return { tone: "neutral", text: due.text };
}

export type PrimaryAction = {
  kind: "take_over" | "take_it" | "take_from" | "resolve" | "none";
  name?: string;
};

/**
 * The one primary button for an item (design 14.4). Taking someone else's claim is for
 * administrators; tenant staff may still resolve such an item, but are never offered the take.
 */
export function primaryAction(
  item: Conversation,
  me: { userId: string | null; role: ConsoleRole | null },
): PrimaryAction {
  switch (item.queue_status) {
    case "bot":
      return { kind: "take_over" };
    case "needs_staff": {
      if (!item.assignee_id) return { kind: "take_it" };
      if (me.userId !== null && item.assignee_id === me.userId) return { kind: "resolve" };
      if (me.role === "tenant_admin" || me.role === "platform_admin") {
        return { kind: "take_from", name: firstName(item.assignee?.display_name ?? null) ?? "someone" };
      }
      return { kind: "resolve" };
    }
    case "waiting_customer":
    case "snoozed":
      return { kind: "resolve" };
    default:
      return { kind: "none" };
  }
}

/** What the thread header offers for a queued item; null for the assistant's chats and rows from an older server. */
export type ThreadActions = {
  primary: PrimaryAction;
  snooze: boolean;
  /** Assign to…: administrators only. */
  assign: boolean;
  /** Give up this item: the holder, or an administrator. */
  unclaim: boolean;
  priority: boolean;
  topic: boolean;
  /** Take over the chat, so a person can reply (the assistant still answers it). */
  takeOver: boolean;
  /** Hand back to the assistant (the release route, after a confirmation). */
  handBack: boolean;
};

const OPEN_STATES: readonly QueueState[] = ["needs_staff", "waiting_customer", "snoozed"];

/**
 * The thread header's actions (design 14.4). The assistant's chats (`bot`) and rows without queue
 * fields keep the old Take over / Hand back block, so this is null for them.
 */
export function threadActions(
  item: Conversation,
  me: { userId: string | null; role: ConsoleRole | null },
): ThreadActions | null {
  const state = item.queue_status;
  if (typeof state !== "string" || state === "bot") return null;
  const admin = me.role === "tenant_admin" || me.role === "platform_admin";
  const open = OPEN_STATES.includes(state);
  const held = !!item.assignee_id;
  return {
    primary: primaryAction(item, me),
    snooze: open,
    assign: admin && open,
    unclaim: held && (admin || (me.userId !== null && item.assignee_id === me.userId)),
    priority: true,
    topic: true,
    takeOver: !item.human_muted,
    handBack: open || item.human_muted,
  };
}

/** "Waiting on us for 3h 20m, due 1h ago" for a needs-us item, from the server's times; "" otherwise. */
export function waitBannerText(
  item: Pick<Conversation, "queue_status" | "needs_staff_since" | "due_at">,
  now: number,
): string {
  if (item.queue_status !== "needs_staff") return "";
  const wait = waitText(item.needs_staff_since ?? null, now);
  if (!wait) return "";
  const head = `Waiting on us for ${wait === "just now" ? "under 1m" : wait}`;
  const due = parseTime(item.due_at ?? null);
  if (due === null) return head;
  const diff = due - now;
  const tail = diff > 0
    ? diff < 60_000 ? "due in under 1m" : `due in ${duration(diff)}`
    : -diff < 60_000 ? "due just now" : `due ${duration(-diff)} ago`;
  return `${head}, ${tail}`;
}

/** "Assigned to Aina Demo" when someone else holds the item, named or not; "" when nobody or I do. */
export function assignedText(
  item: Partial<Pick<Conversation, "assignee_id" | "assignee">>,
  me: { userId: string | null },
): string {
  if (!item.assignee_id || item.assignee_id === me.userId) return "";
  return `Assigned to ${item.assignee?.display_name || "someone outside the team list"}`;
}

const LOCAL_TIME = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?$/;

/**
 * A `datetime-local` value read as company time (+08:00), to the minute, as the snooze `until`; or
 * what is wrong with it in words (the server's own window: in the future, at most 60 days ahead).
 */
export function customSnoozeUntil(value: string, now: number): { until: string } | { error: string } {
  const m = LOCAL_TIME.exec(value.trim());
  const until = m ? `${m[1]}-${m[2]}-${m[3]}T${m[4]}:${m[5]}:00+08:00` : "";
  const ms = until ? Date.parse(until) : Number.NaN;
  if (Number.isNaN(ms)) return { error: "Pick a date and time." };
  if (ms <= now) return { error: "Pick a time in the future." };
  if (ms > now + 60 * DAY_MS) return { error: "Pick a time within the next 60 days." };
  return { until };
}

// ---- errors and bulk results ----

/** A plain sentence for a failed queue action. Never shows a raw code; `fallback` covers the rest. */
export function actionErrorText(status: number | null, code: string | null, fallback: string): string {
  if (status === null) return "Could not reach the server. Nothing was changed that we know of: try again.";
  if (status === 409) {
    if (code === null) return "This conversation changed. Refreshed, so look again before acting.";
    if (code === "held_reply_pending") return "A held reply needs a decision first.";
    if (code === "customer_wrote") return "The customer wrote after you opened this. Reload to see it.";
  }
  if (status === 403) return "You do not have permission to do that.";
  if (status === 404) return "This conversation is not available any more.";
  if (status === 422) {
    if (code === "until_in_past") return "Pick a time in the future.";
    if (code === "until_too_far") return "Pick a time within the next 60 days.";
  }
  if (status === 503) return "The team list is unavailable right now. Try again in a moment.";
  return fallback;
}

/** A queue request whose outcome is unknown; it is kept, so Retry sends the same key. */
export const UNCERTAIN_TEXT = "Could not confirm that. Retry sends the same request again.";

/** The server's plain detail when `assign` cannot read the member directory (nothing was written). */
export const DIRECTORY_DOWN = "member directory unavailable";

/**
 * Whether a failed queue request is settled: nothing was written, so it is dropped and no Retry is
 * offered (a kept request would hold every Inbox control until it is retried). A 4xx is settled; so
 * is `assign`'s 503 when the server says the member directory is down, because only a new assign
 * reads the directory and nothing is written then. Any other 5xx, a 503 without that detail (a proxy,
 * say) and no answer at all may have been applied, so they are kept.
 */
export function queueFailureIsFinal(action: QueueActionName, status: number | null, detail: string | null): boolean {
  if (status === null) return false;
  if (status >= 400 && status < 500) return true;
  return action === "assign" && status === 503 && detail === DIRECTORY_DOWN;
}

/**
 * The sentence for a failed queue action (one item or bulk). A failure that is not final may have
 * been applied, so it is the uncertain sentence (the request is kept for Retry); `assign`'s
 * directory 503 is the team-list sentence. Everything else is `actionErrorText`. `detail` is the
 * server's plain-text detail, `code` the code of a coded one.
 */
export function queueFailureText(
  action: QueueActionName, status: number | null, code: string | null, detail: string | null = null,
): string {
  if (!queueFailureIsFinal(action, status, detail)) return UNCERTAIN_TEXT;
  return actionErrorText(status, code, "That could not be done. Refreshed: look again before acting.");
}

const NOT_DONE: [Exclude<BulkOutcome, "done" | "already_handled">, string][] = [
  ["held_reply_pending", "a held reply needs a decision first"],
  ["customer_wrote", "the customer wrote after you opened the list"],
  ["not_allowed", "it is assigned to someone else, and only an administrator can change that"],
  ["not_in_queue", "it is not in the queue any more"],
  ["not_found", "it is not available any more"],
];

/** One plain summary for a bulk action, and the ids that were not done (they stay selected). */
export function bulkSummary(results: { id: string; outcome: BulkOutcome }[]): {
  text: string;
  failedIds: string[];
} {
  const isDone = (o: BulkOutcome) => o === "done" || o === "already_handled";
  const done = results.filter((r) => isDone(r.outcome)).length;
  const failedIds = results.filter((r) => !isDone(r.outcome)).map((r) => r.id);
  const sentences = [done > 0 ? `${done} done.` : "Nothing was done."];
  for (const [outcome, reason] of NOT_DONE) {
    const n = results.filter((r) => r.outcome === outcome).length;
    if (n > 0) sentences.push(`${n} not done: ${reason}.`);
  }
  return { text: sentences.join(" "), failedIds };
}

/** The selection's ceiling: the bulk route takes 1 to 50 ids. */
export const BULK_LIMIT = 50;
export const BULK_LIMIT_TEXT = `You can select up to ${BULK_LIMIT} conversations at a time.`;

/**
 * What did not apply, one line per conversation, grouped by outcome in the summary's order:
 * "Aina Demo: the customer wrote after you opened the list." `nameOf` gives the name the person
 * saw; an id it does not know is "A conversation no longer in this list".
 */
export function bulkFailureLines(
  results: { id: string; outcome: BulkOutcome }[], nameOf: (id: string) => string | undefined,
): string[] {
  const lines: string[] = [];
  for (const [outcome, reason] of NOT_DONE) {
    for (const result of results) {
      if (result.outcome === outcome) {
        lines.push(`${nameOf(result.id) ?? "A conversation no longer in this list"}: ${reason}.`);
      }
    }
  }
  return lines;
}

/** What "Select all loaded" did, in words: it never reaches past the loaded rows or the ceiling. */
export function selectAllText(loaded: number): string {
  return loaded > BULK_LIMIT
    ? `Selected the first ${BULK_LIMIT} of the ${loaded} conversations loaded so far. ${BULK_LIMIT_TEXT}`
    : `Selected all ${loaded} conversations loaded so far.`;
}

// ---- filter chips ----

function sameList(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((v, i) => v === b[i]);
}

/** The filters beyond the tab's own ones, in words. The key carries the value, so a chip can be removed. */
export function activeFilterChips(filters: QueueFilters, tab: QueueTabId): { key: string; label: string }[] {
  const own = tabFilters(tab);
  const chips: { key: string; label: string }[] = [];
  if (filters.assignee && filters.assignee !== own.assignee) {
    const label = filters.assignee === "me" ? "Assigned to me"
      : filters.assignee === "none" ? "Unassigned" : "Assigned to someone";
    chips.push({ key: `assignee:${filters.assignee}`, label });
  }
  if (filters.priorityMin && filters.priorityMin !== own.priorityMin) {
    chips.push({
      key: "priority",
      label: filters.priorityMin === "urgent"
        ? "Priority urgent"
        : `Priority ${filters.priorityMin} or above`,
    });
  }
  if (!sameList(filters.reason, own.reason)) {
    for (const reason of filters.reason) {
      chips.push({ key: `reason:${reason}`, label: `Reason: ${REASON_LABELS[reason]}` });
    }
  }
  if (!sameList(filters.topic, own.topic)) {
    for (const topic of filters.topic) {
      chips.push({ key: `topic:${topic}`, label: `Topic: ${TOPIC_LABELS[topic]}` });
    }
  }
  if (filters.channel && filters.channel !== own.channel) {
    chips.push({ key: "channel", label: `Channel: ${filters.channel === "whatsapp" ? "WhatsApp" : "Telegram"}` });
  }
  if (filters.sender && filters.sender !== own.sender) {
    chips.push({ key: "sender", label: filters.sender === "known" ? "Customers" : "New senders" });
  }
  if (filters.overdue && !own.overdue) chips.push({ key: "overdue", label: "Overdue only" });
  if (filters.waitingOver !== null && filters.waitingOver !== own.waitingOver) {
    chips.push({ key: "waiting_over", label: `Waiting longer than ${duration(filters.waitingOver * 60_000)}` });
  }
  return chips;
}

/** The filters with one chip (an `activeFilterChips` key) taken off: that value goes back to the tab's own. */
export function withoutChip(filters: QueueFilters, tab: QueueTabId, key: string): QueueFilters {
  const own = tabFilters(tab);
  const [name, value] = key.includes(":") ? [key.slice(0, key.indexOf(":")), key.slice(key.indexOf(":") + 1)] : [key, ""];
  switch (name) {
    case "assignee": return { ...filters, assignee: own.assignee };
    case "priority": return { ...filters, priorityMin: own.priorityMin };
    case "reason": return { ...filters, reason: filters.reason.filter((reason) => reason !== value) };
    case "topic": return { ...filters, topic: filters.topic.filter((topic) => topic !== value) };
    case "channel": return { ...filters, channel: own.channel };
    case "sender": return { ...filters, sender: own.sender };
    case "overdue": return { ...filters, overdue: own.overdue };
    case "waiting_over": return { ...filters, waitingOver: own.waitingOver };
    default: return filters;
  }
}

/** `order=priority` is allowed only when the state filter is exactly `needs_staff` (the API's rule). */
export function priorityOrderAllowed(filters: Pick<QueueFilters, "state">): boolean {
  return filters.state.length === 1 && filters.state[0] === "needs_staff";
}

/** The order a tab opens with. */
export function tabOrder(tab: QueueTabId): QueueOrder {
  return TABS.find((t) => t.id === tab)?.order ?? "activity";
}

// ---- saved views ----

/** A view's filters under the API's keys. Only what is set is present; the search is never part of it. */
export type ViewFilters = {
  state?: QueueState[];
  assignee?: string;
  priority_min?: Priority;
  reason?: Reason[];
  topic?: Topic[];
  overdue?: boolean;
  waiting_over?: number;
  channel?: "telegram" | "whatsapp";
  sender?: "known" | "new";
  order?: QueueOrder;
};

/** What to save for the current screen: the filters and the order, nothing else (no search, cursor or limit). */
export function filtersToView(filters: QueueFilters, order: QueueOrder): ViewFilters {
  const view: ViewFilters = {};
  if (filters.state.length) view.state = [...filters.state];
  if (filters.assignee) view.assignee = filters.assignee;
  if (filters.priorityMin) view.priority_min = filters.priorityMin;
  if (filters.reason.length) view.reason = [...filters.reason];
  if (filters.topic.length) view.topic = [...filters.topic];
  if (filters.overdue) view.overdue = true;
  if (filters.waitingOver !== null) view.waiting_over = filters.waitingOver;
  if (filters.channel) view.channel = filters.channel;
  if (filters.sender) view.sender = filters.sender;
  view.order = order;
  return view;
}

const STATE_VALUES: readonly string[] = ["bot", "needs_staff", "waiting_customer", "snoozed", "resolved"];
const REASON_VALUES: readonly string[] = ["escalation", "held_reply", "customer_message", "manual"];
const TOPIC_VALUES: readonly string[] = [
  "purchase", "question", "complaint", "payment", "booking", "subscription", "other",
];
const PRIORITY_VALUES: readonly string[] = ["low", "normal", "high", "urgent"];
const ORDER_VALUES: readonly string[] = ["activity", "waiting", "due", "priority"];

/** The list of values when every one is a known value, else null. */
function knownList<T extends string>(value: unknown, known: readonly string[]): T[] | null {
  if (!Array.isArray(value)) return null;
  return value.every((v) => typeof v === "string" && known.includes(v)) ? (value as T[]) : null;
}

export type ViewRead = { supported: true; filters: QueueFilters; order: QueueOrder } | { supported: false };

/**
 * A stored view as this screen's filters and order. Absent keys, nulls, empty lists and false mean
 * not set. A key or a value this screen does not know (or the search, a cursor or a limit) makes
 * the view `supported: false`, so the screen can show it disabled instead of guessing.
 */
export function viewToFilters(view: Pick<SavedView, "filters">): ViewRead {
  const raw: unknown = view.filters;
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return { supported: false };
  const stored = raw as Record<string, unknown>;
  const filters = tabFilters("all");
  let order: QueueOrder | null = null;
  for (const [key, value] of Object.entries(stored)) {
    if (value === null || value === undefined) continue;
    switch (key) {
      case "state": {
        const list = knownList<QueueState>(value, STATE_VALUES);
        if (!list) return { supported: false };
        filters.state = list;
        break;
      }
      case "reason": {
        const list = knownList<Reason>(value, REASON_VALUES);
        if (!list) return { supported: false };
        filters.reason = list;
        break;
      }
      case "topic": {
        const list = knownList<Topic>(value, TOPIC_VALUES);
        if (!list) return { supported: false };
        filters.topic = list;
        break;
      }
      case "assignee":
        if (typeof value !== "string" || !value) return { supported: false };
        filters.assignee = value;
        break;
      case "priority_min":
        if (typeof value !== "string" || !PRIORITY_VALUES.includes(value)) return { supported: false };
        filters.priorityMin = value as Priority;
        break;
      case "overdue":
        if (typeof value !== "boolean") return { supported: false };
        filters.overdue = value;
        break;
      case "waiting_over":
        if (typeof value !== "number" || !Number.isInteger(value) || value < 1) return { supported: false };
        filters.waitingOver = value;
        break;
      case "channel":
        if (value !== "telegram" && value !== "whatsapp") return { supported: false };
        filters.channel = value;
        break;
      case "sender":
        if (value !== "known" && value !== "new") return { supported: false };
        filters.sender = value;
        break;
      case "order":
        if (typeof value !== "string" || !ORDER_VALUES.includes(value)) return { supported: false };
        order = value as QueueOrder;
        break;
      default:
        return { supported: false };
    }
  }
  // The server's own default: due-first when every row in scope needs staff, newest activity otherwise.
  const onlyNeedsStaff = (filters.state.length === 1 && filters.state[0] === "needs_staff")
    || filters.overdue || filters.waitingOver !== null;
  return { supported: true, filters, order: order ?? (onlyNeedsStaff ? "due" : "activity") };
}

/**
 * One text for a filter set and its order, the same whatever order the lists were picked in, so the
 * screen can tell whether it still shows exactly a view's filters.
 */
export function viewKey(filters: QueueFilters, order: QueueOrder): string {
  const view = filtersToView(filters, order);
  const sorted = (list: string[] | undefined) => (list ? [...list].sort() : undefined);
  return JSON.stringify({
    ...view, state: sorted(view.state), reason: sorted(view.reason), topic: sorted(view.topic),
  });
}

export type ViewChoice =
  | { supported: true; tab: QueueTabId; filters: QueueFilters; order: QueueOrder; key: string }
  | { supported: false };

function sameSet(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((value) => b.includes(value));
}

/**
 * What choosing a saved view shows. A view is independent of the tab it was saved from: it opens on
 * the most specific tab whose own filters it holds (the tab's state exactly, and every other filter
 * the tab sets), the first in tab order on a tie, so the rest of its filters are that tab's chips.
 * The state is never a chip, so a view whose state no tab has (the assistant's chats alone, say),
 * one this screen cannot read (`viewToFilters`), or one with Priority order outside "needs us" alone
 * is not supported: the menu lists it disabled rather than guess.
 */
export function viewChoice(view: Pick<SavedView, "filters">): ViewChoice {
  const read = viewToFilters(view);
  if (!read.supported) return { supported: false };
  const { filters, order } = read;
  if (order === "priority" && !priorityOrderAllowed(filters)) return { supported: false };
  let best: QueueTab | null = null;
  for (const tab of TABS) {
    const own = tab.filters;
    if (!sameSet(own.state ?? [], filters.state)) continue;
    const holds = Object.entries(own).every(([name, value]) => {
      const mine = filters[name as keyof QueueFilters];
      return Array.isArray(value) ? Array.isArray(mine) && sameSet(value, mine) : value === mine;
    });
    if (!holds) continue;
    if (best === null || Object.keys(own).length > Object.keys(best.filters).length) best = tab;
  }
  if (best === null) return { supported: false };
  // The tab's own state list, in its own order, so the query is the tab's query plus the chips.
  const shown = { ...filters, state: tabFilters(best.id).state };
  return { supported: true, tab: best.id, filters: shown, order, key: viewKey(shown, order) };
}

// ---- notices and the other error sentences ----

/**
 * The plain notice after a queue action (single item). `already_handled` means nothing changed.
 * `now` places the snooze time ("Wed 09:00", with the date when more than six days ahead).
 */
export function queueActionNotice(
  action: QueueActionName,
  outcome: "applied" | "already_handled",
  detail: { until?: string | null; priority?: Priority; topic?: Topic; assigneeName?: string | null },
  now: number,
): string {
  if (outcome === "already_handled") return "Already up to date.";
  switch (action) {
    case "claim": return "You took this conversation.";
    case "unclaim": return "Gave up this item.";
    case "resolve": return "Resolved. The assistant has this conversation again.";
    case "priority": return detail.priority ? `Priority set to ${PRIORITY_LABELS[detail.priority]}.` : "Priority changed.";
    case "topic": return detail.topic ? `Topic set to ${TOPIC_LABELS[detail.topic]}.` : "Topic changed.";
    case "assign": return detail.assigneeName ? `Assigned to ${detail.assigneeName}.` : "Assigned.";
    case "snooze": {
      const when = snoozeUntilText(detail.until, now);
      return when ? `Snoozed until ${when}.` : "Snoozed.";
    }
  }
}

/** A plain sentence for a failed saved-view change. `code` is the server's plain-text detail. */
export function viewErrorText(status: number | null, code: string | null, fallback: string): string {
  if (status === null) return actionErrorText(null, null, fallback);
  if (status === 409 && code !== null) {
    if (code.startsWith("you can keep at most")) return "You can keep at most 20 saved views. Delete one first.";
    if (code.includes("already have a saved view")) return "You already have a view with that name.";
  }
  if (status === 422) return "That name or filter is not allowed.";
  if (status === 403) return "You do not have permission to do that.";
  if (status === 404) return "That view is not available any more. The list is refreshed.";
  return fallback;
}

/** A plain sentence for a failed settings save. A version conflict means the settings were reloaded. */
export function settingsErrorText(status: number | null, code: string | null, fallback: string): string {
  if (status === null) return actionErrorText(null, null, fallback);
  if (status === 409 && code === "version_conflict") {
    return "These settings changed while you were editing. They are reloaded: check them and save again.";
  }
  if (status === 403) return "You do not have permission to do that.";
  if (status === 422) return "One of these values is out of range. Check them and save again.";
  return fallback;
}

/** How a failed assignee read is told apart: 503 is the member directory being down, anything else is a failure. */
export function assigneesFailure(status: number | null): { unavailable: boolean; failed: boolean } {
  return status === 503 ? { unavailable: true, failed: false } : { unavailable: false, failed: true };
}
