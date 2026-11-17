import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import ts from "typescript";

function moduleUrl(source) {
  const code = ts.transpileModule(source, { compilerOptions: {
    target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022,
  } }).outputText;
  return `data:text/javascript;base64,${Buffer.from(code).toString("base64")}`;
}
const read = (name) => readFile(new URL(name, import.meta.url), "utf8");
const peopleUrl = moduleUrl(await read("../lib/hr-people.ts"));
const {
  dueText, processToday, statusLabel, ownerRoleLabel, offsetSentence, progressText, groupReminders, autoCheckText, reminderLabel,
  processKindLabel, eventText, eventLines, groupHistory, isDateText, replaceQuestion,
  reminderLayout, reminderDue, markingText, hasActiveOffboarding, anchorDateRows, noTemplateText, REHIRE_QUESTION,
} = await import(
  moduleUrl((await read("../lib/hr-process-view.ts")).replace('"./hr-people"', JSON.stringify(peopleUrl))));

const TODAY = "2026-11-17";
const open = (due_on, status = "todo") => ({ due_on, status });

test("dueText counts calendar days from today", () => {
  assert.equal(dueText(open("2026-11-17"), TODAY), "Due today");
  assert.equal(dueText(open("2026-11-18"), TODAY), "Due tomorrow");
  assert.equal(dueText(open("2026-11-19"), TODAY), "Due in 2 days");
  assert.equal(dueText(open("2026-12-17"), TODAY), "Due in 30 days");
  assert.equal(dueText(open("2026-11-16"), TODAY), "Overdue by 1 day");
  assert.equal(dueText(open("2026-11-14"), TODAY), "Overdue by 3 days");
});

test("dueText crosses month and year ends and ignores the time of day", () => {
  assert.equal(dueText(open("2027-01-01"), "2026-12-31"), "Due tomorrow");
  assert.equal(dueText(open("2026-12-31"), "2027-01-01"), "Overdue by 1 day");
  assert.equal(dueText(open("2028-03-01"), "2028-02-28"), "Due in 2 days");
});

test("dueText says there is no date for a null due date", () => {
  assert.equal(dueText(open(null), TODAY), "No date yet");
  assert.equal(dueText({ due_on: null, status: "done" }, TODAY), "No date yet");
});

test("dueText never calls a finished step overdue", () => {
  assert.equal(dueText(open("2026-11-10", "done"), TODAY), "Due on 10 Nov 2026");
  assert.equal(dueText(open("2026-11-25", "skipped"), TODAY), "Due on 25 Nov 2026");
  assert.equal(dueText(open("2026-11-10", "awaiting_approval"), TODAY), "Overdue by 7 days");
});

test("processToday is the Kuala Lumpur calendar day, not the UTC one", () => {
  // KL is UTC+8: midnight there is 16:00 UTC the day before.
  assert.equal(processToday(new Date("2026-11-16T15:59:59Z")), "2026-11-16");
  assert.equal(processToday(new Date("2026-11-16T16:00:00Z")), "2026-11-17");
  assert.equal(processToday(new Date("2026-11-16T23:30:00Z")), "2026-11-17");
  assert.equal(processToday(new Date("2026-12-31T16:00:00Z")), "2027-01-01");
  assert.equal(processToday(new Date("2028-02-28T16:00:00Z")), "2028-02-29");
  assert.match(processToday(), /^\d{4}-\d{2}-\d{2}$/);
});

test("a step due on the KL day is due today for a viewer whose UTC day is still yesterday", () => {
  const now = new Date("2026-11-16T23:30:00Z");
  assert.equal(dueText({ due_on: "2026-11-17", status: "todo" }, processToday(now)), "Due today");
  assert.equal(dueText({ due_on: "2026-11-16", status: "todo" }, processToday(now)), "Overdue by 1 day");
});

test("statusLabel names every task and process status and never a raw code", () => {
  assert.deepEqual(["todo", "awaiting_approval", "done", "rejected", "skipped"].map(statusLabel),
    ["To do", "Waiting for approval", "Done", "Sent back", "Skipped"]);
  assert.deepEqual(["active", "completed", "cancelled"].map(statusLabel), ["In progress", "Completed", "Cancelled"]);
  assert.equal(statusLabel("something_new"), "Unknown");
  assert.equal(statusLabel("__proto__"), "Unknown");
});

test("ownerRoleLabel names each owner role", () => {
  assert.deepEqual(["hr", "manager", "employee", "person"].map(ownerRoleLabel),
    ["HR team", "Manager", "The employee", "A named person"]);
  assert.equal(ownerRoleLabel("constructor"), "Unknown");
});

test("offsetSentence reads zero, positive and negative offsets for each anchor", () => {
  assert.equal(offsetSentence("start_date", 0), "On the start date");
  assert.equal(offsetSentence("last_day", 0), "On the last day");
  assert.equal(offsetSentence("probation_end", 0), "On the probation end date");
  assert.equal(offsetSentence("start_date", 5), "5 days after the start date");
  assert.equal(offsetSentence("last_day", 2), "2 days after the last day");
  assert.equal(offsetSentence("probation_end", 14), "14 days after the probation end date");
  assert.equal(offsetSentence("start_date", 1), "1 day after the start date");
  assert.equal(offsetSentence("last_day", -2), "2 days before the last day");
  assert.equal(offsetSentence("start_date", -1), "1 day before the start date");
  assert.equal(offsetSentence("probation_end", -7), "7 days before the probation end date");
  assert.equal(offsetSentence("start_date", 365), "365 days after the start date");
  assert.equal(offsetSentence("start_date", -365), "365 days before the start date");
});

test("progressText counts steps, with the required wording and singular forms", () => {
  assert.equal(progressText(4, 7, true), "4 of 7 required steps");
  assert.equal(progressText(4, 7), "4 of 7 steps");
  assert.equal(progressText(0, 1), "0 of 1 step");
  assert.equal(progressText(1, 1, true), "1 of 1 required step");
  assert.equal(progressText(0, 0), "No steps");
  assert.equal(progressText(0, 0, true), "No required steps");
});

test("groupReminders groups by kind with counts, most urgent first, keeping row order", () => {
  const row = (id, kind) => ({ id, kind, subject: `S ${id}` });
  const rows = [row("a", "task_assigned"), row("b", "task_due"), row("c", "task_overdue"),
    row("d", "task_assigned"), row("e", "workflow_review"), row("f", "task_overdue"), row("g", "task_assigned")];
  const groups = groupReminders(rows);
  assert.deepEqual(groups.map((group) => [group.kind, group.label, group.count, group.items.map((item) => item.id)]), [
    ["task_overdue", "Overdue", 2, ["c", "f"]],
    ["task_due", "Due today", 1, ["b"]],
    ["task_assigned", "New tasks", 3, ["a", "d", "g"]],
    ["workflow_review", "Requests to review", 1, ["e"]],
  ]);
  assert.deepEqual(groupReminders([]), []);
});

test("groupReminders keeps an unknown kind under a generic label after the known ones", () => {
  const groups = groupReminders([{ id: "x", kind: "later_kind" }, { id: "y", kind: "task_due_soon" }]);
  assert.deepEqual(groups.map((group) => [group.kind, group.label]), [
    ["task_due_soon", "Due soon"], ["later_kind", "Other reminders"],
  ]);
});

test("reminderLabel names a row's kind in words and never prints the code", () => {
  assert.equal(reminderLabel("workflow_review"), "Requests to review");
  assert.equal(reminderLabel("training_review"), "Training to review");
  assert.equal(reminderLabel("interview_upcoming"), "Upcoming interviews");
  assert.equal(reminderLabel("task_overdue"), "Overdue");
  assert.equal(reminderLabel("later_kind"), "Reminder");
});

test("autoCheckText says what the automatic check looks for, to the assignee or about the person", () => {
  assert.equal(autoCheckText("personal_details_complete", true), "Checked automatically: your personal details are filled in.");
  assert.equal(autoCheckText("personal_details_complete", false),
    "Checked automatically: the person's personal details are filled in.");
  assert.equal(autoCheckText("account_linked", true), "Checked automatically: your console account is linked.");
  assert.equal(autoCheckText("account_linked", false), "Checked automatically: the person's console account is linked.");
  assert.equal(autoCheckText("document_uploaded:contract", true), "Checked automatically: a contract is uploaded.");
  assert.equal(autoCheckText("document_uploaded:identity", false), "Checked automatically: an identity document is uploaded.");
  assert.equal(autoCheckText("document_uploaded:certificate", true), "Checked automatically: a certificate is uploaded.");
  assert.equal(autoCheckText("document_uploaded:other", true), "Checked automatically: a document is uploaded.");
  assert.equal(autoCheckText("access_removed", true), "Done by the system when your access is removed.");
  assert.equal(autoCheckText("access_removed", false), "Done by the system when the person's access is removed.");
  // A check a later release adds still reads as a sentence, never the code.
  assert.equal(autoCheckText("later_check", true), "Checked automatically.");
});

test("processKindLabel names a process without a template name", () => {
  assert.equal(processKindLabel("onboarding"), "Onboarding");
  assert.equal(processKindLabel("offboarding"), "Offboarding");
  assert.equal(processKindLabel("custom"), "Custom process");
  assert.equal(processKindLabel("later_kind"), "Process");
});

test("eventText says what a history event did; an action it does not know reads generically", () => {
  assert.equal(eventText("process.started"), "Process started");
  assert.equal(eventText("process.cancelled"), "Process cancelled");
  assert.equal(eventText("process.completed"), "Process completed");
  assert.equal(eventText("task.created"), "Step added");
  assert.equal(eventText("task.completed"), "Step marked done");
  assert.equal(eventText("task.skipped"), "Step skipped");
  assert.equal(eventText("task.reassigned"), "Step reassigned");
  assert.equal(eventText("task.date_changed"), "Due date moved with its date");
  assert.equal(eventText("task.rescheduled"), "Step rescheduled");
  assert.equal(eventText("task.approved"), "Step approved");
  assert.equal(eventText("task.sent_back"), "Step sent back");
  assert.equal(eventText("task.noted"), "Note changed");
  assert.equal(eventText("task.escalated"), "Something changed");
  assert.equal(eventText("toString"), "Something changed");
});

const LINA = "55555555-5555-4555-8555-000000000003";
const FARID = "55555555-5555-4555-8555-000000000002";
const GONE = "55555555-5555-4555-8555-000000000099";
const NAMES = { [LINA]: "Lina Wong", [FARID]: "Farid Ismail" };
const nameOf = (id) => NAMES[id] ?? null;
const lines = (detail) => eventLines(detail, nameOf);

test("eventLines says every reason code the migrations write as a sentence, never the code", () => {
  const said = {
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
  for (const [reason, sentence] of Object.entries(said)) assert.deepEqual(lines({ reason }), [sentence], reason);
  // A reason a later release adds reads generically.
  assert.deepEqual(lines({ reason: "later_reason" }), ["Updated"]);
  assert.deepEqual(lines({ reason: 7 }), ["Updated"]);
});

test("eventLines says why a step went to the HR team", () => {
  assert.deepEqual(lines({ owner_role: "manager", assignee_employee_id: null, due_on: "2026-11-20", fallback: "hr_pool",
    reason: "no_manager" }), ["Assigned to the HR team (the person has no manager)", "Due on 20 Nov 2026", "Owner: Manager"]);
  assert.deepEqual(lines({ fallback: "hr_pool", reason: "manager_inactive" }),
    ["Assigned to the HR team (their manager is not active)"]);
  assert.deepEqual(lines({ fallback: "hr_pool", reason: "manager_unlinked" }),
    ["Assigned to the HR team (their manager has no console account)"]);
  assert.deepEqual(lines({ fallback: "hr_pool", reason: "person_inactive" }),
    ["Assigned to the HR team (the person is not active)"]);
  assert.deepEqual(lines({ from: LINA, to: null, fallback: "hr_pool", reason: "person_left" }),
    ["Assigned to the HR team (the person left)", "Was Lina Wong"]);
  assert.deepEqual(lines({ fallback: "hr_pool", reason: "later" }), ["Assigned to the HR team"]);
});

test("eventLines shows HR's words, people by name and dates, and hides ids", () => {
  assert.deepEqual(lines({ reason: "skipped_by_hr", remark: "Done by the agency.\nNo action." }),
    ["Skipped by HR", "Note: Done by the agency.\nNo action."]);
  assert.deepEqual(lines({ remark: "Please sign the last page too." }), ["Note: Please sign the last page too."]);
  assert.deepEqual(lines({ from: LINA, to: FARID, reason: "reassigned_by_hr" }),
    ["Reassigned by HR", "Assigned to Farid Ismail", "Was Lina Wong"]);
  assert.deepEqual(lines({ from: null, to: LINA }), ["Assigned to Lina Wong", "Was the HR team"]);
  // Someone the page cannot name is never shown as an id.
  assert.deepEqual(lines({ from: GONE, to: GONE }), ["Assigned to someone else"]);
  assert.deepEqual(lines({ owner_role: "employee", assignee_employee_id: LINA, due_on: null }),
    ["Assigned to Lina Wong", "Owner: The employee"]);
  assert.deepEqual(lines({ owner_role: "hr", assignee_employee_id: null }), ["Assigned to the HR team", "Owner: HR team"]);
  assert.deepEqual(lines({ old: "2026-11-19", new: "2026-11-27" }), ["Due date changed from 19 Nov 2026 to 27 Nov 2026"]);
  assert.deepEqual(lines({ old: null, new: "2026-11-27" }), ["Due date changed from no date to 27 Nov 2026"]);
  assert.deepEqual(lines({ anchor: "last_day", old: "2026-11-19", new: "2026-11-18", clamped: true }), [
    "Due date changed from 19 Nov 2026 to 18 Nov 2026", "Counted from the last day", "Kept on or before the last day"]);
  assert.deepEqual(lines({ kind: "onboarding", template_id: LINA, template_version: 2 }), ["Onboarding", "Template version 2"]);
});

test("eventLines says what a completion or an approval was", () => {
  assert.deepEqual(lines({ awaiting_approval: false, on_behalf: false }), []);
  assert.deepEqual(lines({ awaiting_approval: true, on_behalf: true, auto_check: "account_linked" }), [
    "Checked automatically: the person's console account is linked.", "Waiting for approval",
    "Done by HR for the person it is assigned to"]);
  assert.deepEqual(lines({ approver: "manager" }), ["Approved by the manager"]);
  assert.deepEqual(lines({ approver: "hr", approved_without_second_person: true }),
    ["Approved by HR", "Approved without a second person: no one else in HR could approve it"]);
  assert.deepEqual(lines({ reason: "person_left", remark: "Not needed.", sent_back: true }),
    ["The person left", "Note: Not needed.", "Sent back after the person left, so it was skipped"]);
});

test("eventLines keeps a key it does not know as plain text, but never an id", () => {
  assert.deepEqual(lines({ level: 2, urgent: true, quiet: false, nothing: null, label: "Second reminder" }),
    ["level: 2", "urgent: yes", "quiet: no", "nothing: none", "label: Second reminder"]);
  assert.deepEqual(lines({ owner_hint: LINA, list: ["x", 2], nested: { who: FARID }, plain: { a: 1 } }),
    ['list: ["x",2]', 'plain: {"a":1}']);
  assert.deepEqual(lines({ kind: "later_kind", owner_role: "later_role", approver: "later", anchor: "later" }), []);
});

test("groupHistory folds each run of task.created into one item", () => {
  const event = (id, action) => ({ event_id: id, action });
  const items = groupHistory([event("1", "process.started"), event("2", "task.created"), event("3", "task.created"),
    event("4", "task.sent_back"), event("5", "task.created")]);
  assert.deepEqual(items.map((item) => item.created ? item.created.map((e) => e.event_id) : item.event.event_id),
    ["1", ["2", "3"], "4", ["5"]]);
  assert.deepEqual(groupHistory([]), []);
});

test("isDateText takes a real calendar date as YYYY-MM-DD only", () => {
  assert.equal(isDateText("2026-11-20"), true);
  assert.equal(isDateText("2028-02-29"), true);
  for (const bad of ["", " ", "2026-02-30", "2026-13-01", "2026-1-5", "20-11-2026", "2026-11-20T00:00", "tomorrow"])
    assert.equal(isDateText(bad), false, bad);
});

test("replaceQuestion asks before replacing an active onboarding or offboarding", () => {
  assert.equal(replaceQuestion("onboarding", "Lina Wong"), "There is already an active onboarding for Lina Wong. Replace it?");
  assert.equal(replaceQuestion("offboarding", "Hana Yusof"), "There is already an active offboarding for Hana Yusof. Replace it?");
  assert.equal(replaceQuestion("custom", null), "There is already an active process for this person. Replace it?");
});

const note = (id, kind, resource_id, extra = {}) => ({
  id, kind, resource_id, read_at: null, subject: `Line ${id}`, due_on: "2026-11-18", ...extra,
});

test("reminderLayout groups the open task reminders, moves finished ones to Done and keeps other kinds apart", () => {
  const rows = [
    note("a", "task_assigned", "t1"), note("b", "workflow_review", "w1", { subject: null, due_on: null }),
    note("c", "task_overdue", "t2"), note("d", "task_due", "gone"), note("e", "task_assigned", "t3"),
    note("f", "task_due_soon", "t1"),
  ];
  const layout = reminderLayout(rows, { ids: new Set(["t1", "t2", "t3"]), complete: true });
  assert.deepEqual(layout.groups.map((group) => [group.kind, group.count, group.items.map((item) => item.id)]), [
    ["task_overdue", 1, ["c"]], ["task_due_soon", 1, ["f"]], ["task_assigned", 2, ["a", "e"]],
  ]);
  assert.equal(layout.grouped, true);
  assert.deepEqual(layout.done.map((item) => item.id), ["d"]);
  assert.deepEqual(layout.other.map((item) => item.id), ["b"]);
  // Screen order: the groups, then Done, then the other kinds.
  assert.deepEqual(layout.order.map((item) => item.id), ["c", "f", "a", "e", "d", "b"]);
});

test("reminderLayout lists a few reminders without groups", () => {
  const layout = reminderLayout([note("a", "task_due", "t1"), note("b", "task_assigned", "t2")],
    { ids: new Set(["t1", "t2"]), complete: true });
  assert.equal(layout.grouped, false);
  assert.deepEqual(layout.order.map((item) => item.id), ["a", "b"]);
});

test("reminderLayout never calls a reminder done while My tasks is unknown or only partly loaded", () => {
  const rows = [note("a", "task_due", "elsewhere")];
  assert.deepEqual(reminderLayout(rows, null).done, []);
  assert.deepEqual(reminderLayout(rows, { ids: new Set(), complete: false }).done, []);
  assert.deepEqual(reminderLayout(rows, { ids: new Set(), complete: true }).done.map((item) => item.id), ["a"]);
});

test("reminderLayout keeps a task reminder without its line with the other kinds", () => {
  const layout = reminderLayout([note("a", "task_due", "t1", { subject: null })], { ids: new Set(["t1"]), complete: true });
  assert.deepEqual(layout.groups, []);
  assert.deepEqual(layout.other.map((item) => item.id), ["a"]);
});

test("reminderDue says when the step is due, or nothing without a date", () => {
  assert.equal(reminderDue({ due_on: "2026-11-17" }, TODAY), "Due today");
  assert.equal(reminderDue({ due_on: "2026-11-15" }, TODAY), "Overdue by 2 days");
  assert.equal(reminderDue({ due_on: null }, TODAY), null);
  assert.equal(reminderDue({}, TODAY), null);
});

test("markingText counts the reminders marked so far", () => {
  assert.equal(markingText(3, 12), "Marking 3 of 12\u2026");
});

test("hasActiveOffboarding is true only for an offboarding still in progress", () => {
  const row = (kind, status) => ({ kind, status });
  assert.equal(hasActiveOffboarding([row("offboarding", "active")]), true);
  assert.equal(hasActiveOffboarding([row("offboarding", "completed"), row("onboarding", "active")]), false);
  assert.equal(hasActiveOffboarding([row("offboarding", "cancelled"), row("custom", "active")]), false);
  assert.equal(hasActiveOffboarding([]), false);
});

test("anchorDateRows lists the set dates in words", () => {
  assert.deepEqual(anchorDateRows({ start_date: "2026-11-02", last_day: null, probation_end: "2027-02-02" }), [
    ["Start date", "2 Nov 2026"], ["Probation end", "2 Feb 2027"],
  ]);
  assert.deepEqual(anchorDateRows(null), []);
  assert.deepEqual(anchorDateRows({}), []);
});

test("noTemplateText names the kind", () => {
  assert.equal(noTemplateText("onboarding"), "No onboarding template matches this person.");
  assert.equal(noTemplateText("offboarding"), "No offboarding template matches this person.");
});

test("REHIRE_QUESTION says what rehiring does to the open offboarding", () => {
  assert.equal(REHIRE_QUESTION,
    "Rehiring cancels the open offboarding and skips its remaining steps (for example final pay checks). Continue?");
});
