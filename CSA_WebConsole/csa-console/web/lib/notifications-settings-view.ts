/**
 * Pure rules and English copy behind the Notifications settings page (no React, no I/O; only
 * `import type`, so node tests load this file directly).
 *
 * Event and kind keys come from the server and can be ones this version does not know: they
 * read as the raw key, never as an error. The ranges below mirror `SETTINGS_RANGES` in
 * lib/inbox-api.ts and `InboxSettingsIn` in csa/console/inbox_settings.py; the tests compare
 * them, so a change on one side fails until the other follows.
 */
import type { NotificationTarget } from "./notifications-settings-api";

/** Events a company target can subscribe to, in the server's order (EVENTS_OFFERED). */
export const EVENT_KEYS = [
  "escalation",
  "approval_needed",
  "order_submitted",
  "dlq",
  "circuit_opened",
  "booking_pending",
  "inbox.overdue",
] as const;

/** The six kinds of personal notification (csa/notify/prefs.py KINDS), in the server's order. */
export const KIND_KEYS = [
  "inbox.assigned",
  "inbox.overdue",
  "inbox.snooze_due",
  "inbox.stale_claim",
  "inbox.follow_up",
  "inbox.handback",
] as const;

const EVENT_LABELS: Record<string, string> = {
  escalation: "Customer needs a person",
  approval_needed: "Order needs approval",
  order_submitted: "Order submitted",
  dlq: "Failed task",
  circuit_opened: "Assistant paused itself after errors",
  booking_pending: "Booking waiting for confirmation",
  "inbox.overdue": "Customer waiting too long",
};

const KIND_LABELS: Record<string, string> = {
  "inbox.assigned": "Assigned to me",
  "inbox.overdue": "Overdue customer",
  "inbox.snooze_due": "Snooze ended",
  "inbox.stale_claim": "Claimed but quiet",
  "inbox.follow_up": "Follow up with customer",
  "inbox.handback": "Hand back to the assistant",
};

const PRIORITY_LABELS: Record<string, string> = {
  low: "Low",
  normal: "Normal",
  high: "High",
  urgent: "Urgent",
};

/** The label for `key`, or the key itself when there is none (own keys only, never inherited ones). */
function labelOf(labels: Record<string, string>, key: unknown): string {
  if (typeof key === "string" && Object.hasOwn(labels, key)) return labels[key];
  return typeof key === "string" ? key : "";
}

/** A company-target event in plain words; an unknown event reads as its key. */
export function eventLabel(event: string): string {
  return labelOf(EVENT_LABELS, event);
}

/** A personal notification kind in plain words; an unknown kind reads as its key. */
export function kindLabel(kind: string): string {
  return labelOf(KIND_LABELS, kind);
}

export function priorityLabel(priority: string): string {
  return labelOf(PRIORITY_LABELS, priority);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "18 Nov 14:02" in `timeZone`, UTC when the zone is unknown; "" when the time does not parse. */
function dayAndTime(iso: unknown, timeZone: string): string {
  if (typeof iso !== "string") return "";
  const at = new Date(iso);
  if (Number.isNaN(at.getTime())) return "";
  const parts = (zone: string) =>
    new Intl.DateTimeFormat("en-GB", {
      timeZone: zone,
      day: "numeric",
      month: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(at);
  let found: Intl.DateTimeFormatPart[];
  try {
    found = parts(timeZone);
  } catch {
    found = parts("UTC");
  }
  const get = (type: string) => found.find((part) => part.type === type)?.value ?? "";
  const month = MONTHS[Number(get("month")) - 1];
  if (!month) return "";
  return `${get("day")} ${month} ${get("hour")}:${get("minute")}`;
}

/**
 * Why a target is flagged, or null when its last delivery worked: "Last delivery failed, 5
 * attempts, 18 Nov 14:02". The time is written in `timeZone` (UTC by default, so the text never
 * depends on the machine; the page passes the browser's zone). A time or count that cannot be
 * read is left out rather than shown as "NaN".
 */
export function failureLine(t: Pick<NotificationTarget, "last_failure">, timeZone = "UTC"): string | null {
  const failure = t.last_failure;
  if (!failure) return null;
  const pieces = ["Last delivery failed"];
  const { attempts } = failure;
  if (typeof attempts === "number" && Number.isInteger(attempts) && attempts > 0) {
    pieces.push(`${attempts} ${attempts === 1 ? "attempt" : "attempts"}`);
  }
  const when = dayAndTime(failure.at, timeZone);
  if (when) pieces.push(when);
  return pieces.join(", ");
}

/** The quiet window in words, for the read-only lists. */
export function quietHoursText(q: { start: string; end: string; tz: string } | null): string {
  if (!q) return "No quiet hours";
  return `${q.start} to ${q.end} (${q.tz}), only urgent alerts`;
}

const CLOCK = /^([01]\d|2[0-3]):[0-5]\d$/;

function validZone(tz: string): boolean {
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

/**
 * Why the server would refuse this window, in plain words, or null when it takes it. Times are
 * 24-hour `HH:MM`, start and end must differ (a window may cross midnight), and the zone must be
 * a real time zone name such as `Asia/Kuala_Lumpur`.
 */
export function validateQuietHours(start: string, end: string, tz: string): string | null {
  if (typeof start !== "string" || !CLOCK.test(start)) return "Enter the start time as hours and minutes, like 22:00.";
  if (typeof end !== "string" || !CLOCK.test(end)) return "Enter the end time as hours and minutes, like 07:00.";
  if (start === end) return "The start and end times must be different.";
  if (typeof tz !== "string" || tz.trim() === "" || tz !== tz.trim() || !validZone(tz)) {
    return "Choose a time zone from the list.";
  }
  return null;
}

const MAX_ADDRESS = 254;
const MAX_LOCAL = 64;
const LABEL = "[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?";
// The server's pattern (csa/console/notification_targets.py): a local part with no space or
// address punctuation, then a dotted domain, so `a@b` (no dot) is refused.
const ADDRESS = new RegExp(`^[^\\s@<>,;"'()\\[\\]\\\\]+@${LABEL}(?:\\.${LABEL})+$`);

/**
 * Why this is not an address the server takes, in plain words, or null when it is. The caller
 * trims and lowercases first, so spaces and line breaks around the address count as a problem.
 */
export function validateEmailAddress(address: string): string | null {
  const problem = "Enter a full email address, like name@example.com.";
  if (typeof address !== "string" || address === "") return problem;
  if (address.length > MAX_ADDRESS) return "That email address is too long.";
  if (address.indexOf("@") > MAX_LOCAL) return "That email address is too long.";
  if (/[\u0000-\u001f\u007f]/.test(address) || !ADDRESS.test(address)) return problem;
  return null;
}

export type TimingKey =
  | "target_urgent_min"
  | "target_high_min"
  | "target_normal_min"
  | "target_low_min"
  | "repeat_minutes"
  | "max_repeats"
  | "follow_up_hours"
  | "stale_claim_hours"
  | "handback_prompt_hours";

export interface TimingField {
  key: TimingKey;
  label: string;
  unit: "minutes" | "hours" | "count";
  min: number;
  max: number;
  /** True when 0 turns the reminder off. */
  zeroOff: boolean;
}

/** The nine Inbox timing settings in the API's order, as the form lists them. */
export const timingFields: readonly TimingField[] = [
  { key: "target_urgent_min", label: "Reply to urgent customers within", unit: "minutes", min: 5, max: 10080, zeroOff: false },
  { key: "target_high_min", label: "Reply to high priority customers within", unit: "minutes", min: 5, max: 10080, zeroOff: false },
  { key: "target_normal_min", label: "Reply to normal priority customers within", unit: "minutes", min: 5, max: 10080, zeroOff: false },
  { key: "target_low_min", label: "Reply to low priority customers within", unit: "minutes", min: 5, max: 10080, zeroOff: false },
  { key: "repeat_minutes", label: "Remind again every", unit: "minutes", min: 15, max: 1440, zeroOff: false },
  { key: "max_repeats", label: "Reminders to send at most", unit: "count", min: 0, max: 10, zeroOff: false },
  { key: "follow_up_hours", label: "Ask staff to follow up after", unit: "hours", min: 0, max: 720, zeroOff: true },
  { key: "stale_claim_hours", label: "Warn about a claimed chat with no reply after", unit: "hours", min: 0, max: 168, zeroOff: true },
  { key: "handback_prompt_hours", label: "Suggest handing back to the assistant after", unit: "hours", min: 0, max: 720, zeroOff: true },
];

/** The whole number a typed (or numeric) value says, or null (digits only: no sign, point or exponent). */
function wholeNumber(raw: unknown): number | null {
  if (typeof raw === "number") return Number.isInteger(raw) ? raw : null;
  if (typeof raw !== "string") return null;
  const text = raw.trim();
  return /^[0-9]+$/.test(text) ? Number(text) : null;
}

function rangeSentence(field: TimingField): string {
  const { min, max, unit } = field;
  return unit === "count"
    ? `Enter a whole number, from ${min} to ${max}.`
    : `Enter a whole number of ${unit}, from ${min} to ${max}.`;
}

/**
 * The problem with each field the server would refuse, keyed by field; an empty object when all
 * nine are fine. Values may be typed text or numbers; a missing one is a problem.
 */
export function validateTiming(values: Partial<Record<TimingKey, unknown>>): Partial<Record<TimingKey, string>> {
  const errors: Partial<Record<TimingKey, string>> = {};
  for (const field of timingFields) {
    const value = wholeNumber(values?.[field.key]);
    if (value === null || value < field.min || value > field.max) errors[field.key] = rangeSentence(field);
  }
  return errors;
}

/**
 * The sentence for a save that lost a race (409 `version_conflict`). The page has already read the
 * latest by then, so it says so, instead of the generic "Reload to see the latest".
 */
export const SAVE_CONFLICT =
  "Someone else changed this, so your change was not saved. The latest is shown; check it and save again.";
