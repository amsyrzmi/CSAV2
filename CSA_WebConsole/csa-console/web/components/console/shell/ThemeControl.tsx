"use client";

import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from "react";
import { Check, Palette } from "lucide-react";
import { menuIndex } from "@/lib/shell-state";
import { THEMES, parseTheme, writeTheme, type Theme } from "@/lib/theme";
import { useDismiss } from "./useDismiss";

const LABELS: Record<Theme, string> = { cobalt: "Cobalt (light)", night: "Night (dark)", bureau: "Bureau (warm)" };

export const BAR_BUTTON =
  "flex h-10 min-w-10 items-center justify-center gap-2 rounded-[var(--fs-radius-control)] border border-[color:var(--fs-border-control)] px-2.5 text-sm font-medium text-[color:var(--fs-text)] hover:bg-[var(--fs-tile)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] pointer-coarse:h-11 pointer-coarse:min-w-11";
export const POPOVER =
  "absolute right-0 top-full z-40 mt-2 rounded-xl border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-1 text-[color:var(--fs-text)] shadow-[var(--fs-shadow-popover)]";

/**
 * Chooses the console theme. A menu of three radio items: Enter, Space or ArrowDown on the
 * button opens it with focus on the current theme, arrows/Home/End move, Enter or Space picks,
 * Escape closes and returns focus to the button. `writeTheme` sets the cookie (which the server
 * reads on the next load), the localStorage mirror and the attribute, so there is no reload.
 */
export function ThemeControl() {
  const [open, setOpen] = useState(false);
  const [current, setCurrent] = useState<Theme>("cobalt");
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const close = useCallback(() => setOpen(false), []);
  const dismiss = useDismiss({ open, rootRef, triggerRef, onClose: close });

  function show() {
    setCurrent(parseTheme(document.documentElement.dataset.theme));
    setOpen(true);
  }

  useEffect(() => {
    if (open) itemRefs.current[THEMES.indexOf(current)]?.focus();
  }, [open, current]);

  function choose(theme: Theme) {
    writeTheme(theme);
    setCurrent(theme);
    setOpen(false);
    triggerRef.current?.focus();
  }

  function onMenuKey(event: KeyboardEvent) {
    const at = itemRefs.current.findIndex((item) => item === document.activeElement);
    const next = menuIndex(event.key, Math.max(at, 0), THEMES.length);
    if (next === null) return;
    event.preventDefault();
    itemRefs.current[next]?.focus();
  }

  return (
    <div ref={rootRef} className="relative" onKeyDown={dismiss.onKeyDown}>
      <button
        ref={triggerRef}
        type="button"
        data-testid="shell-theme"
        aria-label="Colour theme"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => (open ? setOpen(false) : show())}
        onKeyDown={(event) => {
          if (!open && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
            event.preventDefault();
            show();
          }
        }}
        className={BAR_BUTTON}
      >
        <Palette size={18} aria-hidden="true" />
      </button>
      {open && (
        <div role="menu" aria-label="Colour theme" onKeyDown={onMenuKey} className={`${POPOVER} w-44`}>
          {THEMES.map((theme, index) => (
            <button
              key={theme}
              ref={(element) => {
                itemRefs.current[index] = element;
              }}
              type="button"
              role="menuitemradio"
              aria-checked={theme === current}
              tabIndex={theme === current ? 0 : -1}
              onClick={() => choose(theme)}
              className="flex min-h-10 w-full items-center justify-between gap-3 rounded-[var(--fs-radius-control)] px-3 text-left text-sm hover:bg-[var(--fs-tile)] focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[color:var(--fs-focus)] aria-checked:font-semibold pointer-coarse:min-h-11"
            >
              {LABELS[theme]}
              {theme === current && (
                <Check size={16} aria-hidden="true" className="text-[color:var(--fs-primary)]" />
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
