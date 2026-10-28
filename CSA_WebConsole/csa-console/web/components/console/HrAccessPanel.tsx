"use client";
import {
  applyPreset, includedWith, setSwitch, SWITCH_IDS, withIncluded,
  type AccessPerson, type AccessPersonResult, type AccessWarningOut, type PresetId, type SwitchId,
} from "@/lib/hr-access-api";
import {
  CUSTOM_COPY, GROUPS, PRESET_COPY, refusalText, SWITCH_COPY, warningText,
} from "@/lib/hr-access-copy";
import { ApiError } from "@/lib/api";
import { field, secondary } from "./ui";

/** An explained server sentence, or the fallback; raw codes stay off the screen. */
export function plain(cause: unknown, fallback: string): string {
  return cause instanceof ApiError && cause.message !== cause.code ? cause.message : fallback;
}

export function initials(name: string | null): string {
  const words = (name ?? "").trim().split(/\s+/).filter(Boolean);
  return (words.slice(0, 2).map((word) => word[0]).join("") || "?").toUpperCase();
}

/** Warnings about this member: as the person named, or as the employee linked to them. */
export function warningsFor(person: AccessPerson, warnings: AccessWarningOut[]): AccessWarningOut[] {
  return warnings.filter((w) => w.user_id === person.user_id
    || (person.employee_id !== null && w.employee_id === person.employee_id));
}

/** The selected member: their switches, grouped, with presets, copy-from, custom access and warnings. */
export function HrAccessPanel({ person, desired, others, warnings, result, disabled, onChange, onBack, onAdvanced }: {
  person: AccessPerson;
  desired: SwitchId[];
  others: AccessPerson[];
  warnings: AccessWarningOut[];
  result: AccessPersonResult | undefined;
  disabled: boolean;
  onChange: (on: SwitchId[]) => void;
  onBack: () => void;
  onAdvanced: () => void;
}) {
  const name = person.display_name ?? "Unnamed member";
  const saved = new Set(person.on);
  return <section className="card space-y-5 p-5 sm:p-6" data-testid="hr-access-panel" aria-label={`Access for ${name}`}>
    <button type="button" className={`${secondary} lg:hidden`} onClick={onBack}>Go back to the list</button>
    <header className="flex items-center gap-3">
      <span aria-hidden className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-brand-50 text-sm font-semibold text-brand-700">{initials(person.display_name)}</span>
      <div className="min-w-0">
        <h2 className="text-base font-semibold">{name}</h2>
        <p className="text-sm text-ink-500">
          {[person.department, person.is_owner ? "HR owner" : null,
            person.employee_id ? `${person.team_size} ${person.team_size === 1 ? "person reports" : "people report"} to them` : "No employee record linked"]
            .filter(Boolean).join(" · ")}
        </p>
      </div>
    </header>
    {result?.outcome === "refused" && <p role="alert" className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800">
      Not saved. {refusalText(result.reason)}</p>}
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="block text-sm">Preset
        <select className={`${field} mt-1`} data-testid="hr-access-preset" value="" disabled={disabled}
          onChange={(event) => { if (event.target.value) onChange(applyPreset(desired, event.target.value as PresetId)); }}>
          <option value="">Add a preset…</option>
          {(Object.keys(PRESET_COPY) as PresetId[]).map((id) =>
            <option key={id} value={id}>{PRESET_COPY[id].label}: {PRESET_COPY[id].description}</option>)}
        </select>
      </label>
      <label className="block text-sm">Copy access from…
        <select className={`${field} mt-1`} data-testid="hr-access-copy-from" value="" disabled={disabled || others.length === 0}
          onChange={(event) => {
            const source = others.find((other) => other.user_id === event.target.value);
            if (source) onChange(withIncluded(source.on));
          }}>
          <option value="">Choose a member…</option>
          {others.map((other) => <option key={other.user_id} value={other.user_id}>{other.display_name ?? "Unnamed member"}</option>)}
        </select>
      </label>
    </div>
    <p className="text-xs text-ink-500">Presets only turn switches on. Copying replaces the switches but keeps custom access.</p>
    {GROUPS.map((group) => <fieldset key={group} className="space-y-3" disabled={disabled}>
      <legend className="mb-2 text-sm font-semibold">{group}</legend>
      {SWITCH_IDS.filter((id) => SWITCH_COPY[id].group === group).map((id) => {
        const on = desired.includes(id);
        const inside = includedWith(desired, id);
        const changed = on !== saved.has(id);
        return <label key={id} className={`flex gap-3 rounded-md border p-3 text-sm ${changed ? "border-brand-300 bg-brand-50" : "border-ink-200"}`}>
          <input type="checkbox" className="mt-1 h-4 w-4 shrink-0" data-testid={`hr-access-switch-${id}`} checked={on}
            disabled={inside !== null} onChange={(event) => onChange(setSwitch(desired, id, event.target.checked))} />
          <span>
            <span className="font-medium">{SWITCH_COPY[id].label}</span>
            {changed && <span className="ml-2 text-xs font-medium text-brand-700">{on ? "Turning on" : "Turning off"}</span>}
            <span className="mt-1 block text-ink-600">{SWITCH_COPY[id].explanation}</span>
            {inside && <span className="mt-1 block text-xs text-ink-500">Included with {SWITCH_COPY[inside].label}</span>}
          </span>
        </label>;
      })}
    </fieldset>)}
    {person.custom.length > 0 && <div className="space-y-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm">
      <h3 className="font-semibold">{CUSTOM_COPY.title}</h3>
      <p>{CUSTOM_COPY.intro}</p>
      <ul className="list-disc space-y-1 pl-5">{person.custom.map((row) => <li key={row}>{row}</li>)}</ul>
      <button type="button" className="font-medium text-brand-700 underline" onClick={onAdvanced}>{CUSTOM_COPY.link}</button>
    </div>}
    {warnings.length > 0 && <div className="space-y-2 rounded-md border border-amber-200 bg-amber-50 p-3 text-sm" role="status">
      <h3 className="font-semibold">Things to check</h3>
      <ul className="list-disc space-y-1 pl-5">{warnings.map((w, index) => <li key={`${w.kind}:${index}`}>{warningText(w)}</li>)}</ul>
    </div>}
  </section>;
}
