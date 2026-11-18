"use client";
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";

/**
 * Where focus goes after a write, so it never falls to the page body: the button the person chose
 * (`opener`) when it is still there and enabled, else the message (`notice`), else the panel's
 * heading. A write disables its buttons while it runs, so the move waits until `settled` (no write
 * in flight and no reload running). The returned function asks for the move; it applies to writes
 * that reload and to those that do not.
 */
export function useReturnFocus(settled: boolean, notice: RefObject<HTMLElement | null>,
  panel: RefObject<HTMLElement | null>) {
  const pending = useRef<{ opener: HTMLElement | null } | null>(null);
  const [asked, setAsked] = useState(0);
  useEffect(() => {
    if (!settled || !pending.current) return;
    const { opener } = pending.current;
    pending.current = null;
    const usable = opener && opener.isConnected && !(opener instanceof HTMLButtonElement && opener.disabled)
      && opener.getClientRects().length > 0;
    if (usable) { opener.focus(); return; }
    if (notice.current) { notice.current.focus(); return; }
    const heading = panel.current?.querySelector("h2");
    if (heading) { heading.tabIndex = -1; heading.focus(); }
  }, [settled, asked, notice, panel]);
  return useCallback((opener: HTMLElement | null) => {
    pending.current = { opener };
    setAsked(count => count + 1);
  }, []);
}
