import { dateText } from "./hr-people";
import type { Anchor, AnchorDates, AutoCheck, DefaultTemplatesReceipt, OwnerRole, ProcessKind, ProcessStatus, TaskStatus } from "./hr-process-api";

/** Pure words for processes and tasks: due dates, statuses, offsets and progress. */

const DAY = 86_400_000;

const KUALA_LUMPUR_DAY = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Kuala_Lumpur", year: "numeric", month: "2-digit", day: "2-digit",
});
/**
 * Today's date as `YYYY-MM-DD` in Kuala Lumpur, the source of `today` for `dueText`: the server decides
 * `overdue` ("due before today") on that calendar, whatever zone the browser is in.
 */
export function processToday(now: Date = new Date()): string {
  const part = (type: string) => KUALA_LUMPUR_DAY.formatToParts(now).find((item) => item.type === type)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}

function plural(count: number, one: string): string {
  return `${count} ${one}${count === 1 ? "" : "s"}`;
}
/** Whole calendar days from `from` to `to` (both `YYYY-MM-DD`); negative when `to` is earlier. */
function daysBetween(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / DAY);
}

/**
 * When a step is due, from `today` (`YYYY-MM-DD`, the viewer's calendar day). A step that is done or
 * skipped is never "overdue": it just says the date. Take `today` from `processToday()`.
 */
export function dueText(task: { due_on: string | null; status: TaskStatus }, today: string): string {
  if (task.due_on === null) return "No date yet";
  if (task.status === "done" || task.status === "skipped") return `Due on ${dateText(task.due_on)}`;
  const days = daysBetween(today, task.due_on);
  if (days === 0) return "Due today";
  if (days === 1) return "Due tomorrow";
  if (days > 1) return `Due in ${days} days`;
  return `Overdue by ${plural(-days, "day")}`;
}

const STATUS: Record<string, string> = {
  todo: "To do",
  awaiting_approval: "Waiting for approval",
  done: "Done",
  rejected: "Sent back",
  skipped: "Skipped",
  active: "In progress",
  completed: "Completed",
  cancelled: "Cancelled",
};
/** A task or process status in words. A status a later release adds reads "Unknown", never the code. */
export function statusLabel(status: TaskStatus | ProcessStatus | (string & {})): string {
  return Object.hasOwn(STATUS, status) ? STATUS[status] : "Unknown";
}

const OWNER_ROLE: Record<OwnerRole, string> = {
  hr: "HR team",
  manager: "Manager",
  employee: "The employee",
  person: "A named person",
};
export function ownerRoleLabel(role: OwnerRole | (string & {})): string {
  return Object.hasOwn(OWNER_ROLE, role) ? OWNER_ROLE[role as OwnerRole] : "Unknown";
}

const KIND: Record<ProcessKind, string> = { onboarding: "Onboarding", offboarding: "Offboarding", custom: "Custom process" };
/** A process kind in words, for a process without a template name. */
export function processKindLabel(kind: ProcessKind | (string & {})): string {
  return Object.hasOwn(KIND, kind) ? KIND[kind as ProcessKind] : "Process";
}

/** What each automatic check looks for: [said to the assignee, said about the person]. */
const AUTO_CHECK: Record<Exclude<AutoCheck, "access_removed">, [mine: string, theirs: string]> = {
  account_linked: ["your console account is linked", "the person's console account is linked"],
  personal_details_complete: ["your personal details are filled in", "the person's personal details are filled in"],
  "document_uploaded:contract": ["a contract is uploaded", "a contract is uploaded"],
  "document_uploaded:identity": ["an identity document is uploaded", "an identity document is uploaded"],
  "document_uploaded:certificate": ["a certificate is uploaded", "a certificate is uploaded"],
  "document_uploaded:other": ["a document is uploaded", "a document is uploaded"],
};
/**
 * The hint on a step with an automatic check, to its assignee (`mine`) or to someone acting on it.
 * `access_removed` is done by the system alone; a check a later release adds stays a plain sentence.
 */
export function autoCheckText(check: AutoCheck | (string & {}), mine: boolean): string {
  if (check === "access_removed")
    return `Done by the system when ${mine ? "your" : "the person's"} access is removed.`;
  if (!Object.hasOwn(AUTO_CHECK, check)) return "Checked automatically.";
  const [own, theirs] = AUTO_CHECK[check as keyof typeof AUTO_CHECK];
  return `Checked automatically: ${mine ? own : theirs}.`;
}

const ANCHOR: Record<Anchor, string> = {
  start_date: "the start date",
  last_day: "the last day",
  probation_end: "the probation end date",
};
/** "5 days after the start date", "2 days before the last day", "On the start date". */
export function offsetSentence(anchor: Anchor, offset: number): string {
  const date = ANCHOR[anchor];
  if (offset === 0) return `On ${date}`;
  return `${plural(Math.abs(offset), "day")} ${offset > 0 ? "after" : "before"} ${date}`;
}

/** "4 of 7 required steps" (`required`), "4 of 7 steps", "No steps". */
export function progressText(done: number, total: number, required = false): string {
  const noun = required ? "required step" : "step";
  if (total === 0) return `No ${noun}s`;
  return `${done} of ${total} ${noun}${total === 1 ? "" : "s"}`;
}

const EVENT: Record<string, string> = {
  "process.started": "Process started",
  "process.cancelled": "Process cancelled",
  "process.completed": "Process completed",
  "task.created": "Step added",
  "task.completed": "Step marked done",
  "task.skipped": "Step skipped",
  "task.reassigned": "Step reassigned",
  "task.date_changed": "Due date moved with its date",
  "task.rescheduled": "Step rescheduled",
  "task.approved": "Step approved",
  "task.sent_back": "Step sent back",
  "task.noted": "Note changed",
};
/** What a history event did. Later releases add actions: one this console does not know reads generically. */
export function eventText(action: string): string {
  return Object.hasOwn(EVENT, action) ? EVENT[action] : "Something changed";
}

/** The `reason` codes the migrations (0632-0636) write, as sentences. */
const REASON: Record<string, string> = {
  process_cancelled: "Skipped because the process was cancelled",
  process_completed: "Skipped because the process was completed",
  process_closed_anyway: "Skipped because HR closed the process",
  superseded: "Replaced by a new process",
  last_day_cleared: "Cancelled because the last day was cleared",
  rehired: "Cancelled because the person was rehired",
  cancelled_by_hr: "Cancelled by HR",
  person_left: "The person left",
  skipped_by_hr: "Skipped by HR",
  reassigned_by_hr: "Reassigned by HR",
  closed_anyway: "Closed by HR before every step was done",
  no_manager: "The person has no manager",
  manager_inactive: "Their manager is not active",
  manager_unlinked: "Their manager has no console account",
  person_inactive: "The person is not active",
};
/** Why a step went to the HR team (`fallback: "hr_pool"`), inside a sentence. */
const POOL_REASON: Record<string, string> = {
  no_manager: "the person has no manager",
  manager_inactive: "their manager is not active",
  manager_unlinked: "their manager has no console account",
  person_inactive: "the person is not active",
  person_left: "the person left",
};
/** The keys `eventLines` says in words; any other key is shown as plain "key: value". */
const KNOWN_DETAIL: ReadonlySet<string> = new Set([
  "reason", "fallback", "remark", "from", "to", "assignee_employee_id", "old", "new", "due_on", "anchor", "clamped",
  "kind", "template_id", "template_version", "owner_role", "auto_check", "awaiting_approval", "on_behalf", "approver",
  "approved_without_second_person", "sent_back",
]);
const ID_TEXT = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i;
const known = <T extends string>(table: Record<T, string>, value: unknown): value is T =>
  typeof value === "string" && Object.hasOwn(table, value);

/** A value of a key the console does not know, as plain text; null when it holds an id (never shown). */
function plainValue(value: unknown): string | null {
  const text = typeof value === "string" ? value
    : value === null || value === undefined ? "none"
      : typeof value === "boolean" ? (value ? "yes" : "no")
        : typeof value === "number" ? String(value) : JSON.stringify(value);
  return ID_TEXT.test(text) ? null : text;
}

/**
 * A history event's `detail` in words, one line each: the reason as a sentence (an unknown one reads
 * "Updated"), HR's own words (`remark`), people by name through `nameOf` (an id the page cannot name
 * is never shown), dates as "27 Oct 2026". A key a later release adds is shown as "key: value",
 * unless its value holds an id. Line breaks in HR's words are kept.
 */
export function eventLines(detail: Record<string, unknown>, nameOf: (employeeId: string) => string | null): string[] {
  const lines: string[] = [];
  const has = (key: string) => Object.hasOwn(detail, key);
  /** A person: null is the HR team; undefined is someone the page cannot name. */
  const who = (value: unknown) => value === null ? "the HR team"
    : typeof value === "string" ? nameOf(value) ?? undefined : undefined;
  const day = (value: unknown) => value === null ? "no date"
    : typeof value === "string" && isDateText(value) ? dateText(value) : "another date";
  const pooled = detail.fallback === "hr_pool";
  if (pooled)
    lines.push(known(POOL_REASON, detail.reason) ? `Assigned to the HR team (${POOL_REASON[detail.reason]})` : "Assigned to the HR team");
  else if (has("reason")) lines.push(known(REASON, detail.reason) ? REASON[detail.reason] : "Updated");
  if (typeof detail.remark === "string" && detail.remark.trim()) lines.push(`Note: ${detail.remark}`);
  if (has("to") && !pooled) {
    const to = who(detail.to);
    lines.push(to === undefined ? "Assigned to someone else" : `Assigned to ${to}`);
  }
  if (has("from")) {
    const from = who(detail.from);
    if (from !== undefined) lines.push(`Was ${from}`);
  }
  if (has("assignee_employee_id") && !pooled) {
    const assignee = who(detail.assignee_employee_id);
    if (assignee !== undefined) lines.push(`Assigned to ${assignee}`);
  }
  if (has("old") || has("new")) lines.push(`Due date changed from ${day(detail.old)} to ${day(detail.new)}`);
  if (typeof detail.due_on === "string" && isDateText(detail.due_on)) lines.push(`Due on ${dateText(detail.due_on)}`);
  if (known(ANCHOR, detail.anchor)) lines.push(`Counted from ${ANCHOR[detail.anchor]}`);
  if (detail.clamped === true) lines.push("Kept on or before the last day");
  if (known(KIND, detail.kind)) lines.push(KIND[detail.kind]);
  if (typeof detail.template_version === "number") lines.push(`Template version ${detail.template_version}`);
  if (known(OWNER_ROLE, detail.owner_role)) lines.push(`Owner: ${OWNER_ROLE[detail.owner_role]}`);
  if (typeof detail.auto_check === "string") lines.push(autoCheckText(detail.auto_check, false));
  if (detail.awaiting_approval === true) lines.push("Waiting for approval");
  if (detail.on_behalf === true) lines.push("Done by HR for the person it is assigned to");
  if (detail.approver === "hr" || detail.approver === "manager")
    lines.push(`Approved by ${detail.approver === "hr" ? "HR" : "the manager"}`);
  if (detail.approved_without_second_person === true)
    lines.push("Approved without a second person: no one else in HR could approve it");
  if (detail.sent_back === true) lines.push("Sent back after the person left, so it was skipped");
  for (const [key, value] of Object.entries(detail)) {
    if (KNOWN_DETAIL.has(key)) continue;
    const text = plainValue(value);
    if (text !== null) lines.push(`${key.replaceAll("_", " ")}: ${text}`);
  }
  return lines;
}

/** History items: each run of `task.created` events (one per step of a start) becomes one item. */
export type HistoryItem<E> = { event: E; created?: undefined } | { created: E[]; event?: undefined };
export function groupHistory<E extends { action: string }>(events: readonly E[]): HistoryItem<E>[] {
  const items: HistoryItem<E>[] = [];
  for (const event of events) {
    const last = items.at(-1);
    if (event.action !== "task.created") items.push({ event });
    else if (last?.created) last.created.push(event);
    else items.push({ created: [event] });
  }
  return items;
}

/** A real calendar date written `YYYY-MM-DD`, as a due date is sent. */
export function isDateText(text: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return false;
  const parsed = new Date(`${text}T00:00:00Z`);
  return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === text;
}

/** The start form's question after a `process_already_active` refusal. `name` is null when unknown. */
export function replaceQuestion(kind: ProcessKind | (string & {}), name: string | null): string {
  const what = kind === "onboarding" || kind === "offboarding" ? kind : "process";
  return `There is already an active ${what} for ${name ?? "this person"}. Replace it?`;
}

/** The sentence when the create-defaults request found every default already there. */
export const DEFAULTS_NOTHING_CREATED = "These templates already exist: nothing was created.";

/**
 * What the create-defaults answer left alone, in a sentence: "" when nothing was skipped, the plain
 * "already exist" line when nothing was created, else which kind the company already had.
 */
export function defaultsSkippedText(receipt: Pick<DefaultTemplatesReceipt, "created" | "skipped">): string {
  if (receipt.skipped.length === 0) return "";
  if (receipt.created.length === 0) return DEFAULTS_NOTHING_CREATED;
  // Something was created, so at most one of the two kinds was skipped.
  return `The company already had an active ${processKindLabel(receipt.skipped[0].kind).toLowerCase()} template, so it was left as it is.`;
}

/** Most urgent first; any kind not listed follows in the order it first appears. */
const REMINDER_KINDS: [kind: string, label: string][] = [
  ["task_overdue", "Overdue"],
  ["task_due", "Due today"],
  ["task_due_soon", "Due soon"],
  ["task_assigned", "New tasks"],
  ["workflow_review", "Requests to review"],
  ["training_review", "Training to review"],
  ["interview_upcoming", "Upcoming interviews"],
];
export interface ReminderGroup<T> { kind: string; label: string; count: number; items: T[] }

/** A reminder row's kind in words, for a row without its own line (the older kinds): never the code. */
export function reminderLabel(kind: string): string {
  return REMINDER_KINDS.find(([known]) => known === kind)?.[1] ?? "Reminder";
}

/** Home reminder rows grouped by kind, with a count each. Rows keep their order inside a group. */
export function groupReminders<T extends { kind: string }>(items: readonly T[]): ReminderGroup<T>[] {
  const groups = new Map<string, ReminderGroup<T>>();
  for (const item of items) {
    const known = REMINDER_KINDS.find(([kind]) => kind === item.kind);
    const group = groups.get(item.kind) ?? { kind: item.kind, label: known ? known[1] : "Other reminders", count: 0, items: [] };
    group.count += 1;
    group.items.push(item);
    groups.set(item.kind, group);
  }
  const rank = (kind: string) => {
    const index = REMINDER_KINDS.findIndex(([known]) => known === kind);
    return index === -1 ? REMINDER_KINDS.length : index;
  };
  return [...groups.values()].sort((a, b) => rank(a.kind) - rank(b.kind));
}

/** The task reminder kinds (0635): each carries `subject` and `due_on`, and its `resource_id` is the step. */
const TASK_REMINDERS: ReadonlySet<string> = new Set(["task_assigned", "task_due_soon", "task_due", "task_overdue"]);
/** More task reminders than this are shown in groups with a count. */
const GROUP_AFTER = 3;

/** A Home notification row (`GET /records/notifications`). The earlier kinds have no `subject` or `due_on`. */
export interface HomeReminder {
  id: string;
  kind: string;
  resource_id: string;
  read_at: string | null;
  subject?: string | null;
  due_on?: string | null;
}
/** The open steps My tasks holds on the page: `complete` when it read every page. */
export interface OpenTasks { ids: ReadonlySet<string>; complete: boolean }
export interface ReminderLayout<T> {
  /** Task reminders whose step is still open (or may be), grouped by kind. */
  groups: ReminderGroup<T>[];
  /** Show the groups with their headings: more than a few reminders. */
  grouped: boolean;
  /** Task reminders whose step is no longer in My tasks: history, never an action. */
  done: T[];
  /** Every other row, shown as before. */
  other: T[];
  /** Every row in screen order: the groups, Done, then the others. */
  order: T[];
}

/**
 * Home's reminder rows. A task reminder is done only when My tasks read every page and does not
 * hold its step; while My tasks is loading, failed or partly read it stays actionable. A task
 * reminder without its line is kept with the other kinds (shown as before).
 */
export function reminderLayout<T extends HomeReminder>(items: readonly T[], open: OpenTasks | null): ReminderLayout<T> {
  const task = items.filter((item) => TASK_REMINDERS.has(item.kind) && item.subject);
  const finished = (item: T) => open !== null && open.complete && !open.ids.has(item.resource_id);
  const live = task.filter((item) => !finished(item));
  const done = task.filter(finished);
  const other = items.filter((item) => !task.includes(item));
  const groups = groupReminders(live);
  return {
    groups, grouped: live.length > GROUP_AFTER, done, other,
    order: [...groups.flatMap((group) => group.items), ...done, ...other],
  };
}

/** When a reminder's step is due, from `today`; null without a date. */
export function reminderDue(reminder: { due_on?: string | null }, today: string): string | null {
  return reminder.due_on ? dueText({ due_on: reminder.due_on, status: "todo" }, today) : null;
}

/** Progress of Mark all read. */
export function markingText(done: number, total: number): string {
  return `Marking ${done} of ${total}\u2026`;
}

/** Rehire cancels an offboarding still in progress: the form asks first when there is one. */
export function hasActiveOffboarding(processes: readonly { kind: string; status: string }[]): boolean {
  return processes.some((item) => item.kind === "offboarding" && item.status === "active");
}
export const REHIRE_QUESTION =
  "Rehiring cancels the open offboarding and skips its remaining steps (for example final pay checks). Continue?";

const ANCHOR_ROWS = [["start_date", "Start date"], ["last_day", "Last day"], ["probation_end", "Probation end"]] as const;
/** The dates a person's processes count from, those that are set, in words. */
export function anchorDateRows(dates: AnchorDates | null | undefined): [label: string, value: string][] {
  return ANCHOR_ROWS.flatMap(([key, label]) => {
    const value = dates?.[key];
    return value ? [[label, dateText(value)] as [string, string]] : [];
  });
}

/** HR's notice when no template of the kind matches the person. */
export function noTemplateText(kind: "onboarding" | "offboarding"): string {
  return `No ${kind} template matches this person.`;
}
