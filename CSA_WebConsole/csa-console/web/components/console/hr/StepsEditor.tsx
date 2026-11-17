"use client";
import { useEffect, useRef, useState } from "react";
import type { Option } from "@/lib/hr-pickers";
import type { Anchor, ApprovalBy, AutoCheck, OwnerRole, ProcessKind } from "@/lib/hr-process-api";
import { offsetSentence } from "@/lib/hr-process-view";
import {
  ANCHOR_LABELS, APPROVAL_CHOICES, CHECK_CHOICES, OWNER_CHOICES, SYSTEM_CHECK_LABEL, TEMPLATE_LIMITS, TOO_MANY_STEPS,
  anchorsFor, emptyStep, moveItem, stepName, type StepDraft, type StepField, type StepProblems,
} from "@/lib/hr-template-form";
import { field, secondary } from "../ui";

const card = "rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3";
const problemText = "mt-1 text-sm text-[color:var(--fs-danger)]";
type Which = "up" | "down" | "remove";
/** After a move or a removal, focus goes to this row's button (or its next usable one). */
type FocusAsk = { key: number; which: Which } | { title: number };

/**
 * The steps of a template, 1 to 40 rows: each row edits one step (titles and descriptions in English
 * and Malay side by side, the reference note, the owner, the due offset with its sentence, required,
 * approval and the automatic check) and has Move up, Move down and Remove, each described by the row's
 * name ("Step 3: <title>"). A move keeps focus on the moved row. A 41st step is refused with a sentence.
 * `problems` are shown next to their fields (the editor passes none until the first save).
 */
export function StepsEditor({ kind, steps, problems, people, peopleLoaded, onChange }: {
  kind: ProcessKind;
  steps: StepDraft[];
  problems: StepProblems[];
  people: Option[];
  peopleLoaded: boolean;
  onChange: (steps: StepDraft[]) => void;
}) {
  const [refused, setRefused] = useState("");
  // The focus move a change asks for, made once the rows have re-rendered.
  const focusAsk = useRef<FocusAsk | null>(null);
  const [focusTick, setFocusTick] = useState(0);
  const setFocusAsk = (next: FocusAsk) => { focusAsk.current = next; setFocusTick(count => count + 1); };
  const list = useRef<HTMLOListElement>(null);
  const addButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const ask = focusAsk.current;
    if (!ask) return;
    focusAsk.current = null;
    if ("title" in ask) { document.getElementById(`hr-template-step-${ask.title}-title_en`)?.focus(); return; }
    const order: Which[] = [ask.which, ...(["up", "down", "remove"] as const).filter(which => which !== ask.which)];
    for (const which of order) {
      const button = list.current?.querySelector<HTMLButtonElement>(`[data-step="${ask.key}"][data-move="${which}"]`);
      if (button && !button.disabled) { button.focus(); return; }
    }
    addButton.current?.focus();
  }, [focusTick]);

  const change = (index: number, patch: Partial<StepDraft>) =>
    onChange(steps.map((step, at) => at === index ? { ...step, ...patch } : step));
  const move = (index: number, delta: number, which: Which) => {
    onChange(moveItem(steps, index, index + delta));
    setFocusAsk({ key: steps[index].key, which });
  };
  const remove = (index: number) => {
    if (steps.length <= 1) return;
    const neighbour = steps[index + 1] ?? steps[index - 1];
    onChange(steps.filter((_, at) => at !== index));
    setRefused("");
    // The neighbour's Remove (off when one step is left: then its move buttons, else Add step).
    setFocusAsk({ key: neighbour.key, which: "remove" });
  };
  const add = () => {
    if (steps.length >= TEMPLATE_LIMITS.steps) { setRefused(TOO_MANY_STEPS); return; }
    const step = emptyStep(kind);
    setRefused("");
    onChange([...steps, step]);
    setFocusAsk({ title: step.key });
  };

  return <section aria-labelledby="hr-template-steps-heading" className="space-y-3">
    <h3 id="hr-template-steps-heading" className="font-semibold">Steps</h3>
    <ol ref={list} className="space-y-4">{steps.map((step, index) => <StepRow key={step.key} step={step} index={index}
      count={steps.length} kind={kind} problems={problems[index] ?? {}} people={people} peopleLoaded={peopleLoaded}
      onChange={patch => change(index, patch)} onMove={(delta, which) => move(index, delta, which)}
      onRemove={() => remove(index)} />)}</ol>
    <div className="flex flex-wrap items-center gap-3">
      <button ref={addButton} type="button" className={secondary} onClick={add}>Add step</button>
      {refused && <p role="alert" className="text-sm text-[color:var(--fs-danger)]">{refused}</p>}
    </div>
  </section>;
}

function StepRow({ step, index, count, kind, problems, people, peopleLoaded, onChange, onMove, onRemove }: {
  step: StepDraft;
  index: number;
  count: number;
  kind: ProcessKind;
  problems: StepProblems;
  people: Option[];
  peopleLoaded: boolean;
  onChange: (patch: Partial<StepDraft>) => void;
  onMove: (delta: number, which: Which) => void;
  onRemove: () => void;
}) {
  const base = `hr-template-step-${step.key}`;
  const nameId = `${base}-name`;
  const id = (name: StepField | "owner_role" | "required" | "approval_by" | "auto_check") => `${base}-${name}`;
  const text = (name: StepField, label: string, options: { lang?: string; lines?: number } = {}) =>
    <TextField id={id(name)} name={name} label={label} value={step[name as keyof StepDraft] as string}
      problem={problems[name]} lang={options.lang} lines={options.lines}
      onChange={value => onChange({ [name]: value })} />;
  const offset = step.due_offset_days.trim();
  const sentence = /^-?\d{1,3}$/.test(offset) && Math.abs(Number(offset)) <= TEMPLATE_LIMITS.offset
    ? offsetSentence(step.anchor, Number(offset)) : "";
  const checks = step.auto_check === "access_removed"
    ? [...CHECK_CHOICES, ["access_removed", SYSTEM_CHECK_LABEL] as [AutoCheck, string]] : CHECK_CHOICES;
  const buttons: { which: Which; label: string; disabled: boolean; run: () => void }[] = [
    { which: "up", label: "Move up", disabled: index === 0, run: () => onMove(-1, "up") },
    { which: "down", label: "Move down", disabled: index === count - 1, run: () => onMove(1, "down") },
    { which: "remove", label: "Remove step", disabled: count <= 1, run: onRemove },
  ];
  return <li data-testid="hr-template-step" className={card}>
    <fieldset className="min-w-0 space-y-3">
      <legend id={nameId} className="mb-1 font-semibold [overflow-wrap:anywhere]">{stepName(index, step.title_en)}</legend>
      <div className="grid gap-3 sm:grid-cols-2">
        {text("title_en", "Title (English)")}
        {text("title_ms", "Title (Malay)", { lang: "ms" })}
        {text("description_en", "Description (English)", { lines: 3 })}
        {text("description_ms", "Description (Malay)", { lang: "ms", lines: 3 })}
      </div>
      {text("reference_note", "Reference note")}
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="block text-sm font-medium" htmlFor={id("owner_role")}>Owner</label>
          <select id={id("owner_role")} className={`${field} mt-1`} value={step.owner_role}
            onChange={event => onChange({ owner_role: event.target.value as OwnerRole })}>
            {OWNER_CHOICES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </div>
        {step.owner_role === "person" && <div>
          <label className="block text-sm font-medium" htmlFor={id("owner_employee_id")}>Employee</label>
          <select id={id("owner_employee_id")} className={`${field} mt-1`} value={step.owner_employee_id}
            aria-invalid={Boolean(problems.owner_employee_id)}
            aria-describedby={problems.owner_employee_id ? `${id("owner_employee_id")}-problem` : undefined}
            onChange={event => onChange({ owner_employee_id: event.target.value })}>
            <option value="">{peopleLoaded ? "Choose a person…" : "Loading…"}</option>
            {people.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
          {problems.owner_employee_id && <p id={`${id("owner_employee_id")}-problem`} className={problemText}>
            {problems.owner_employee_id}</p>}
        </div>}
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="block text-sm font-medium" htmlFor={id("due_offset_days")}>Days</label>
          <input id={id("due_offset_days")} type="number" inputMode="numeric" min={-TEMPLATE_LIMITS.offset}
            max={TEMPLATE_LIMITS.offset} step={1} className={`${field} mt-1`} value={step.due_offset_days}
            aria-invalid={Boolean(problems.due_offset_days)}
            aria-describedby={`${id("due_offset_days")}-hint${problems.due_offset_days ? ` ${id("due_offset_days")}-problem` : ""}`}
            onChange={event => onChange({ due_offset_days: event.target.value })} />
          <p id={`${id("due_offset_days")}-hint`} className="mt-1 text-xs text-[color:var(--fs-text-secondary)]">
            Before the date is a minus number.</p>
          {problems.due_offset_days && <p id={`${id("due_offset_days")}-problem`} className={problemText}>
            {problems.due_offset_days}</p>}
        </div>
        <div>
          <label className="block text-sm font-medium" htmlFor={id("anchor")}>Counted from</label>
          <select id={id("anchor")} className={`${field} mt-1`} value={step.anchor}
            aria-invalid={Boolean(problems.anchor)}
            onChange={event => onChange({ anchor: event.target.value as Anchor })}>
            {anchorsFor(kind).map(anchor => <option key={anchor} value={anchor}>{ANCHOR_LABELS[anchor]}</option>)}
          </select>
          {problems.anchor && <p className={problemText}>{problems.anchor}</p>}
        </div>
      </div>
      {sentence && <p className="text-sm" data-testid="hr-template-step-due">Due: {sentence}</p>}
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label className="block text-sm font-medium" htmlFor={id("approval_by")}>Approval by</label>
          <select id={id("approval_by")} className={`${field} mt-1`} value={step.approval_by}
            onChange={event => onChange({ approval_by: event.target.value as ApprovalBy | "" })}>
            {APPROVAL_CHOICES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </div>
        <div>
          <label className="block text-sm font-medium" htmlFor={id("auto_check")}>Automatic check</label>
          <select id={id("auto_check")} className={`${field} mt-1`} value={step.auto_check}
            onChange={event => onChange({ auto_check: event.target.value as AutoCheck | "" })}>
            {checks.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </div>
      </div>
      <label className="flex min-h-10 items-center gap-2 text-sm pointer-coarse:min-h-11" htmlFor={id("required")}>
        <input id={id("required")} type="checkbox" className="h-4 w-4" checked={step.required}
          onChange={event => onChange({ required: event.target.checked })} />Required</label>
      <div className="flex flex-wrap gap-2">
        {buttons.map(button => <button key={button.which} type="button" className={secondary} data-step={step.key}
          data-move={button.which} disabled={button.disabled} aria-describedby={nameId} onClick={button.run}>
          {button.label}</button>)}
      </div>
    </fieldset>
  </li>;
}

/** A labelled text box or (with `lines`) a multi-line one, with its problem sentence. */
export function TextField({ id, name, label, value, problem, lang, lines, list, onChange }: {
  id: string;
  name: string;
  label: string;
  value: string;
  problem?: string;
  lang?: string;
  lines?: number;
  list?: string;
  onChange: (value: string) => void;
}) {
  const shared = {
    id, lang, value, "data-field": name, "aria-invalid": Boolean(problem),
    "aria-describedby": problem ? `${id}-problem` : undefined,
  };
  // No maxLength: a longer text (pasted) stays visible with its sentence instead of being cut.
  return <div className="min-w-0">
    <label className="block text-sm font-medium" htmlFor={id}>{label}</label>
    {lines ? <textarea {...shared} rows={lines} className={`${field} mt-1 h-auto py-2`}
      onChange={event => onChange(event.target.value)} />
      : <input {...shared} list={list} className={`${field} mt-1`} onChange={event => onChange(event.target.value)} />}
    {problem && <p id={`${id}-problem`} className={problemText}>{problem}</p>}
  </div>;
}
