import type { AvatarState } from "./pixel-avatars";

/**
 * Where a service or agent switch is in its life. The avatar only animates after the
 * server has confirmed a change: a click alone moves to `pending`, which is still and dimmed.
 */
export type Phase = "idle" | "still" | "pending" | "wake" | "settling";

export type TransitionEvent =
  | { type: "start" }
  | { type: "confirmed"; to: "active" | "inactive" }
  | { type: "failed" }
  | { type: "done" };

/** The look to restore after a failure; never an animation or an in-flight phase. */
function restingPhase(before: Phase | undefined): Phase {
  if (before === "idle" || before === "still") return before;
  if (before === "wake") return "idle";
  return "still";
}

/**
 * Next phase for an event. `confirmed` and `failed` only apply while a request is pending;
 * `done` only ends the two timed phases. `wake` is entered by a confirmed activation and by
 * nothing else. `before` is the phase when the request started (used by `failed`).
 */
export function nextPhase(
  current: Phase,
  event: TransitionEvent,
  motionAllowed: boolean,
  before?: Phase,
): Phase {
  switch (event.type) {
    case "start":
      return "pending";
    case "confirmed":
      if (current !== "pending") return current;
      if (event.to === "active") return motionAllowed ? "wake" : "idle";
      return motionAllowed ? "settling" : "still";
    case "failed":
      return current === "pending" ? restingPhase(before) : current;
    case "done":
      if (current === "wake") return "idle";
      if (current === "settling") return "still";
      return current;
  }
}

export function avatarStateForPhase(phase: Phase): AvatarState {
  return phase === "settling" ? "still" : phase;
}
