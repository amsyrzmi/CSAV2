"use client";
import { isValidElement, useRef, useState } from "react";
import { action, field, Notice, Panel } from "../ui";

/** The HR tool forms, moved from HrAutomation. A field is a Spec, or a ready control such as a picker. */
export type Spec = { multiple?: boolean; name: string; label: string; type?: string; options?: [string, string][]; optional?: boolean; value?: string; max?: number };
export const regions: [string, string][] = [["peninsular_labuan", "Peninsular Malaysia / Labuan"], ["sabah", "Sabah"], ["sarawak", "Sarawak"]];
export const value = (data: FormData, name: string) => String(data.get(name) ?? "").trim();
export const maybe = (data: FormData, name: string) => value(data, name) || null;
export const local = (input: string) => `${input}:00+08:00`;
export const ref = (name: string, label: string): Spec => ({ name, label });
export const regionField: Spec = { name: "region", label: "Workforce region", options: regions };
export const dates: Spec[] = [{ name: "starts_on", label: "Period start", type: "date" }, { name: "ends_on", label: "Period end", type: "date" }];

export function Form({ title, fields, disabled, save, children }: { title: string; fields: (Spec | React.ReactElement)[]; disabled: boolean;
  save: (data: FormData) => Promise<void>; children?: React.ReactNode }) {
  const submitting = useRef(false);
  const [localBusy, setLocalBusy] = useState(false);
  const [problem, setProblem] = useState("");
  return <Panel title={title}>{problem && <Notice error>{problem}</Notice>}<form onSubmit={event => {
    event.preventDefault(); if (submitting.current) return;
    const data = new FormData(event.currentTarget); submitting.current = true; setLocalBusy(true); setProblem("");
    void save(data).catch(cause => setProblem((cause as Error).message)).finally(() => { submitting.current = false; setLocalBusy(false); });
  }}>
    <fieldset disabled={disabled || localBusy} className="grid gap-4 sm:grid-cols-2">{fields.map(spec => isValidElement(spec) ? spec : <label className="block text-sm" key={spec.name}>{spec.label}
      {spec.options ? <select className={`${field} mt-1 ${spec.multiple ? "h-36" : ""}`} name={spec.name} required={!spec.optional} multiple={spec.multiple} size={spec.multiple ? spec.options.length : undefined} defaultValue={spec.multiple ? [] : spec.value ?? ""}>
        {!spec.multiple && <option value="">Choose…</option>}{spec.options.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select>
        : spec.type === "textarea" ? <textarea className={`${field} mt-1 h-32 py-2`} name={spec.name} maxLength={spec.max ?? 1200} required={!spec.optional} />
          : <input className={`${field} mt-1`} name={spec.name} type={spec.type ?? "text"} defaultValue={spec.value}
            maxLength={spec.max ?? 200} required={!spec.optional} step={spec.type === "number" ? "0.01" : undefined} accept={spec.type === "file" ? ".csv,text/csv" : undefined} />}
    </label>)}{children}<button className={action}>Save {title.toLowerCase()}</button></fieldset>
  </form></Panel>;
}

/** A single text input with its label, from the Training and Requests forms. */
export function Input({ name, label, type = "text", maxLength = 160, required = true }: {
  name: string; label: string; type?: string; maxLength?: number; required?: boolean;
}) {
  return <label className="block text-sm">{label}<input name={name} type={type}
    maxLength={maxLength} required={required} className={`${field} mt-1`} /></label>;
}
