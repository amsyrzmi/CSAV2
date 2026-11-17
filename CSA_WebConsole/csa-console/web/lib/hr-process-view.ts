import { dateText } from "./hr-people";
import type { Anchor, AutoCheck, OwnerRole, ProcessKind, ProcessStatus, TaskStatus } from "./hr-process-api";

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
