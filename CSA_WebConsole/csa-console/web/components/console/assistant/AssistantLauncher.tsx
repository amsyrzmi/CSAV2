"use client";

import { useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
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
 */
export function AssistantLauncher({
  label,
  busy,
  panelId,
  onOpen,
  buttonRef,
}: {
  label: string;
  busy: boolean;
  panelId: string;
  onOpen(): void;
  buttonRef: Ref<HTMLButtonElement>;
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

  const state = busy ? "working" : awake && motion ? "wake" : "idle";
  return (
    <button
      ref={own}
      type="button"
      data-testid="assistant-open"
      aria-expanded={false}
      aria-controls={panelId}
      onClick={press}
      onPointerEnter={wake}
      onFocus={wake}
      className={`${action} fixed ${corner} z-40 rounded-full py-0 pl-1.5 shadow-[var(--fs-shadow-popover)]${hopping && motion ? " fs-dock-hop" : ""}`}
    >
      <AgentAvatar agentId="assistant" size={48} state={state} label="" />
      {label}
    </button>
  );
}
