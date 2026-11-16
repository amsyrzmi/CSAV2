"use client";

import { useEffect, useState } from "react";
import { BUBBLE_WAIT_MS } from "@/lib/assistant-bubble";

/** What counts as the person being there: the same signals a person gives by using the page. */
const ACTIVITY = ["pointermove", "pointerdown", "keydown", "scroll", "touchstart"] as const;
/** How often the idle time is looked at, and the step it is reported in. */
const TICK_MS = 1000;

/**
 * Milliseconds since the person last moved the pointer, pressed a key, scrolled or touched the
 * page, in whole seconds and never more than `BUBBLE_WAIT_MS` (nothing here needs to tell a longer
 * quiet from the wait itself, so the value stops changing and the component stops re-rendering).
 *
 * The clock does not run while the tab is hidden: when it is shown again the quiet starts over, so
 * a person returning to the tab is not greeted at once. A change of `restartKey` (the dock passes
 * the page path) also starts the quiet over, for a move that is not itself an input, such as the
 * browser's back button.
 */
export function useIdleTimer(restartKey: string): number {
  const [idleMs, setIdleMs] = useState(0);
  const [seenKey, setSeenKey] = useState(restartKey);
  if (seenKey !== restartKey) {
    setSeenKey(restartKey);
    setIdleMs(0);
  }

  useEffect(() => {
    let last = Date.now();
    const restart = () => {
      last = Date.now();
      setIdleMs(0);
    };
    const tick = () => {
      if (document.visibilityState === "hidden") return;
      const idle = Math.min(BUBBLE_WAIT_MS, Math.floor((Date.now() - last) / TICK_MS) * TICK_MS);
      setIdleMs(idle);
    };
    const shown = () => {
      if (document.visibilityState === "visible") restart();
    };
    for (const name of ACTIVITY)
      document.addEventListener(name, restart, { capture: true, passive: true });
    document.addEventListener("visibilitychange", shown);
    const timer = window.setInterval(tick, TICK_MS);
    return () => {
      for (const name of ACTIVITY) document.removeEventListener(name, restart, { capture: true });
      document.removeEventListener("visibilitychange", shown);
      window.clearInterval(timer);
    };
    // Re-run on a new key: the closure's `last` starts again from now.
  }, [restartKey]);

  return idleMs;
}
