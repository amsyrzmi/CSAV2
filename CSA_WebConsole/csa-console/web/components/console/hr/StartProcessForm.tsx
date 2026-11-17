"use client";
import { useEffect, useId, useRef, useState } from "react";
import type { ProcessKind, ProcessStart, TemplateSummary } from "@/lib/hr-process-api";
import { processErrorText, processLoadError, processSaveError } from "@/lib/hr-process-copy";
import { processKindLabel, replaceQuestion } from "@/lib/hr-process-view";
import Dialog from "../fs/Dialog";
import { action as primary, field, secondary, Notice } from "../ui";
import type { Options } from "./Pickers";
import { PersonSelect } from "./Pickers";
import { isStale, NOTICE_FOCUS, type ProcessClient } from "./TaskActions";
import { useHrRunner } from "./useHrRunner";

const KINDS: ProcessKind[] = ["onboarding", "offboarding", "custom"];
const ALREADY_ACTIVE: ReadonlySet<string> = new Set(["process_already_active"]);

// Set when a start succeeds; the process page that opens next reads it once and moves focus to its heading.
let arrivedFromStart = false;
/** True once, right after a start brought the person to the new process. */
export function takeStartArrival(): boolean {
  const arrived = arrivedFromStart;
  arrivedFromStart = false;
  return arrived;
}

/** "Lina Wong" from a people option "Lina Wong · Packer"; null when the picker fell back to a typed id. */
function personName(people: Options, employeeId: string): string | null {
  const option = people.options.find(([id]) => id === employeeId);
  return option ? option[1].split(" · ")[0] : null;
}

type Problem = { field: "employee_id" | "template_id"; text: string };

/**
 * Start a process by hand (the HR team): a person, the kind and, for a custom process, its
 * template (else the matching one). When the person already has an active process of that kind
 * the API refuses, and the form asks whether to replace it: only "Replace" sends `replace: true`
 * (a different body, so the client gives it its own Idempotency-Key).
 */
export function StartProcessForm({ client, people, onClose, onStarted }: {
  client: ProcessClient;
  people: Options;
  onClose: () => void;
  onStarted: (processId: string) => void;
}) {
  const runner = useHrRunner();
  const { busy, run } = runner;
  const id = useId();
  const [kind, setKind] = useState<ProcessKind>("onboarding");
  const [templates, setTemplates] = useState<TemplateSummary[] | { failed: string } | null>(null);
  const [problem, setProblem] = useState<Problem | null>(null);
  const [error, setError] = useState("");
  const [replace, setReplace] = useState<{ body: ProcessStart; name: string | null } | null>(null);
  const [kept, setKept] = useState(0);
  const form = useRef<HTMLFormElement>(null);
  const question = useRef<HTMLDivElement>(null);
  const refusal = useRef<HTMLDivElement>(null);
  const submit = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    let active = true;
    client.templates()
      .then(list => { if (active) setTemplates(list.items); })
      .catch(cause => { if (active) setTemplates({ failed: processLoadError(cause) }); });
    return () => { active = false; };
  }, [client]);
  useEffect(() => { if (replace) question.current?.focus(); }, [replace]);
  useEffect(() => { if (error) refusal.current?.focus(); }, [error]);
  useEffect(() => { if (kept) submit.current?.focus(); }, [kept]);

  const choices = Array.isArray(templates) ? templates.filter(item => item.kind === kind && item.is_active) : [];
  const custom = kind === "custom";
  const close = () => { if (!busy) onClose(); };
  const send = (body: ProcessStart, name: string | null) => run(async () => {
    setError("");
    try {
      const receipt = await client.startProcess(body);
      arrivedFromStart = true;
      onStarted(receipt.process_id);
    } catch (cause) {
      setReplace(null);
      if (!body.replace && isStale(cause, ALREADY_ACTIVE)) setReplace({ body, name });
      else setError(processSaveError(cause, "start"));
    }
  });
  const start = () => {
    const data = new FormData(form.current ?? undefined);
    const employee = String(data.get("employee_id") ?? "").trim();
    const template = String(data.get("template_id") ?? "");
    const wrong = !employee ? { field: "employee_id" as const, text: "Choose a person." }
      : custom && !template ? { field: "template_id" as const, text: processErrorText("process_template_required") }
        : null;
    setProblem(wrong);
    if (wrong) {
      const control = form.current?.elements.namedItem(wrong.field);
      if (control instanceof HTMLElement) control.focus();
      return;
    }
    void send({ employee_id: employee, kind, ...(template ? { template_id: template } : {}) }, personName(people, employee));
  };

  return <Dialog open onClose={close} title="Start a process" testId="hr-start-dialog">
    <form ref={form} noValidate className="space-y-3 text-sm" onSubmit={event => { event.preventDefault(); if (!busy) start(); }}
      onChange={event => {
        const target = event.target;
        if (target instanceof HTMLSelectElement || target instanceof HTMLInputElement) {
          setProblem(null);
          setReplace(null);
        }
      }}>
      <PersonSelect people={people} name="employee_id" label="Person" />
      <label className="block">Kind
        <select className={`${field} mt-1`} name="kind" value={kind}
          onChange={event => setKind(event.target.value as ProcessKind)}>
          {KINDS.map(item => <option key={item} value={item}>{processKindLabel(item)}</option>)}
        </select></label>
      <label className="block">Template
        {/* Keyed by kind: a template of another kind never stays chosen. */}
        <select key={kind} className={`${field} mt-1`} name="template_id" defaultValue=""
          aria-invalid={problem?.field === "template_id"}
          aria-describedby={`${id}-template-hint${problem?.field === "template_id" ? ` ${id}-problem` : ""}`}>
          <option value="">{custom ? "Choose a template…" : "Use the matching template"}</option>
          {choices.map(item => <option key={item.template_id} value={item.template_id}>{item.name}</option>)}
        </select></label>
      <p id={`${id}-template-hint`} className="text-[color:var(--fs-text-secondary)]">
        {custom ? "A custom process always starts from the template you choose."
          : "Leave it to use the template that applies to this person."}</p>
      {templates && "failed" in templates && <Notice error>{templates.failed}</Notice>}
      {problem && <p id={`${id}-problem`} role="alert" className="text-[color:var(--fs-danger)]">{problem.text}</p>}
      {error && <div ref={refusal} tabIndex={-1} className={NOTICE_FOCUS}><Notice error>{error}</Notice></div>}
      {replace ? <div ref={question} tabIndex={-1} data-testid="hr-start-replace" role="group"
        aria-labelledby={`${id}-replace`} className={`space-y-3 ${NOTICE_FOCUS}`}>
        <Notice warning><span id={`${id}-replace`}>{replaceQuestion(replace.body.kind, replace.name)}</span>{" "}
          Replacing it cancels the active one and skips its open steps.</Notice>
        <div className="flex flex-wrap gap-2">
          <button type="button" className={primary} disabled={busy}
            onClick={() => void send({ ...replace.body, replace: true }, replace.name)}>Replace</button>
          <button type="button" className={secondary} disabled={busy}
            onClick={() => { setReplace(null); setKept(count => count + 1); }}>Keep</button>
        </div>
      </div>
        : <div className="flex flex-wrap gap-2">
          <button ref={submit} type="submit" className={primary} disabled={busy}>Start process</button>
          <button type="button" className={secondary} disabled={busy} onClick={close}>Cancel</button>
        </div>}
    </form>
  </Dialog>;
}
