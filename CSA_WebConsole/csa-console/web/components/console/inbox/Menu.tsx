"use client";

import {
  useCallback, useEffect, useId, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode,
  type RefObject,
} from "react";
import { ChevronDown } from "lucide-react";
import { useDismiss } from "@/components/console/shell/useDismiss";
import { secondary } from "@/components/console/ui";
import { menuIndex } from "@/lib/shell-state";

export type Entry = {
  label: string;
  onSelect: () => void;
  /** A list key when the label may repeat; the label otherwise. */
  key?: string;
  /** Listed but not chosen: `aria-disabled`, still reached by the arrows, a press does nothing. */
  disabled?: boolean;
  /** A second line under the label, also the item's accessible description (why it is disabled). */
  note?: string;
  /** A `menuitemradio` (the one that applies is checked) when set; a plain `menuitem` otherwise. */
  checked?: boolean;
  /** The menu stays open (Try again in place); the items that replace it take focus. */
  keepOpen?: boolean;
  /** A thin line above this entry. */
  separated?: boolean;
};

const EDGE = 8;
const ITEM =
  "flex min-h-10 w-full flex-col items-start justify-center rounded-[var(--fs-radius-control)] px-3 py-1.5 text-left text-sm hover:bg-[var(--fs-tile)] focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[color:var(--fs-focus)] pointer-coarse:min-h-11 aria-disabled:cursor-not-allowed aria-disabled:text-[color:var(--fs-text-secondary)] aria-disabled:hover:bg-transparent aria-checked:font-semibold";

/**
 * A button and its `role="menu"` popover, shared by the thread's Snooze and More menus, the bulk
 * bar's and the queue bar's Views. Opening (click, or an arrow key on the button) puts focus on the
 * first item; the arrows, Home and End move it and wrap; Escape closes and returns focus to the
 * button, and so does choosing an item (a dialog it opens returns focus there too); Tab closes it. A
 * press or focus outside closes it. When the items change while it is open (a list arriving) and the
 * focused one is gone, focus moves to the first. The popover is `fixed` and placed from the button,
 * kept inside the screen and scrolling inside itself when tall, so no overflow clips it. Keys it
 * handles are marked handled, so the Inbox's J, K and Escape (Back on a phone) leave them alone.
 */
export function Menu({
  label, menuLabel, testId, disabled, entries, triggerRef, buttonName, buttonClass = "", width = 240, onOpen,
}: {
  label: ReactNode; menuLabel: string; testId: string; disabled: boolean; entries: Entry[];
  triggerRef: RefObject<HTMLButtonElement | null>;
  /** The button's accessible name when the visible label is not enough (it must contain it). */
  buttonName?: string;
  buttonClass?: string;
  width?: number;
  /** Called each time it opens, before the items show (a list read on first use). */
  onOpen?: () => void;
}) {
  const [open, setOpen] = useState(false);
  // A request that starts while it is open (a poll, another control) closes it.
  if (open && disabled) setOpen(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const noteId = useId();
  const [place, setPlace] = useState<{ top: number; left: number; width: number } | null>(null);
  const close = useCallback(() => setOpen(false), []);
  const dismiss = useDismiss({ open, rootRef, triggerRef, onClose: close });
  const shape = entries.map((entry) => entry.key ?? entry.label).join("\n");

  function show() {
    onOpen?.();
    setOpen(true);
  }

  useLayoutEffect(() => {
    if (!open) return;
    function measure() {
      const trigger = triggerRef.current;
      if (!trigger) return;
      const box = trigger.getBoundingClientRect();
      const viewport = document.documentElement.clientWidth;
      const size = Math.min(width, viewport - 2 * EDGE);
      // Right edges line up; moved left or right as far as needed to stay on screen.
      const left = Math.max(EDGE, Math.min(box.right - size, viewport - EDGE - size));
      const height = menuRef.current?.offsetHeight ?? 0;
      const below = box.bottom + 4;
      const above = box.top - 4 - height;
      const top = below + height > window.innerHeight - EDGE && above >= EDGE ? above : below;
      setPlace({ top, left, width: size });
    }
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open, triggerRef, width, shape]);

  // Focus moves in once the popover is placed (a hidden element cannot take focus), and back to the
  // first item when the item that had it went away.
  const placed = place !== null;
  useEffect(() => {
    if (!open || !placed) return;
    if (!menuRef.current?.contains(document.activeElement)) itemRefs.current[0]?.focus({ preventScroll: true });
  }, [open, placed, shape]);

  // React listens on the document here, so stopping propagation cannot keep a key from the Inbox's
  // own document listener: a key the menu handles is marked handled (defaultPrevented) instead.
  function onMenuKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Enter" || event.key === " ") return; // the item's own click
    if (event.key === "Tab") { setOpen(false); return; }
    event.preventDefault();
    if (event.key === "Escape") return; // the root's dismiss handler closes and refocuses
    event.stopPropagation();
    const at = itemRefs.current.findIndex((item) => item === document.activeElement);
    const next = menuIndex(event.key, Math.max(at, 0), entries.length);
    if (next !== null) itemRefs.current[next]?.focus();
  }

  return <div ref={rootRef} className="relative min-w-0" onKeyDown={(event) => {
    if (event.key === "Escape" && open) event.preventDefault();
    dismiss.onKeyDown(event);
  }}>
    <button ref={triggerRef} type="button" className={`${secondary} ${buttonClass}`} data-testid={testId}
      disabled={disabled} aria-haspopup="menu" aria-expanded={open} aria-label={buttonName}
      onClick={() => { if (open) setOpen(false); else show(); }}
      onKeyDown={(event) => {
        if (!open && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
          event.preventDefault();
          show();
        }
      }}>
      {label}<ChevronDown aria-hidden size={16} className="shrink-0" />
    </button>
    {open && <div ref={menuRef} role="menu" aria-label={menuLabel} onKeyDown={onMenuKey}
      style={place ? { top: place.top, left: place.left, width: place.width }
        : { top: 0, left: 0, width, visibility: "hidden" }}
      className="fixed z-40 max-h-[min(28rem,calc(100dvh-1rem))] overflow-y-auto overscroll-contain rounded-xl border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-1 text-[color:var(--fs-text)] shadow-[var(--fs-shadow-popover)]">
      {entries.map((entry, index) => {
        const note = entry.note ? `${noteId}-${index}` : undefined;
        return <div key={entry.key ?? entry.label} role="none"
          className={entry.separated ? "mt-1 border-t border-[color:var(--fs-border)] pt-1" : undefined}>
          <button type="button" role={entry.checked === undefined ? "menuitem" : "menuitemradio"}
            aria-checked={entry.checked} aria-disabled={entry.disabled || undefined} aria-describedby={note}
            tabIndex={-1} ref={(element) => { itemRefs.current[index] = element; }} className={ITEM}
            onClick={() => {
              if (entry.disabled) return;
              if (entry.keepOpen) { entry.onSelect(); return; }
              setOpen(false);
              triggerRef.current?.focus();
              entry.onSelect();
            }}>
            <span data-label className="min-w-0 max-w-full [overflow-wrap:anywhere]">{entry.label}</span>
            {note && <span id={note} className="max-w-full text-xs font-normal text-[color:var(--fs-text-secondary)] [overflow-wrap:anywhere]">
              {entry.note}</span>}
          </button>
        </div>;
      })}
    </div>}
  </div>;
}
