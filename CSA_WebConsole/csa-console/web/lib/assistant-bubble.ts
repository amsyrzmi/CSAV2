/** Pure rules for when the collapsed assistant dock may offer an idea (no React, no I/O). */

/** How long the person must be idle before the dock offers an idea. */
export const BUBBLE_WAIT_MS = 20_000;

/** localStorage key holding the ids of ideas the person has dismissed. */
export const DISMISSED_KEY = "fs-assistant-dismissed-ideas";

/** Reads the stored dismissed ids; anything that is not a JSON list of strings gives what it can, never throws. */
export function parseDismissed(raw: string | null): string[] {
  if (!raw) return [];
  try {
    const value: unknown = JSON.parse(raw);
    if (!Array.isArray(value)) return [];
    return value.filter((item): item is string => typeof item === "string");
  } catch {
    return [];
  }
}

export function serializeDismissed(ids: readonly string[]): string {
  return JSON.stringify(ids);
}

/**
 * The first idea the person has not dismissed, or null when there is none or one bubble has
 * already been shown on this visit (one idea per visit, never a stream).
 */
export function pickBubbleIdea(
  candidates: readonly { id: string; text: string }[],
  dismissed: readonly string[],
  shownThisVisit: boolean,
): { id: string; text: string } | null {
  if (shownThisVisit) return null;
  return candidates.find((idea) => !dismissed.includes(idea.id)) ?? null;
}

/**
 * True only when the dock is closed and quiet: no panel or card open, not on Overview (the
 * assistant is the page there), nothing being answered, none shown yet this visit, the tab
 * visible, and the person idle for at least BUBBLE_WAIT_MS.
 */
export function bubbleMayShow(state: {
  panelOpen: boolean;
  cardOpen: boolean;
  onOverview: boolean;
  busy: boolean;
  shownThisVisit: boolean;
  idleMs: number;
  hidden: boolean;
}): boolean {
  if (state.panelOpen || state.cardOpen || state.onOverview || state.busy) return false;
  if (state.shownThisVisit || state.hidden) return false;
  return state.idleMs >= BUBBLE_WAIT_MS;
}
