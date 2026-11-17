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
const { dueText, processToday, statusLabel, ownerRoleLabel, offsetSentence, progressText, groupReminders } = await import(
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
