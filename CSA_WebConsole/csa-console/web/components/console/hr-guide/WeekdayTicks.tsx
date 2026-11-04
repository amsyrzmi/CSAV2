"use client";

import { useEffect, useRef, useState } from "react";

/**
 * The working weekdays as seven tick-boxes. They submit exactly what the multi-select did: one
 * `name=value` pair per ticked day, in weekday order, so `FormData.getAll(name)` is unchanged. Like the
 * select (which was required), at least one day must be ticked before the form can be sent.
 */
export default function WeekdayTicks({ name, label, options }: {
  name: string;
  label: string;
  options: readonly (readonly [string, string])[];
}) {
  const [ticked, setTicked] = useState<string[]>([]);
  const first = useRef<HTMLInputElement>(null);
  useEffect(() => {
    first.current?.setCustomValidity(ticked.length === 0 ? "Choose at least one working day." : "");
  }, [ticked]);
  return <fieldset data-testid="hr-weekday-ticks" className="min-w-0 sm:col-span-2">
    <legend className="mb-1 text-sm">{label}</legend>
    <div className="flex flex-wrap gap-2">{options.map(([value, text], index) =>
      <label key={value} className="inline-flex min-h-10 cursor-pointer items-center gap-2 rounded-[var(--fs-radius-control)] border border-[color:var(--fs-border-control)] bg-[var(--fs-surface)] px-3 text-sm text-[color:var(--fs-text)] has-[:focus-visible]:outline-3 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-[color:var(--fs-focus)] pointer-coarse:min-h-11">
        <input ref={index === 0 ? first : undefined} type="checkbox" className="size-4 shrink-0" name={name} value={value}
          checked={ticked.includes(value)}
          onChange={event => setTicked(old => event.target.checked ? [...old, value] : old.filter(item => item !== value))} />
        {text}
      </label>)}
    </div>
  </fieldset>;
}
