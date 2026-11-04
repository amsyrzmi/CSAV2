/**
 * Pure state rules for the status chips (components/console/kit/StatusChips.tsx). They live
 * here so node tests can cover them without a browser.
 */
export interface ChipOption<T extends string = string> {
  value: T;
  label: string;
  /** A number shows as "label (count)"; null, undefined or a bad number shows no figure. */
  count?: number | null;
}

/** Multi select: the pressed value joins the selection or leaves it. Never mutates `current`. */
export function toggleChip<T>(current: T[], value: T): T[] {
  return current.includes(value) ? current.filter((item) => item !== value) : [...current, value];
}

/** Single select: pressing a chip chooses it; pressing the chosen chip keeps it chosen. */
export function nextSingle<T>(current: T, value: T): T {
  return current === value ? current : value;
}

/** Whether `value` is pressed, for a single value or a list of them. */
export function isPressed<T>(selected: T | T[], value: T): boolean {
  return Array.isArray(selected) ? selected.includes(value) : selected === value;
}

/**
 * The words on the chip. A count is a real figure or nothing: a failed or missing count is
 * left out, never drawn as zero.
 */
export function chipLabel(option: { label: string; count?: number | null }): string {
  const { label, count } = option;
  return typeof count === "number" && Number.isFinite(count) && count >= 0
    ? `${label} (${count})`
    : label;
}
