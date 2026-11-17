/** Inbox wire types and pure helpers. No imports, so node tests can load this file directly. */

export type Contact = {
  display_name: string | null;
  telegram_username: string | null;
  phone_masked: string | null;
  /** Set only by the single-conversation read; list rows carry the mask. */
  phone: string | null;
  kind: string;
  known: boolean;
};

export type LastMessage = { preview: string; at: string };

/** The assistant's note to staff from its handoff; only the single-conversation read carries it. */
export type HandoffNote = { text: string; at: string };

/** Where an item is in the work queue (`bot` means the assistant has it and it is not in the queue). */
export type QueueState = "bot" | "needs_staff" | "waiting_customer" | "snoozed" | "resolved";
export type Priority = "low" | "normal" | "high" | "urgent";
export type Reason = "escalation" | "held_reply" | "customer_message" | "manual";
export type Topic = "purchase" | "question" | "complaint" | "payment" | "booking" | "subscription" | "other";
/** One id's result in a bulk queue action. */
export type BulkOutcome =
  | "done"
  | "already_handled"
  | "held_reply_pending"
  | "customer_wrote"
  | "not_found"
  | "not_in_queue"
  | "not_allowed";

/** The assignee as the member directory knows them; the name is null when it cannot be read. */
export type Assignee = { id: string; display_name: string | null };

export type Conversation = {
  id: string;
  channel: "telegram" | "whatsapp";
  status: string;
  owned_by: string | null;
  ai_muted_until: string | null;
  human_muted: boolean;
  inbox_version: number;
  opened_at: string;
  contact: Contact | null;
  last_message: LastMessage | null;
  pending_approvals: number;
  handoff_note?: HandoffNote | null;
  queue_status: QueueState;
  /** Set only while the item is `needs_staff`. */
  queue_reason: Reason | null;
  /** Null until a person sets it. */
  topic: Topic | null;
  priority: Priority;
  /** Set even when `assignee` has no name, so a claimed item always shows as claimed. */
  assignee_id: string | null;
  assignee: Assignee | null;
  claimed_at: string | null;
  /** The clock: when the item started waiting on a person. Null outside `needs_staff`. */
  needs_staff_since: string | null;
  due_at: string | null;
  snoozed_until: string | null;
  last_customer_at: string | null;
  last_staff_reply_at: string | null;
};

/** The four delivery fields, restated structurally (same shape as DeliveryState in channels-api). */
export type Message = {
  id: string;
  direction: string;
  author: string;
  body: string;
  created_at: string;
  delivery_status: string | null;
  delivery_error: string | null;
  provider_status: "sent" | "delivered" | "read" | "failed" | null;
  provider_error: string | null;
  /** Set only on an outbound picture reply: the stored picture, read through the media route. */
  picture?: { asset_id: string } | null;
};

/** The stored picture a message shows, or null (also for a server that predates the field). */
export function messagePictureId(message: { picture?: { asset_id: string } | null }): string | null {
  return message.picture?.asset_id ?? null;
}

export type Approval = {
  id: number;
  conversation_id: string;
  draft: string;
  reason: string | null;
  status: string;
  expires_at: string;
};

export type Page<T> = { items: T[]; next_cursor: string | number | null };
/** The tab counts. `mine` is the asking person's items in `needs_staff` or `waiting_customer`. */
export type QueueCounts = {
  needs_us: number;
  mine: number;
  unassigned: number;
  overdue: number;
  waiting_customer: number;
  snoozed: number;
  oldest_waiting_at: string | null;
};
export type ConversationPage = Page<Conversation> & {
  needs_review_count: number;
  /** Null when the server could not read them; a client shows "–", never 0. */
  counts: QueueCounts | null;
  /** The server's time of the read: the bulk route's `seen_at`. */
  as_of: string;
};

/** The seven single-item queue actions; the bulk route takes the same names. */
export type QueueActionName = "claim" | "unclaim" | "assign" | "snooze" | "resolve" | "priority" | "topic";
/**
 * What an action needs beyond the item. `assigneeName` is for the notice only and is never sent.
 * A field the action does not take is ignored by the body builders.
 */
export type QueueFields = {
  assignee_id?: string;
  assigneeName?: string | null;
  until?: string;
  priority?: Priority;
  topic?: Topic;
};
/** `applied` when something changed, `already_handled` when nothing did. */
export type QueueActionResult = { outcome: "applied" | "already_handled"; conversation: Conversation };
export type BulkResult = { id: string; outcome: BulkOutcome };
export type BulkResponse = { results: BulkResult[] };
/** A person a conversation can be assigned to (no address is ever sent). */
export type InboxAssignee = { id: string; display_name: string; role: "tenant_admin" | "tenant_staff" };
/** The company's response targets and reminder knobs, `version` 0 until a first save. */
export type InboxSettings = {
  target_urgent_min: number;
  target_high_min: number;
  target_normal_min: number;
  target_low_min: number;
  repeat_minutes: number;
  max_repeats: number;
  follow_up_hours: number;
  stale_claim_hours: number;
  handback_prompt_hours: number;
  version: number;
};
export type InboxSettingsValues = Omit<InboxSettings, "version">;
export type InboxSettingsKey = keyof InboxSettingsValues;
/** The settings form as typed: one string per field. */
export type SettingsDraft = Record<InboxSettingsKey, string>;
/** A saved view as read back; `filters` carries the API's keys (see `viewToFilters`). */
export type SavedView = { id: string; name: string; filters: Record<string, unknown>; created_at: string };

export type ChannelFilter = "" | "whatsapp" | "telegram";
export type SenderFilter = "" | "known" | "new";

const INBOX = "/console/v1/inbox";

/** Approvals for one conversation, used by the customer card. */
export function approvalsPath(o: {
  tenantQuery: string;
  conversationId: string;
  includeHandled: boolean;
}): string {
  const parts: string[] = [];
  if (o.tenantQuery) parts.push(o.tenantQuery);
  parts.push(`conversation_id=${encodeURIComponent(o.conversationId)}`);
  parts.push(`include_handled=${o.includeHandled}`);
  return `${INBOX}/approvals?${parts.join("&")}`;
}

/** Same rule as ServicesWorkspace's canManage. */
export function canConnectWhatsApp(role: string | null): boolean {
  return role === "platform_admin" || role === "tenant_admin";
}

/** First occurrence wins, order preserved. Pages can repeat a row when activity reorders the list. */
export function mergeById<T extends { id: string | number }>(items: T[]): T[] {
  const seen = new Set<string | number>();
  const merged: T[] = [];
  for (const item of items) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    merged.push(item);
  }
  return merged;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Relative time for the conversation list. `now` is epoch milliseconds. */
export function listTime(at: string, now: number): string {
  const time = new Date(at);
  if (Number.isNaN(time.getTime())) return "";
  const elapsed = Math.max(0, now - time.getTime());
  const minutes = Math.floor(elapsed / 60_000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h`;
  const today = new Date(now);
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
  if (
    time.getFullYear() === yesterday.getFullYear() &&
    time.getMonth() === yesterday.getMonth() &&
    time.getDate() === yesterday.getDate()
  ) return "Yesterday";
  const label = `${time.getDate()} ${MONTHS[time.getMonth()]}`;
  return time.getFullYear() === today.getFullYear() ? label : `${label} ${time.getFullYear()}`;
}

// ---- queue routes ----

/** The tenant query as a suffix: empty stays empty, so no stray question mark. */
function withQuery(path: string, query: string): string {
  return query ? `${path}?${query}` : path;
}

/** `base` is the conversation's own path, `/console/v1/inbox/conversations/{id}`. */
export function queueActionPath(base: string, action: QueueActionName, query: string): string {
  return withQuery(`${base}/queue/${action}`, query);
}

export function bulkPath(query: string): string {
  return withQuery(`${INBOX}/bulk`, query);
}

export function assigneesPath(query: string): string {
  return withQuery(`${INBOX}/assignees`, query);
}

export function settingsPath(query: string): string {
  return withQuery(`${INBOX}/settings`, query);
}

export function viewsPath(query: string, id?: string): string {
  return withQuery(id === undefined ? `${INBOX}/views` : `${INBOX}/views/${encodeURIComponent(id)}`, query);
}

/** The one field an action takes, under the API's names; nothing for claim, unclaim and resolve. */
function actionField(action: QueueActionName, fields: QueueFields): Record<string, unknown> {
  switch (action) {
    case "assign": return fields.assignee_id === undefined ? {} : { assignee_id: fields.assignee_id };
    case "snooze": return fields.until === undefined ? {} : { until: fields.until };
    case "priority": return fields.priority === undefined ? {} : { priority: fields.priority };
    case "topic": return fields.topic === undefined ? {} : { topic: fields.topic };
    default: return {};
  }
}

/**
 * The body of one action: the key, the version the person read and the action's field. Snooze and
 * resolve also say when the customer last wrote, so the server can refuse a stale decision.
 */
export function queueActionBody(
  action: QueueActionName,
  item: Pick<Conversation, "inbox_version" | "last_customer_at">,
  fields: QueueFields,
  key: string = crypto.randomUUID(),
): Record<string, unknown> {
  const seen = action === "snooze" || action === "resolve"
    ? { seen_customer_at: item.last_customer_at ?? null }
    : {};
  return { request_key: key, expected_version: item.inbox_version, ...actionField(action, fields), ...seen };
}

/** `seenAt` is the list's `as_of`: the server refuses an item whose customer wrote after it. */
export function bulkBody(
  action: QueueActionName,
  ids: string[],
  fields: QueueFields,
  seenAt: string,
  key: string = crypto.randomUUID(),
): Record<string, unknown> {
  return { request_key: key, action, ids, seen_at: seenAt, ...actionField(action, fields) };
}

/**
 * The code of a coded refusal, `{"detail": {"code": "..."}}`, read from an error that carries the
 * decoded body (ApiError.body). A plain-text detail, a list or no body gives null, so a stale-version
 * 409 (plain text) is told apart from `held_reply_pending` and `customer_wrote`.
 */
export function errorCode(cause: unknown): string | null {
  if (typeof cause !== "object" || cause === null || !("body" in cause)) return null;
  const body = cause.body;
  if (typeof body !== "object" || body === null || !("detail" in body)) return null;
  const detail = body.detail;
  if (typeof detail !== "object" || detail === null || !("code" in detail)) return null;
  return typeof detail.code === "string" ? detail.code : null;
}

// ---- response times (the company's Inbox settings) ----

/**
 * The lowest and highest value the API takes for each setting (csa/console/inbox_settings.py,
 * InboxSettingsIn; tests/test_inbox_queue_browser.py compares the two). Keys in the API's order.
 */
export const SETTINGS_RANGES: Record<InboxSettingsKey, readonly [number, number]> = {
  target_urgent_min: [5, 10080],
  target_high_min: [5, 10080],
  target_normal_min: [5, 10080],
  target_low_min: [5, 10080],
  repeat_minutes: [15, 1440],
  max_repeats: [0, 10],
  follow_up_hours: [0, 720],
  stale_claim_hours: [0, 168],
  handback_prompt_hours: [0, 720],
};
const SETTINGS_KEYS = Object.keys(SETTINGS_RANGES) as InboxSettingsKey[];
/** The four response targets, most urgent first. */
export const TARGET_KEYS = ["target_urgent_min", "target_high_min", "target_normal_min", "target_low_min"] as const;
const SETTING_UNITS: Record<InboxSettingsKey, "minutes" | "hours" | "count"> = {
  target_urgent_min: "minutes", target_high_min: "minutes", target_normal_min: "minutes", target_low_min: "minutes",
  repeat_minutes: "minutes", max_repeats: "count",
  follow_up_hours: "hours", stale_claim_hours: "hours", handback_prompt_hours: "hours",
};

/** A typed value as the whole number it says, or null (digits only: no sign, point or exponent). */
function wholeNumber(raw: string): number | null {
  const text = raw.trim();
  return /^[0-9]+$/.test(text) ? Number(text) : null;
}

/** Why the server would refuse this value, in plain words, or null when it takes it. */
export function settingProblem(key: InboxSettingsKey, raw: string): string | null {
  const [low, high] = SETTINGS_RANGES[key];
  const value = wholeNumber(raw);
  if (value !== null && value >= low && value <= high) return null;
  const unit = SETTING_UNITS[key];
  return unit === "count"
    ? `Enter a whole number, from ${low} to ${high}.`
    : `Enter a whole number of ${unit}, from ${low} to ${high}.`;
}

/** The settings as the form shows them. */
export function settingsDraft(settings: InboxSettingsValues): SettingsDraft {
  return Object.fromEntries(SETTINGS_KEYS.map((key) => [key, String(settings[key])])) as SettingsDraft;
}

/** The nine values a draft says, or null while any of them would be refused. */
export function draftValues(draft: SettingsDraft): InboxSettingsValues | null {
  const values: Partial<InboxSettingsValues> = {};
  for (const key of SETTINGS_KEYS) {
    if (settingProblem(key, draft[key]) !== null) return null;
    values[key] = wholeNumber(draft[key]) as number;
  }
  return values as InboxSettingsValues;
}

/** The PUT body: the nine fields and the version that was read, nothing else (the server refuses more). */
export function settingsBody(values: InboxSettingsValues, version: number): InboxSettings {
  const body = Object.fromEntries(SETTINGS_KEYS.map((key) => [key, values[key]])) as InboxSettingsValues;
  return { ...body, version };
}

/** Minutes as hours and minutes ("1 h 30 min"); empty for anything but a whole number from 0. */
export function minutesText(minutes: number): string {
  if (!Number.isInteger(minutes) || minutes < 0) return "";
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  if (hours === 0) return `${rest} min`;
  return rest === 0 ? `${hours} h` : `${hours} h ${rest} min`;
}

/** A stored setting in plain words, for the read-only lists; 0 hours is "Off". */
export function reminderSettingText(key: InboxSettingsKey, value: number): string {
  const unit = SETTING_UNITS[key];
  if (unit === "count") return String(value);
  if (unit === "minutes") {
    const words = `${value} minute${value === 1 ? "" : "s"}`;
    return value >= 60 ? `${words} (${minutesText(value)})` : words;
  }
  return value === 0 ? "Off" : `${value} hour${value === 1 ? "" : "s"}`;
}
