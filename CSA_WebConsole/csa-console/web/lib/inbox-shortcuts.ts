/**
 * Inbox keyboard shortcuts. Approve, edit and reject never get one; claim, resolve and snooze act
 * on the open conversation through the same buttons a click uses.
 * Pure on purpose (no imports), so node --test can load it.
 */
export type Shortcut =
  | "next"
  | "previous"
  | "close"
  | "toggle"
  | "claim"
  | "resolve"
  | "snooze"
  | "search"
  | "help";

export type KeyLike = {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  /** A key held down sends repeats: only J and K act on them. */
  repeat?: boolean;
  target: {
    tagName?: string;
    isContentEditable?: boolean;
    /** The DOM's `Element.closest`, so a key pressed inside a dialog, popover or menu is left to it. */
    closest?: (selector: string) => unknown;
  } | null;
};

const TYPING_TAGS = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/** Anything that handles its own keys: native dialogs, slide-overs, popovers and menus. */
export const OVERLAY_SELECTOR = 'dialog, [role="dialog"], [role="alertdialog"], [role="menu"]';

/** An overlay open anywhere on the page (closed native dialogs stay in the page, so only open ones). */
export const OPEN_OVERLAY_SELECTOR = 'dialog[open], [role="dialog"]:not(dialog), [role="alertdialog"]:not(dialog), [role="menu"]';

const KEYS: Record<string, Shortcut> = {
  j: "next",
  k: "previous",
  x: "toggle",
  c: "claim",
  e: "resolve",
  s: "snooze",
  "/": "search",
  "?": "help",
  escape: "close",
};

/**
 * Which shortcut, if any, a key press means. Ignored while typing in a field, with a modifier held,
 * from inside a dialog, popover or menu, and while one is open (`dialogOpen`: it handles its own
 * keys, Escape included). Shift is not a modifier here: "?" is Shift and slash on most keyboards.
 * Holding a key repeats only J and K, so an action never runs twice from one press.
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
    if (target.closest?.(OVERLAY_SELECTOR)) return null;
  }
  if (event.key.length !== 1 && event.key !== "Escape") return null;
  const shortcut = KEYS[event.key.toLowerCase()] ?? null;
  if (event.repeat && shortcut !== "next" && shortcut !== "previous") return null;
  return shortcut;
}

/** The help dialog's list, in words: the key as shown, its spoken name and what it does. */
export const SHORTCUT_HELP: readonly { key: string; name: string; text: string }[] = [
  { key: "J", name: "J", text: "Open the next conversation in the list." },
  { key: "K", name: "K", text: "Open the previous conversation in the list." },
  { key: "X", name: "X", text: "Select or clear the conversation your focus is on, to act on several at once." },
  { key: "C", name: "C", text: "Take the open conversation, when Take it is offered." },
  { key: "E", name: "E", text: "Resolve the open conversation, when Resolve is offered." },
  { key: "S", name: "S", text: "Open the Snooze menu of the open conversation." },
  { key: "/", name: "Slash", text: "Go to the search box." },
  { key: "?", name: "Question mark", text: "Show this list of shortcuts." },
  { key: "Escape", name: "Escape", text: "Clear the selection, or on a phone go back to the list." },
];
