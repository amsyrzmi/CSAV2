"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { CircleUser, LogOut } from "lucide-react";
import { PAUSE_KEY, parsePause } from "@/lib/motion";
import { PAUSE_EVENT, setMotionPaused } from "@/components/console/fs/useMotionAllowed";
import { BAR_BUTTON, POPOVER } from "./ThemeControl";
import { useDismiss } from "./useDismiss";

export interface ShellAccount {
  name: string;
  role: string;
  busy: boolean;
  onSignOut: () => void;
}

function storedPause(): boolean {
  try {
    return parsePause(window.localStorage.getItem(PAUSE_KEY));
  } catch {
    return false;
  }
}

/**
 * The signed-in person: name, role, the Calm motion switch, and (below 1024 px, where the
 * top bar has no room for it) Sign out. From 1024 px Sign out is its own button in the bar,
 * so it is one click and keeps working for tests and habits that expect it on the page; the
 * copy inside this menu is `lg:hidden`, so the page never has two visible Sign out buttons.
 */
export function UserMenu({ account }: { account: ShellAccount }) {
  const [open, setOpen] = useState(false);
  const [paused, setPaused] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const noteId = useId();
  const close = useCallback(() => setOpen(false), []);
  const dismiss = useDismiss({ open, rootRef, triggerRef, onClose: close });

  // The preference lives in localStorage, so read it when the menu opens, not during render.
  function toggleMenu() {
    if (!open) setPaused(storedPause());
    setOpen(!open);
  }

  // Keep the switch honest if the preference changes elsewhere while the menu is open.
  useEffect(() => {
    if (!open) return;
    const sync = (event?: Event) => {
      if (event instanceof StorageEvent && event.key !== null && event.key !== PAUSE_KEY) return;
      setPaused(storedPause());
    };
    window.addEventListener("storage", sync);
    window.addEventListener(PAUSE_EVENT, sync);
    return () => {
      window.removeEventListener("storage", sync);
      window.removeEventListener(PAUSE_EVENT, sync);
    };
  }, [open]);

  function toggleMotion() {
    const next = !paused;
    setMotionPaused(next);
    setPaused(next);
  }

  return (
    <div ref={rootRef} className="relative" onKeyDown={dismiss.onKeyDown}>
      <button
        ref={triggerRef}
        type="button"
        data-testid="shell-user-menu"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={toggleMenu}
        className={`${BAR_BUTTON} max-w-52`}
      >
        <CircleUser size={18} aria-hidden="true" className="shrink-0" />
        <span className="sr-only sm:not-sr-only sm:truncate">
          <span className="sr-only">Account menu, </span>
          {account.name}
        </span>
      </button>
      {open && (
        <div
          id={panelId}
          role="group"
          aria-label="Account"
          data-testid="shell-user-menu-panel"
          className={`${POPOVER} w-72 max-w-[calc(100vw-2rem)] p-4`}
        >
          <p className="break-words text-sm font-semibold">{account.name}</p>
          <p className="mt-0.5 text-sm text-[color:var(--fs-text-secondary)]">{account.role}</p>
          <div className="mt-4 flex items-center justify-between gap-4 border-t border-[color:var(--fs-border)] pt-4">
            <div className="min-w-0">
              <span id={`${noteId}-label`} className="block text-sm font-medium">
                Calm motion
              </span>
              <span id={noteId} className="mt-0.5 block text-xs text-[color:var(--fs-text-secondary)]">
                Pauses animated decoration in the inbox, side panels and agent avatars. Nothing on the server pauses.
              </span>
            </div>
            <button
              type="button"
              role="switch"
              data-testid="shell-motion-toggle"
              aria-checked={paused}
              aria-labelledby={`${noteId}-label`}
              aria-describedby={noteId}
              onClick={toggleMotion}
              className="relative h-6 w-11 shrink-0 rounded-full border border-[color:var(--fs-border-control)] bg-[var(--fs-tile)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] aria-checked:border-[color:var(--fs-primary)] aria-checked:bg-[var(--fs-primary)] pointer-coarse:h-7 pointer-coarse:w-12"
            >
              <span
                aria-hidden="true"
                className={`absolute top-0.5 left-0.5 h-4 w-4 rounded-full pointer-coarse:h-5 pointer-coarse:w-5 ${paused ? "translate-x-5 bg-[var(--fs-on-primary)]" : "bg-[var(--fs-text-secondary)]"} transition-transform duration-150`}
              />
            </button>
          </div>
          <SignOutButton
            account={account}
            className="mt-4 flex min-h-11 w-full items-center justify-center gap-2 rounded-[var(--fs-radius-control)] border border-[color:var(--fs-border-control)] text-sm font-medium hover:bg-[var(--fs-tile)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] disabled:opacity-60 lg:hidden"
            onBefore={() => {
              setOpen(false);
              triggerRef.current?.focus();
            }}
          />
        </div>
      )}
    </div>
  );
}

export function SignOutButton({
  account,
  className,
  onBefore,
}: {
  account: ShellAccount;
  className: string;
  onBefore?: () => void;
}) {
  return (
    <button
      type="button"
      disabled={account.busy}
      onClick={() => {
        onBefore?.();
        account.onSignOut();
      }}
      className={className}
    >
      <LogOut size={16} aria-hidden="true" />
      {account.busy ? "Signing out…" : "Sign out"}
    </button>
  );
}
