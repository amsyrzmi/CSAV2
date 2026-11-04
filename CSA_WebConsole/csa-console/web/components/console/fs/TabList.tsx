"use client";

import { useRef, type KeyboardEvent } from "react";

const tab =
  "inline-flex min-h-10 items-center justify-center gap-1.5 rounded-[var(--fs-radius-control)] border px-4 py-1.5 pointer-coarse:min-h-11 " +
  "transition-[background-color,border-color,color] duration-150 ease-(--fs-ease-out) motion-reduce:transition-none";
const idle =
  "border-[color:var(--fs-border-control)] bg-[var(--fs-surface)] text-[color:var(--fs-text)] hover:border-[color:var(--fs-primary)]";
// The inset ring doubles the selected border to 2px without changing the tab's size.
const selected =
  "border-[color:var(--fs-primary)] bg-[var(--fs-surface-blue)] text-[color:var(--fs-text)] shadow-[inset_0_0_0_1px_var(--fs-primary)]";

/**
 * A tab row that wraps onto more lines on a narrow screen instead of running past the edge. The
 * same props as the older kit `Tabs`. The selected tab is the only one in the tab order; the
 * arrow keys, Home and End move to a neighbour and select it.
 */
export default function TabList<T extends string>({
  value,
  onChange,
  items,
  ariaLabel,
}: {
  value: T;
  onChange: (v: T) => void;
  items: { value: T; label: string; count?: number }[];
  ariaLabel?: string;
}) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  const current = Math.max(
    0,
    items.findIndex((item) => item.value === value),
  );

  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const last = items.length - 1;
    const next =
      event.key === "ArrowRight" || event.key === "ArrowDown"
        ? index === last ? 0 : index + 1
        : event.key === "ArrowLeft" || event.key === "ArrowUp"
          ? index === 0 ? last : index - 1
          : event.key === "Home"
            ? 0
            : event.key === "End"
              ? last
              : null;
    if (next === null) return;
    event.preventDefault();
    buttons.current[next]?.focus();
    onChange(items[next].value);
  }

  return (
    <div role="tablist" aria-label={ariaLabel} className="flex flex-wrap gap-2">
      {items.map((item, index) => {
        const on = item.value === value;
        return (
          <button
            key={item.value}
            ref={(element) => {
              buttons.current[index] = element;
            }}
            type="button"
            role="tab"
            aria-selected={on}
            tabIndex={index === current ? 0 : -1}
            onClick={() => onChange(item.value)}
            onKeyDown={(event) => onKeyDown(event, index)}
            className={`${tab} ${on ? selected : idle}`}
          >
            {/* Sized on a span: the tokens' unlayered `font: inherit` on buttons beats utilities. */}
            <span className="min-w-0 text-[length:var(--fs-text-sm)] font-medium [overflow-wrap:anywhere]">
              {item.label}
            </span>
            {item.count !== undefined && (
              <span className="num rounded-full bg-[var(--fs-neutral-bg)] px-1.5 text-xs font-semibold text-[color:var(--fs-neutral-text)]">
                {item.count}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
