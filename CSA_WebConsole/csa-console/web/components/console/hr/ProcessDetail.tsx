"use client";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { dateText } from "@/lib/hr-people";
import { shortWhen } from "@/lib/hr-pickers";
import { processClient, type ProcessDetail as Detail, type ProcessTask, type TaskStatus } from "@/lib/hr-process-api";
import { processErrorText, processLoadError, processSaveError, type ProcessAction } from "@/lib/hr-process-copy";
import {
  autoCheckText, dueText, eventDetailLines, eventText, isDateText, ownerRoleLabel, processKindLabel, processToday,
  progressText, statusLabel,
} from "@/lib/hr-process-view";
import Dialog from "../fs/Dialog";
import StatusLabel, { type StatusTone } from "../fs/StatusLabel";
import { action as primary, field, secondary, Notice, Panel } from "../ui";
import { PersonSelect, usePeopleOptions, type Options } from "./Pickers";
import { processTone } from "./ProcessList";
import { takeStartArrival } from "./StartProcessForm";
import { isStale, NOTICE_FOCUS, TextForm } from "./TaskActions";
import { useHrRunner } from "./useHrRunner";
import { useReturnFocus } from "./useReturnFocus";

const REASON_MAX = 500;
const OPEN: ReadonlySet<TaskStatus> = new Set<TaskStatus>(["todo", "awaiting_approval", "rejected"]);
const TONES: Record<TaskStatus, StatusTone> = {
  todo: "neutral", awaiting_approval: "info", done: "success", rejected: "attention", skipped: "neutral",
};
const taskTone = (status: TaskStatus) => (Object.hasOwn(TONES, status) ? TONES[status] : "neutral");
/** Refusals that mean the page is out of date: show the sentence and read the process again. */
const STALE: ReadonlySet<string> = new Set(["task_changed", "task_closed", "process_closed", "resource_unavailable"]);
const ANCHORS = [["start_date", "Start date"], ["last_day", "Last day"], ["probation_end", "Probation end"]] as const;
const card = "rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3";

type Load = { detail: Detail } | { failed: string } | null;
/** One open dialog and the button that opened it. */
type Ask = { opener: HTMLElement } & (
  | { kind: "cancel" | "close" }
  | { kind: "skip" | "reschedule" | "reassign"; task: ProcessTask & { version: number } });

/**
 * One process for the HR team: the header, the steps as a timeline (who owns each, its date and
 * status, who did it and when, the notes), a collapsed History of its events, and the HR actions:
 * cancel, close anyway, and per step reschedule, reassign and skip, each through a dialog. A stale
 * step or process shows the sentence and reads the process again. Read-only members see no action.
 */
export function ProcessDetail({ tenant, processId, readonly, userId }: {
  tenant: string; processId: string; readonly: boolean; userId: string;
}) {
  // Memoised: the client holds the Idempotency-Key of an uncertain write until it succeeds.
  const client = useMemo(() => processClient(tenant), [tenant]);
  const people = usePeopleOptions(tenant);
  const runner = useHrRunner();
  const [load, setLoad] = useState<Load>(null);
  const [reads, setReads] = useState(0);
  const [reloading, setReloading] = useState(false);
  const [outcome, setOutcome] = useState<{ message?: string; error?: string } | null>(null);
  const [ask, setAsk] = useState<Ask | null>(null);
  const [dialogError, setDialogError] = useState("");
  const panel = useRef<HTMLDivElement>(null);
  const notices = useRef<HTMLDivElement>(null);
  const returnFocus = useReturnFocus(!reloading && !runner.busy, notices, panel);
  useEffect(() => {
    let active = true;
    client.process(processId)
      .then(detail => { if (active) setLoad({ detail }); })
      .catch(cause => { if (active) setLoad({ failed: processLoadError(cause) }); })
      .finally(() => { if (active) setReloading(false); });
    return () => { active = false; };
  }, [client, processId, reads]);
  // Right after a start, focus moves to this page's heading (the start dialog it came from is gone).
  const loaded = load !== null && "detail" in load;
  useEffect(() => {
    if (loaded && takeStartArrival()) returnFocus(null);
  }, [loaded, returnFocus]);
  const reload = useCallback(() => { setReloading(true); setReads(count => count + 1); }, []);

  const write = (opener: HTMLElement, button: ProcessAction, saved: string, call: () => Promise<unknown>) =>
    runner.run(async () => {
      setOutcome(null);
      try {
        await call();
        setAsk(null);
        setOutcome({ message: saved });
        returnFocus(opener);
        reload();
      } catch (cause) {
        const text = processSaveError(cause, button);
        if (isStale(cause, STALE)) {
          setAsk(null);
          setOutcome({ error: text });
          returnFocus(opener);
          reload();
        } else setDialogError(text);
      }
    });
  const show = (next: Ask) => { setDialogError(""); setAsk(next); };
  const close = () => { if (!runner.busy) setAsk(null); };

  if (!load) return <Notice>Loading the process…</Notice>;
  if ("failed" in load) return <div ref={panel}><Panel title="Process">
    <div className="space-y-3">
      <Notice error>{load.failed}</Notice>
      <button type="button" className={secondary} onClick={reload}>Try again</button>
    </div>
  </Panel></div>;
  const { process, tasks, events } = load.detail;
  const active = process.status === "active";
  const busy = runner.busy || reloading;
  const today = processToday();
  const titleOf = (taskId: string | null) => tasks.find(task => task.task_id === taskId)?.title_en ?? null;
  const kindWord = processKindLabel(process.kind).toLowerCase();

  return <div ref={panel} data-testid="hr-process-detail" aria-busy={busy}>
    <Panel title={`${processKindLabel(process.kind)} for ${process.employee_name}`}>
      <div className="space-y-5">
        {(outcome?.message || outcome?.error) && <div ref={notices} tabIndex={-1} data-testid="hr-process-outcome"
          className={`space-y-3 ${NOTICE_FOCUS}`}>
          {outcome.message && <Notice testId="hr-process-notice">{outcome.message}</Notice>}
          {outcome.error && <Notice error testId="hr-process-error">{outcome.error}</Notice>}
        </div>}
        <dl data-testid="hr-process-header" className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-2">
          <Fact label="Template">{process.name
            ? `${process.name}${process.template_version ? `, version ${process.template_version}` : ""}` : "No template"}</Fact>
          <Fact label="Status"><StatusLabel tone={processTone(process.status)}>{statusLabel(process.status)}</StatusLabel></Fact>
          {typeof process.tasks_total === "number" && typeof process.tasks_done === "number"
            && <Fact label="Progress">{progressText(process.tasks_done, process.tasks_total)}</Fact>}
          {process.started_at && <Fact label="Started">{shortWhen(process.started_at)}</Fact>}
          {process.closed_at && <Fact label="Closed">{shortWhen(process.closed_at)}</Fact>}
          {ANCHORS.map(([key, label]) => {
            const value = process.anchor_dates?.[key];
            return value ? <Fact key={key} label={label}>{dateText(value)}</Fact> : null;
          })}
        </dl>
        {!readonly && active && <div className="flex flex-wrap gap-2">
          <button type="button" className={secondary} disabled={busy}
            onClick={event => show({ kind: "cancel", opener: event.currentTarget })}>Cancel process</button>
          <button type="button" className={secondary} disabled={busy}
            onClick={event => show({ kind: "close", opener: event.currentTarget })}>Close anyway</button>
        </div>}
        <section aria-labelledby={`hr-process-steps-${process.process_id}`}>
          <h3 id={`hr-process-steps-${process.process_id}`} className="mb-3 font-semibold">Steps</h3>
          <ol data-testid="hr-process-steps" className="space-y-3">{tasks.map(task => {
            const titleId = `hr-process-step-title-${task.task_id}`;
            const version = task.version;
            const actionable = !readonly && active && OPEN.has(task.status) && typeof version === "number";
            const open = (kind: "skip" | "reschedule" | "reassign") => (event: React.MouseEvent<HTMLButtonElement>) =>
              typeof version === "number" && show({ kind, task: { ...task, version }, opener: event.currentTarget });
            return <li key={task.task_id} data-testid={`hr-process-step-${task.task_id}`} className={card}>
              <StepText task={task} titleId={titleId} today={today} />
              {actionable && <div className="mt-3 flex flex-wrap gap-2">
                {(["reschedule", "reassign", "skip"] as const).map(kind => <button key={kind} type="button" className={secondary}
                  disabled={busy} aria-describedby={titleId} onClick={open(kind)}>
                  {kind === "reschedule" ? "Reschedule" : kind === "reassign" ? "Reassign" : "Skip"}</button>)}
              </div>}
            </li>;
          })}</ol>
        </section>
        {Array.isArray(events) && <details data-testid="hr-process-history" className="text-sm">
          <summary className={`cursor-pointer font-semibold ${NOTICE_FOCUS}`}>History</summary>
          {events.length === 0 ? <p className="mt-2 text-ink-500">Nothing recorded yet.</p>
            : <ol className="mt-3 space-y-3">{events.map(event => {
              const step = titleOf(event.task_id);
              return <li key={event.event_id} data-testid="hr-process-event"
                className="border-l-2 border-[color:var(--fs-border)] pl-3">
                <p className="font-medium [overflow-wrap:anywhere]">{eventText(event.action)}{step ? `: ${step}` : ""}</p>
                <p className="text-[color:var(--fs-text-secondary)]">{shortWhen(event.at)} · {event.actor === null
                  ? "By the system" : event.actor === userId ? "By you" : "By someone in your team"}</p>
                {eventDetailLines(event.detail).map((line, index) =>
                  <p key={index} className="whitespace-pre-line [overflow-wrap:anywhere]">{line}</p>)}
              </li>;
            })}</ol>}
        </details>}
      </div>
    </Panel>
    {ask && !readonly && <Dialog open onClose={close} testId="hr-process-dialog" title={{
      cancel: "Cancel process", close: "Close anyway", skip: "Skip step", reschedule: "Reschedule step",
      reassign: "Reassign step",
    }[ask.kind]}>
      <div className="space-y-3 text-sm">
        {ask.kind === "cancel" && <>
          <p className="font-medium [overflow-wrap:anywhere]">Cancel the {kindWord} for {process.employee_name}?</p>
          <p>Its open steps are skipped. You cannot undo this.</p>
          {dialogError && <Refusal text={dialogError} />}
          <div className="flex flex-wrap gap-2">
            <button type="button" className={primary} disabled={runner.busy}
              onClick={() => void write(ask.opener, "cancel", "Process cancelled. Its open steps were skipped.",
                () => client.cancelProcess(process.process_id))}>Cancel process</button>
            <button type="button" className={secondary} disabled={runner.busy} onClick={close}>Keep it</button>
          </div>
        </>}
        {ask.kind === "close" && <>
          <p>Its open steps are skipped and the process counts as completed.</p>
          <TextForm label="Note" hint="Why you are closing it. Only the HR team sees this note." initial="" required
            max={REASON_MAX} tooLong={processErrorText("task_reason_invalid")} submit="Close anyway" busy={runner.busy}
            error={dialogError} onCancel={close}
            onSubmit={note => void write(ask.opener, "close", "Process closed. Its open steps were skipped.",
              () => client.closeProcess(process.process_id, note))} />
        </>}
        {(ask.kind === "skip" || ask.kind === "reschedule" || ask.kind === "reassign") && <>
          <p className="font-medium [overflow-wrap:anywhere]">{ask.task.title_en}</p>
          {ask.kind === "skip" && <TextForm label="Reason" hint="Only the HR team sees this reason." initial="" required
            max={REASON_MAX} tooLong={processErrorText("task_reason_invalid")} submit="Skip" busy={runner.busy}
            error={dialogError} onCancel={close}
            onSubmit={reason => void write(ask.opener, "skip", "Step skipped.",
              () => client.taskAction(ask.task.task_id, "skip", { expected_version: ask.task.version, reason }))} />}
          {ask.kind === "reschedule" && <DateForm initial={ask.task.due_on ?? ""} busy={runner.busy} error={dialogError}
            onCancel={close}
            onSubmit={due_on => void write(ask.opener, "reschedule", "New due date saved.",
              () => client.taskAction(ask.task.task_id, "reschedule", { expected_version: ask.task.version, due_on }))} />}
          {ask.kind === "reassign" && <ReassignForm people={people} initial={ask.task.assignee_employee_id ?? ""}
            busy={runner.busy} error={dialogError} onCancel={close}
            onSubmit={assignee_employee_id => void write(ask.opener, "reassign", "Step reassigned.",
              () => client.taskAction(ask.task.task_id, "reassign",
                { expected_version: ask.task.version, assignee_employee_id }))} />}
        </>}
      </div>
    </Dialog>}
  </div>;
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return <div className="flex min-w-0 flex-wrap items-center gap-x-2"><dt className="text-[color:var(--fs-text-secondary)]">{label}</dt>
    <dd className="min-w-0 [overflow-wrap:anywhere]">{children}</dd></div>;
}

/** One step: its title (the id its buttons are described by), owner, assignee, date, status and texts. */
function StepText({ task, titleId, today }: { task: ProcessTask; titleId: string; today: string }) {
  const line = "mt-1 whitespace-pre-line text-sm [overflow-wrap:anywhere]";
  const finished = task.status === "done" || task.status === "awaiting_approval";
  const doneBy = finished && (task.done_by_hr || task.completed_at)
    ? `Done by ${task.done_by_hr ? "HR" : task.assignee_name ?? "the HR team"}${task.completed_at ? ` on ${shortWhen(task.completed_at)}` : ""}`
    : null;
  return <div className="min-w-0">
    <p className="text-xs text-[color:var(--fs-text-secondary)]">Step {task.ordinal}{task.required ? "" : " · optional"}</p>
    <p id={titleId} className="font-medium [overflow-wrap:anywhere]">{task.title_en}</p>
    {task.title_ms && <p lang="ms" className="text-sm text-[color:var(--fs-text-secondary)] [overflow-wrap:anywhere]">{task.title_ms}</p>}
    <dl className="mt-2 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">
      <Fact label="Owner">{ownerRoleLabel(task.owner_role)}</Fact>
      <Fact label="Assigned to">{task.assignee_name ?? "HR team"}</Fact>
      <Fact label="Due"><span className={task.overdue ? "font-medium text-[color:var(--fs-danger)]" : undefined}>
        {dueText(task, today)}</span></Fact>
      <Fact label="Status"><StatusLabel tone={taskTone(task.status)}>{statusLabel(task.status)}</StatusLabel></Fact>
    </dl>
    {task.assignee_overridden && <p className={line}>Overridden by HR</p>}
    {doneBy && <p className={line}>{doneBy}</p>}
    {task.description_en && <p className={line}>{task.description_en}</p>}
    {task.description_ms && <p lang="ms" className={`${line} text-[color:var(--fs-text-secondary)]`}>{task.description_ms}</p>}
    {task.auto_check && <p className={line}>{autoCheckText(task.auto_check, task.mine)}</p>}
    {task.reference_note && <p className={line}>Reference: {task.reference_note}</p>}
    {task.note && <p className={line}>Note: {task.note}</p>}
    {task.sent_back_reason && <p className={`${line} font-medium`}>Sent back: {task.sent_back_reason}</p>}
  </div>;
}

/** A dialog's refusal, which takes focus (the button it came from was off while the write ran). */
function Refusal({ text }: { text: string }) {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { box.current?.focus(); }, [text]);
  return <div ref={box} tabIndex={-1} className={NOTICE_FOCUS}><Notice error>{text}</Notice></div>;
}

function Buttons({ submit, busy, onCancel }: { submit: string; busy: boolean; onCancel: () => void }) {
  return <div className="flex flex-wrap gap-2">
    <button type="submit" className={primary} disabled={busy}>{submit}</button>
    <button type="button" className={secondary} disabled={busy} onClick={onCancel}>Cancel</button>
  </div>;
}

/** A new due date: an empty or impossible date is caught here and never sent. */
function DateForm({ initial, busy, error, onSubmit, onCancel }: {
  initial: string; busy: boolean; error: string; onSubmit: (date: string) => void; onCancel: () => void;
}) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const [wrong, setWrong] = useState(false);
  return <form noValidate className="space-y-3" onSubmit={event => {
    event.preventDefault();
    if (busy) return;
    const value = input.current?.value ?? "";
    if (!isDateText(value)) { setWrong(true); input.current?.focus(); return; }
    setWrong(false);
    onSubmit(value);
  }}>
    <div>
      <label className="block font-medium" htmlFor={`${id}-date`}>New due date</label>
      <input ref={input} id={`${id}-date`} type="date" className={`${field} mt-1`} defaultValue={initial} required
        aria-invalid={wrong} aria-describedby={wrong ? `${id}-wrong` : undefined} onChange={() => setWrong(false)} />
      {wrong && <p id={`${id}-wrong`} className="mt-1 text-[color:var(--fs-danger)]">{processErrorText("task_due_invalid")}</p>}
    </div>
    {error && <Refusal text={error} />}
    <Buttons submit="Reschedule" busy={busy} onCancel={onCancel} />
  </form>;
}

/** Someone else for the step, or the HR team (no one named). */
function ReassignForm({ people, initial, busy, error, onSubmit, onCancel }: {
  people: Options; initial: string; busy: boolean; error: string; onSubmit: (employeeId: string | null) => void;
  onCancel: () => void;
}) {
  return <form className="space-y-3" onSubmit={event => {
    event.preventDefault();
    if (busy) return;
    const value = String(new FormData(event.currentTarget).get("assignee") ?? "").trim();
    onSubmit(value || null);
  }}>
    {/* Remounted once the people have loaded, so the current assignee starts chosen. */}
    <PersonSelect key={String(people.loaded)} people={people} name="assignee" label="Assign to"
      noneLabel="HR team (no one named)" required={false} defaultValue={initial} />
    {error && <Refusal text={error} />}
    <Buttons submit="Reassign" busy={busy} onCancel={onCancel} />
  </form>;
}
