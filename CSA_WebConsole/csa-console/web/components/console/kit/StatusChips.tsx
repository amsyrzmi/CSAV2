"use client";

import { chipLabel, isPressed, nextSingle, toggleChip, type ChipOption } from "@/lib/chips";

const chip =
  "inline-flex min-h-10 items-center gap-1.5 rounded-[var(--fs-radius-round)] border px-3 py-1.5 pointer-coarse:min-h-11 " +
  "transition-[background-color,border-color,color] duration-150 ease-(--fs-ease-out) motion-reduce:transition-none " +
  "focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]";
const idle =
  "border-[color:var(--fs-border-control)] bg-[var(--fs-surface)] text-[color:var(--fs-text)] hover:border-[color:var(--fs-primary)]";
const pressed =
  "border-[color:var(--fs-primary)] bg-[var(--fs-surface-blue)] text-[color:var(--fs-text)]";

/**
 * A wrapping row of toggle chips. Pressed state is a check mark and bold words as well as
 * colour. `single` always has one chip pressed; `multi` lets any number be pressed (an "All"
 * chip, if the page wants one, is an ordinary option the caller handles in `onChange`).
 */
export default function StatusChips<T extends string>({
  options,
  value,
  onChange,
  mode,
  testId,
  ariaLabel,
}: {
  options: { value: T; label: string; count?: number | null }[];
  value: T | T[];
  onChange: (next: T | T[]) => void;
  mode: "single" | "multi";
  testId: string;
  ariaLabel: string;
}) {
  const press = (picked: T) => {
    if (mode === "multi") {
      onChange(toggleChip(Array.isArray(value) ? value : [value], picked));
    } else {
      onChange(nextSingle(Array.isArray(value) ? value[0] : value, picked));
    }
  };
  return (
    <div role="group" aria-label={ariaLabel} data-testid={testId} className="flex flex-wrap gap-2">
      {options.map((option: ChipOption<T>) => {
        const on = isPressed(value, option.value);
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={on}
            data-testid={`${testId}-${option.value}`}
            onClick={() => press(option.value)}
            className={`${chip} ${on ? pressed : idle}`}
            // Inline: the console's `.fs-ui button { font: inherit }` would beat a weight class.
            style={{ fontSize: "var(--fs-text-sm)", fontWeight: on ? 700 : 500 }}
          >
            {on ? (
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                width="14"
                height="14"
                fill="none"
                stroke="currentColor"
                strokeWidth="3"
                strokeLinecap="round"
                strokeLinejoin="round"
                className="shrink-0"
              >
                <path d="m5 12 5 5 9-10" />
              </svg>
            ) : null}
            <span className="min-w-0 [overflow-wrap:anywhere]">{chipLabel(option)}</span>
          </button>
        );
      })}
    </div>
  );
}
