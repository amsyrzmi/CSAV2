"use client";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import type { MyTask, processClient, TaskAction, TaskBodies, TaskReceipt } from "@/lib/hr-process-api";
import { processErrorText, processSaveError, type ProcessAction } from "@/lib/hr-process-copy";
import Dialog from "../fs/Dialog";
import { action as primary, field, secondary, Notice } from "../ui";
import type { HrRunner } from "./useHrRunner";

export type ProcessClient = ReturnType<typeof processClient>;
/**
 * What a task write came to: the list shows `message` or `error`, and reloads when `reload`. `opener` is
 * the row button the person chose: the list gives focus back to it (after the reload, if any) when it
 * is still there, else to the message, so focus never falls to the page body.
 */
export interface TaskOutcome { message?: string; error?: string; reload: boolean; opener?: HTMLElement | null }

const NOTE_MAX = 2000;
/** A message that takes focus shows the focus ring when the keyboard brought it there. */
export const NOTICE_FOCUS = "rounded-[var(--fs-radius-control)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]";
const REASON_MAX = 500;
/** Refusals that mean the list is out of date: show the sentence and read the list again. */
const OUTDATED = new Set(["task_changed", "task_closed", "resource_unavailable", "task_awaiting_approval",
  "task_not_awaiting_approval", "task_not_approver"]);

/** A 404 or 409 refusal whose code is in `codes`: what the viewer sees is out of date. */
export function isStale(failure: unknown, codes: ReadonlySet<string>): boolean {
  if (typeof failure !== "object" || failure === null) return false;
  const { status, code } = failure as { status?: unknown; code?: unknown };
  return (status === 404 || status === 409) && typeof code === "string" && codes.has(code);
}

function savedText(action: TaskAction, receipt: TaskReceipt): string {
  if (action === "complete")
    return receipt.task_status === "awaiting_approval" ? "Marked done. It now waits for approval." : "Marked done.";
  if (action === "approve") return "Approved.";
  if (action === "send-back") return "Sent back with your reason.";
  if (action === "note") return "Note saved.";
  return "Saved.";
}

/** One open dialog: the step, the row button that opened it, what it asks for and the button it stands for. */
type Ask = { task: MyTask; opener: HTMLElement } & (
  | { kind: "complete"; label: "Mark done" | "Mark done for them"; button: ProcessAction }
  | { kind: "note" }
  | { kind: "send-back" });

/**
 * The task writes of one list: every write goes through the shared `runner` (one at a time, so a
 * double click sends one request) with the step's `version`; the client keeps the Idempotency-Key of
 * an uncertain write, so the same button with the same text is safe to choose again. A refusal that
 * means the list is stale closes the dialog and asks for a reload. One dialog serves the whole list.
 * `onOutcome(null)` says a write has started (the list clears the last message).
 */
export function useTaskWrites(client: ProcessClient, runner: HrRunner,
  onOutcome: (outcome: TaskOutcome | null) => void) {
  const [ask, setAsk] = useState<Ask | null>(null);
  const [dialogError, setDialogError] = useState("");
  const { run, busy } = runner;
  const send = useCallback(<A extends TaskAction>(task: MyTask, name: A, body: TaskBodies[A], button: ProcessAction,
    opener: HTMLElement, inDialog: boolean) => run(async () => {
    onOutcome(null);
    try {
      const receipt = await client.taskAction(task.task_id, name, body);
      setAsk(null);
      onOutcome({ message: savedText(name, receipt), reload: true, opener });
    } catch (cause) {
      const text = processSaveError(cause, button);
      if (isStale(cause, OUTDATED)) { setAsk(null); onOutcome({ error: text, reload: true, opener }); }
      else if (inDialog) setDialogError(text);
      else onOutcome({ error: text, reload: false, opener });
    }
  }), [client, run, onOutcome]);
  const show = useCallback((next: Ask) => { setDialogError(""); setAsk(next); }, []);
  const close = useCallback(() => { if (!busy) setAsk(null); }, [busy]);
  return { ask, dialogError, busy, send, show, close };
}
export type TaskWrites = ReturnType<typeof useTaskWrites>;

/**
 * The buttons of one My tasks row; nothing when the viewer has nothing to do on it. Each button is
 * described by the row's title (`describedBy`, an element id), so "Mark done" says which step.
 */
export function TaskActions({ task, writes, disabled, describedBy }: {
  task: MyTask; writes: TaskWrites; disabled: boolean; describedBy: string;
}) {
  const { send, show } = writes;
  const busy = disabled || writes.busy;
  const open = task.status === "todo" || task.status === "rejected";
  const expected = { expected_version: task.version };
  const buttons: { label: string; run: (opener: HTMLElement) => void }[] = [];
  if (task.needs_my_approval && task.status === "awaiting_approval") {
    buttons.push({ label: "Approve", run: opener => void send(task, "approve", expected, "approve", opener, false) });
    buttons.push({ label: "Send back", run: opener => show({ task, opener, kind: "send-back" }) });
  } else if (open && task.auto_check === "access_removed") {
    // The system completes it when the person's access is removed; the hint says so.
  } else if (open && task.auto_check !== null) {
    buttons.push({ label: "Check now", run: opener => void send(task, "complete", expected, "check", opener, false) });
  } else if (open && task.mine) {
    buttons.push({ label: "Mark done",
      run: opener => show({ task, opener, kind: "complete", label: "Mark done", button: "complete" }) });
  } else if (open) {
    const label = task.pool ? "Mark done" : "Mark done for them";
    buttons.push({ label,
      run: opener => show({ task, opener, kind: "complete", label, button: task.pool ? "complete" : "complete-for" }) });
  }
  if (task.mine) buttons.push({ label: "Add note", run: opener => show({ task, opener, kind: "note" }) });
  if (buttons.length === 0) return null;
  return <div className="flex flex-wrap gap-2">
    {buttons.map(item => <button key={item.label} type="button" className={secondary} disabled={busy}
      aria-describedby={describedBy} onClick={event => item.run(event.currentTarget)}>{item.label}</button>)}
  </div>;
}

/**
 * The list's one dialog: Mark done (an optional note), Add note, or Send back (a reason). It is only
 * in the page while open; closing it gives focus back to the button that opened it.
 */
export function TaskDialog({ writes }: { writes: TaskWrites }) {
  const { ask, dialogError, busy, send, close } = writes;
  if (!ask) return null;
  const { task, opener } = ask;
  const expected = task.version;
  return <Dialog open onClose={close} testId="hr-task-dialog"
    title={ask.kind === "complete" ? ask.label : ask.kind === "note" ? "Add note" : "Send back"}>
    <div className="space-y-3 text-sm">
      <p className="font-medium [overflow-wrap:anywhere]">{task.title_en}</p>
      {ask.kind === "complete" && <TextForm label="Note (optional)" initial="" required={false} max={NOTE_MAX}
        tooLong={processErrorText("task_note_invalid")} submit={ask.label} busy={busy} error={dialogError}
        onCancel={close}
        onSubmit={note => void send(task, "complete", note ? { expected_version: expected, note } : { expected_version: expected },
          ask.button, opener, true)} />}
      {ask.kind === "note" && <TextForm label="Note" hint="Your HR team sees this note." initial={task.note ?? ""}
        required={false} max={NOTE_MAX} tooLong={processErrorText("task_note_invalid")} submit="Save note"
        busy={busy} error={dialogError} onCancel={close}
        onSubmit={note => void send(task, "note", { note: note || null }, "note", opener, true)} />}
      {ask.kind === "send-back" && <TextForm label="Reason" hint="The person sees this reason with the step."
        initial="" required max={REASON_MAX} tooLong={processErrorText("task_reason_invalid")} submit="Send back"
        busy={busy} error={dialogError} onCancel={close}
        onSubmit={reason => void send(task, "send-back", { expected_version: expected, reason }, "send-back", opener, true)} />}
    </div>
  </Dialog>;
}

/**
 * A text box with its limit for a dialog: the submit button stays off while the text is required
 * and empty, or longer than `max` (then `tooLong` says why). It submits the trimmed text. A refusal
 * (`error`) takes focus, since the submit button it came from was off while the write ran.
 */
export function TextForm({ label, hint, initial, required, max, tooLong, submit, busy, error, onSubmit, onCancel }: {
  label: string;
  hint?: string;
  initial: string;
  required: boolean;
  max: number;
  tooLong: string;
  submit: string;
  busy: boolean;
  error: string;
  onSubmit: (text: string) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const id = useId();
  const refusal = useRef<HTMLDivElement>(null);
  useEffect(() => { if (error) refusal.current?.focus(); }, [error]);
  const text = value.trim();
  const long = text.length > max;
  const invalid = long || (required && text === "");
  return <form className="space-y-3" onSubmit={event => { event.preventDefault(); if (!invalid && !busy) onSubmit(text); }}>
    <div>
      <label className="block font-medium" htmlFor={`${id}-text`}>{label}</label>
      {hint && <p id={`${id}-hint`} className="text-[color:var(--fs-text-secondary)]">{hint}</p>}
      <textarea id={`${id}-text`} className={`${field} mt-1 h-auto py-2`} rows={4} value={value} required={required}
        aria-invalid={long} aria-describedby={`${hint ? `${id}-hint ` : ""}${id}-count${long ? ` ${id}-long` : ""}`}
        onChange={event => setValue(event.target.value)} />
      <p id={`${id}-count`} className="mt-1 text-xs text-[color:var(--fs-text-secondary)]">{text.length} of {max} characters</p>
      {long && <p id={`${id}-long`} className="mt-1 text-[color:var(--fs-danger)]">{tooLong}</p>}
    </div>
    {error && <div ref={refusal} tabIndex={-1} className={NOTICE_FOCUS}><Notice error>{error}</Notice></div>}
    <div className="flex flex-wrap gap-2">
      <button type="submit" className={primary} disabled={invalid || busy}>{submit}</button>
      <button type="button" className={secondary} disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
  </form>;
}
