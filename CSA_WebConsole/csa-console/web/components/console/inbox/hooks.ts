"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

/**
 * Epoch milliseconds, refreshed every `ms`. Starts at 0 on the server and before mount;
 * the inbox only renders times for rows it fetched after mount, so nothing mismatches.
 */
export function useTicker(ms: number): number {
  const [now, setNow] = useState(0);
  useEffect(() => {
    const tick = () => setNow(Date.now());
    const first = setTimeout(tick, 0);
    const timer = setInterval(tick, ms);
    return () => { clearTimeout(first); clearInterval(timer); };
  }, [ms]);
  return now;
}

/**
 * Whether a media query matches. False on the server and during hydration, so layout that
 * must be right on first paint stays in CSS; this only decides where a single copy of a
 * piece (the customer card) lives once the page is interactive.
 */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      if (typeof window.matchMedia !== "function") return () => {};
      const media = window.matchMedia(query);
      media.addEventListener("change", onChange);
      return () => media.removeEventListener("change", onChange);
    },
    () => typeof window.matchMedia === "function" && window.matchMedia(query).matches,
    () => false,
  );
}

/** Three panes from here (spec 7.2); below it the customer card is a slide-over. */
export const WIDE_QUERY = "(min-width: 68.75rem)"; // 1100 px, matching the CSS `min-[68.75rem]`
/** Below `md`: the list and the thread are separate full-width views. */
export const PHONE_QUERY = "(max-width: 767.98px)";
