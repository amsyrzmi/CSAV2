"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  avatarStateForPhase,
  nextPhase,
  type Phase,
  type TransitionEvent,
} from "@/lib/avatar-transition";
import type { AvatarState } from "@/lib/pixel-avatars";
import { useMotionAllowed } from "./useMotionAllowed";

const WAKE_MS = 1500;
const SETTLE_MS = 400;

export interface AvatarTransition {
  phase: Phase;
  state: AvatarState;
  start(): void;
  confirm(to: "active" | "inactive"): void;
  fail(): void;
}

/**
 * Drives one avatar through a switch: `start()` when the request is sent (still, dimmed),
 * then `confirm(...)` only once the API call has resolved with the new state, or `fail()`.
 * Only a confirmation animates (a short wake after activation, a settle after suspension).
 *
 * If the server state changes WITHOUT a user action here (for example the 3-second poll
 * finds the service active), do not call `confirm`: re-initialise from the server state
 * instead, so no wake plays for a change nobody confirmed in this tab.
 */
export function useAvatarTransition(initial: Phase): AvatarTransition {
  const motionAllowed = useMotionAllowed();
  const [phase, setPhase] = useState<Phase>(initial);
  const phaseRef = useRef<Phase>(initial);
  const beforeRef = useRef<Phase | undefined>(undefined);
  const motionRef = useRef(motionAllowed);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    motionRef.current = motionAllowed;
  }, [motionAllowed]);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) clearTimeout(timerRef.current);
    timerRef.current = null;
  }, []);

  useEffect(() => clearTimer, [clearTimer]);

  const apply = useCallback((event: TransitionEvent): Phase => {
    const next = nextPhase(
      phaseRef.current, event, motionRef.current, beforeRef.current,
    );
    phaseRef.current = next;
    setPhase(next);
    return next;
  }, []);

  const start = useCallback(() => {
    clearTimer();
    // A repeated start keeps the original look to return to.
    if (phaseRef.current !== "pending") beforeRef.current = phaseRef.current;
    apply({ type: "start" });
  }, [apply, clearTimer]);

  const confirm = useCallback(
    (to: "active" | "inactive") => {
      const wasPending = phaseRef.current === "pending";
      const next = apply({ type: "confirmed", to });
      if (!wasPending || (next !== "wake" && next !== "settling")) return;
      clearTimer();
      timerRef.current = setTimeout(
        () => apply({ type: "done" }),
        next === "wake" ? WAKE_MS : SETTLE_MS,
      );
    },
    [apply, clearTimer],
  );

  const fail = useCallback(() => {
    apply({ type: "failed" });
  }, [apply]);

  return { phase, state: avatarStateForPhase(phase), start, confirm, fail };
}
