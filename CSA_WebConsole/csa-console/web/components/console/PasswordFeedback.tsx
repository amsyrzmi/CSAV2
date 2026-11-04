"use client";
import { useEffect, useState } from "react";
import { Check, Circle } from "lucide-react";
import {
  announcementFor,
  passwordStrength,
  RULE_LABELS,
  RULE_ORDER,
  type PasswordRules,
} from "@/lib/password-rules";

/** How long typing must pause before the checklist is read out. */
const PAUSE_MS = 800;

const LEVEL_TONE = [
  "",
  "bg-[var(--fs-danger)]",
  "bg-[var(--fs-warning)]",
  "bg-[var(--fs-success)]",
  "bg-[var(--fs-success)]",
];

/** Length-based hint under the new password. Advisory: it never blocks saving. */
export function StrengthHint({ password }: { password: string }) {
  const { level, label } = passwordStrength(password);
  return (
    <div data-testid="password-strength" className="mt-3">
      <div className="flex items-center gap-3">
        <div aria-hidden="true" className="grid flex-1 grid-cols-4 gap-1">
          {[1, 2, 3, 4].map((step) => (
            <span
              key={step}
              className={`h-1.5 rounded-full ${step <= level ? LEVEL_TONE[level] : "bg-[var(--fs-border)]"}`}
            />
          ))}
        </div>
        <span className="min-w-20 text-right text-xs font-medium text-ink-900">
          {level === 0 ? "No hint yet" : `Strength: ${label}`}
        </span>
      </div>
      <p className="mt-1 text-xs text-[color:var(--fs-text-secondary)]">
        {level === 0
          ? "Type a new password to see a hint."
          : "This is only a hint. It looks at length and sends nothing anywhere."}
      </p>
    </div>
  );
}

/**
 * The three rules, each with a mark and words. The list is not live itself: a separate quiet
 * line is updated once typing has paused and only when the set of passing rules changed, so a
 * screen reader hears a short summary instead of every keystroke.
 */
export function RuleChecklist({ rules, typed }: { rules: PasswordRules; typed: boolean }) {
  const summary = announcementFor(rules);
  const [announced, setAnnounced] = useState("");
  useEffect(() => {
    // Cleared fields (after a save) empty the line at once; typing waits for a pause.
    const timer = setTimeout(() => setAnnounced(typed ? summary : ""), typed ? PAUSE_MS : 0);
    return () => clearTimeout(timer);
  }, [summary, typed]);
  return (
    <div data-testid="password-checklist" className="mt-3">
      <p className="text-xs font-medium text-ink-900">Before you can save:</p>
      <ul className="mt-1 space-y-1">
        {RULE_ORDER.map((key) => {
          const met = rules[key];
          const Mark = met ? Check : Circle;
          return (
            <li
              key={key}
              data-testid={`password-rule-${key}`}
              data-state={met ? "met" : "unmet"}
              className={`flex items-center gap-2 text-xs ${met ? "text-[color:var(--fs-success)]" : "text-[color:var(--fs-text-secondary)]"}`}
            >
              <Mark size={14} aria-hidden="true" className="shrink-0" />
              <span>
                {RULE_LABELS[key]}
                <span className="sr-only">{met ? " (done)" : " (not done)"}</span>
              </span>
            </li>
          );
        })}
      </ul>
      <p aria-live="polite" className="sr-only">
        {announced}
      </p>
    </div>
  );
}
