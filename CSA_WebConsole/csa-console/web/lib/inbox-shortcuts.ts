/**
 * Inbox keyboard shortcuts. Navigation only: approve, edit and reject never get one.
 * Pure on purpose (no imports), so node --test can load it.
 */
export type Shortcut = "next" | "previous" | "close";

export type KeyLike = {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  target: { tagName?: string; isContentEditable?: boolean } | null;
};

const TYPING_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/**
 * Which shortcut, if any, a key press means. Ignored while typing in a field, with a
 * modifier held, and while a dialog or slide-over is open (it handles its own Escape).
 */
export function shortcutFor(
  event: KeyLike,
  context: { dialogOpen: boolean },
): Shortcut | null {
  if (context.dialogOpen) return null;
  if (event.ctrlKey || event.metaKey || event.altKey) return null;
  const target = event.target;
  if (target) {
    if (target.isContentEditable === true) return null;
    if (target.tagName && TYPING_TAGS.has(target.tagName.toUpperCase())) return null;
  }
  switch (event.key) {
    case "j":
    case "J":
      return "next";
    case "k":
    case "K":
      return "previous";
    case "Escape":
      return "close";
    default:
      return null;
  }
}
