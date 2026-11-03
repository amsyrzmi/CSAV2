"use client";

import { applyPreset, type PresetId, type SwitchId } from "@/lib/hr-access-api";
import { PRESET_COPY } from "@/lib/hr-access-copy";

/** The five roles, in the order people think of them. A role with no preset in PRESET_COPY is left out. */
const ROLES: readonly PresetId[] = ["staff", "manager", "hr_team", "payroll_preparer", "payroll_reviewer"];

const card =
  "flex h-full w-full min-w-0 flex-col gap-1 rounded-[var(--fs-radius-tile)] border border-[color:var(--fs-border-control)] bg-[var(--fs-surface)] p-3 text-left text-sm text-[color:var(--fs-text)] transition-colors duration-150 hover:border-[color:var(--fs-primary)] hover:bg-[var(--fs-surface-blue)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] disabled:cursor-default disabled:border-[color:var(--fs-border)] disabled:bg-[var(--fs-surface)] disabled:hover:bg-[var(--fs-surface)] pointer-coarse:min-h-11";

/**
 * Five roles that each turn on a preset's permissions for the selected member. A card calls the
 * page's own change handler with `applyPreset(desired, id)`, exactly as the Preset list does:
 * presets only turn permissions on, and nothing is saved until the owner saves from the bar below.
 */
export default function RoleCards({ desired, disabled, onChange }: {
  desired: SwitchId[];
  disabled: boolean;
  onChange: (on: SwitchId[]) => void;
}) {
  const roles = ROLES.filter((id) => id in PRESET_COPY);
  return <section data-testid="hr-role-cards" aria-labelledby="hr-role-cards-title" className="min-w-0 space-y-2">
    <div className="space-y-1">
      <h3 id="hr-role-cards-title" className="text-sm font-semibold text-[color:var(--fs-text)]">Start from a role</h3>
      <p className="max-w-prose text-sm leading-6 text-[color:var(--fs-text-secondary)]">
        Choose a role to turn on its permissions. Changes wait in the bar below until you save them.
      </p>
    </div>
    <ul className="grid gap-3 [grid-template-columns:repeat(auto-fit,minmax(min(100%,12.5rem),1fr))]">{roles.map((id) => {
      const next = applyPreset(desired, id);
      const added = next.length - desired.length;
      return <li key={id} className="min-w-0">
        <button type="button" data-testid={`hr-role-card-${id}`} className={card} disabled={disabled || added === 0}
          onClick={() => onChange(next)}>
          <span className="font-semibold [overflow-wrap:anywhere]">{PRESET_COPY[id].label}</span>
          <span className="text-[color:var(--fs-text-secondary)] [overflow-wrap:anywhere]">{PRESET_COPY[id].description}</span>
          <span className="mt-auto pt-1 text-xs font-medium text-[color:var(--fs-text)]">
            {added === 0 ? "Already has these permissions" : `Turns on ${added} ${added === 1 ? "permission" : "permissions"}`}
          </span>
        </button>
      </li>;
    })}</ul>
  </section>;
}
