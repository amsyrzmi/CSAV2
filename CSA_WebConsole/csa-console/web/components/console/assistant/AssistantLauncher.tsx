"use client";

import { useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import { X } from "lucide-react";
import AgentAvatar from "../fs/AgentAvatar";
import { useMotionAllowed } from "../fs/useMotionAllowed";
import { action } from "../ui";
import { corner } from "./looks";

/** How long the avatar stays awake after the pointer or keyboard focus arrives (its wake loop). */
const WAKE_MS = 1500;
/** How long the click hop class stays on the button; matches `fs-dock-hop` in globals.css. */
const HOP_MS = 400;
/** The click opens the panel at the top of the hop, so the hop shows before the panel replaces it. */
const OPEN_AFTER_HOP_MS = 160;

/**
 * The closed dock: a button showing the assistant's own pixel avatar beside its name. The avatar
 * works while an answer is pending, wakes for a moment when the pointer or keyboard focus arrives,
 * and the button hops once when pressed. Every motion goes through the one motion gate
 * (`useMotionAllowed`): when it says no, the avatar draws its rest frame and neither the wake
 * state nor the hop class is ever set. The avatar is decorative, so the button's name is `label`.
 *
 * When the dock has an `idea` to offer, a small bubble sits above the button: a button of its own
 * (after the launcher in DOM order, named by the idea text) and a Dismiss button. It is not a
 * live region and takes no focus by itself. Clicking the idea hands it to `onIdea` at once (no
 * hop); Escape inside the bubble dismisses it.
 */
export function AssistantLauncher({
  label,
  busy,
  panelId,
  onOpen,
  buttonRef,
  idea,
  dismissLabel,
  onIdea,
  onDismissIdea,
}: {
  label: string;
  busy: boolean;
  panelId: string;
  onOpen(): void;
  buttonRef: Ref<HTMLButtonElement>;
  idea: { id: string; text: string } | null;
  /** The accessible name of the bubble's Dismiss button, in the conversation's language. */
  dismissLabel: string;
  onIdea(idea: { id: string; text: string }): void;
  onDismissIdea(id: string): void;
}) {
  const own = useRef<HTMLButtonElement>(null);
  const motion = useMotionAllowed(own);
  const [awake, setAwake] = useState(false);
  const [hopping, setHopping] = useState(false);
  const wakeTimer = useRef<number | undefined>(undefined);
  const hopTimer = useRef<number | undefined>(undefined);
  const openTimer = useRef<number | undefined>(undefined);

  // The caller's ref (focus return) and the motion gate share the one button.
  useImperativeHandle(buttonRef, () => own.current as HTMLButtonElement);

  useEffect(
    () => () => {
      window.clearTimeout(wakeTimer.current);
      window.clearTimeout(hopTimer.current);
      window.clearTimeout(openTimer.current);
    },
    [],
  );

  function wake() {
    if (!motion) return;
    window.clearTimeout(wakeTimer.current);
    setAwake(true);
    wakeTimer.current = window.setTimeout(() => setAwake(false), WAKE_MS);
  }

  function press() {
    if (!motion) {
      onOpen();
      return;
    }
    if (hopping) return;
    setHopping(true);
    hopTimer.current = window.setTimeout(() => setHopping(false), HOP_MS);
    openTimer.current = window.setTimeout(onOpen, OPEN_AFTER_HOP_MS);
  }

  function dismiss(id: string) {
    onDismissIdea(id);
    // The bubble is about to leave the page: keep keyboard focus on the dock instead of losing it.
    own.current?.focus();
  }

  const state = busy ? "working" : awake && motion ? "wake" : "idle";
  // The wrapper is the fixed corner; reversed, so the bubble sits above the button while the
  // button stays first in DOM order (and so first in the tab order).
  return (
    <div className={`fixed ${corner} z-40 flex flex-col-reverse items-end gap-2`}>
      <button
        ref={own}
        type="button"
        data-testid="assistant-open"
        aria-expanded={false}
        aria-controls={panelId}
        onClick={press}
        onPointerEnter={wake}
        onFocus={wake}
        className={`${action} rounded-full py-0 pl-1.5 shadow-[var(--fs-shadow-popover)]${hopping && motion ? " fs-dock-hop" : ""}`}
      >
        <AgentAvatar agentId="assistant" size={48} state={state} label="" />
        {label}
      </button>
      {idea && (
        <div
          data-testid="assistant-idea"
          onKeyDown={(event) => {
            if (event.key === "Escape") dismiss(idea.id);
          }}
          className="flex max-w-[min(18rem,calc(100vw-2.5rem))] items-start rounded-[var(--fs-radius-tile)] border border-[color:var(--fs-border)] bg-[var(--fs-surface-raised)] text-[color:var(--fs-text)] shadow-[var(--fs-shadow-popover)]"
        >
          <button
            type="button"
            data-testid="assistant-idea-open"
            onClick={() => onIdea(idea)}
            className="min-w-0 flex-1 rounded-[var(--fs-radius-tile)] px-3.5 py-2.5 text-left text-sm leading-snug transition-colors duration-150 hover:bg-[var(--fs-surface-blue)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] pointer-coarse:py-3"
          >
            {idea.text}
          </button>
          <button
            type="button"
            data-testid="assistant-idea-dismiss"
            aria-label={dismissLabel}
            onClick={() => dismiss(idea.id)}
            className="inline-grid size-9 shrink-0 place-items-center rounded-[var(--fs-radius-round)] text-[color:var(--fs-text-secondary)] transition-colors duration-150 hover:bg-[var(--fs-surface-blue)] hover:text-[color:var(--fs-text)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] pointer-coarse:size-11"
          >
            <X size={16} aria-hidden />
          </button>
        </div>
      )}
    </div>
  );
}
