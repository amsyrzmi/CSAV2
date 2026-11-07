/**
 * Pure rules for the slides viewer: which page to show and what a key does. Pages count from 1.
 * No React, no pdf.js, no I/O.
 */

export type SlideAction = "next" | "previous" | "first" | "last" | "fullscreen";

/** A whole page between 1 and `total`; page 1 while the deck has no pages yet. */
export function clampPage(page: number, total: number): number {
  const last = Number.isFinite(total) && total >= 1 ? Math.floor(total) : 1;
  const whole = Number.isFinite(page) ? Math.floor(page) : 1;
  return Math.min(Math.max(whole, 1), last);
}

export function nextPage(page: number, total: number): number {
  return clampPage(clampPage(page, total) + 1, total);
}

export function previousPage(page: number, total: number): number {
  return clampPage(clampPage(page, total) - 1, total);
}

/** The page after an action; fullscreen leaves the page where it is (the viewer handles it). */
export function pageForAction(action: SlideAction, page: number, total: number): number {
  switch (action) {
    case "next":
      return nextPage(page, total);
    case "previous":
      return previousPage(page, total);
    case "first":
      return 1;
    case "last":
      return clampPage(total, total);
    default:
      return clampPage(page, total);
  }
}

const KEYS: ReadonlyMap<string, SlideAction> = new Map<string, SlideAction>([
  ["ArrowRight", "next"],
  ["ArrowDown", "next"],
  ["PageDown", "next"],
  ["ArrowLeft", "previous"],
  ["ArrowUp", "previous"],
  ["PageUp", "previous"],
  ["Home", "first"],
  ["End", "last"],
  ["f", "fullscreen"],
  ["F", "fullscreen"],
]);

export interface KeyModifiers {
  ctrlKey?: boolean;
  metaKey?: boolean;
  altKey?: boolean;
  shiftKey?: boolean;
}

/**
 * The slide action for a `KeyboardEvent.key`, or null. A key held with Ctrl, Cmd, Alt or Shift
 * is left to the browser (zoom, history, text selection), so it never moves the deck.
 */
export function keyToAction(key: string, modifiers: KeyModifiers = {}): SlideAction | null {
  if (modifiers.ctrlKey || modifiers.metaKey || modifiers.altKey || modifiers.shiftKey) return null;
  return KEYS.get(key) ?? null;
}
