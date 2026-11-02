"use client";

import { memo, useRef } from "react";
import type { AgentId } from "@/lib/agent-avatars";
import {
  avatarPlan,
  decodeFrame,
  frameRects,
  gridForSize,
  haloFilter,
  type AvatarSize,
  type AvatarMotion,
  type AvatarState,
  type FrameSet,
  type SpriteGrid,
  type SpriteRect,
} from "@/lib/pixel-avatars";
import { PIXEL_DATA } from "@/lib/pixel-avatars-data";
import { useMotionAllowed } from "./useMotionAllowed";

export type { AvatarSize, AvatarState };

const cache = new Map<string, SpriteRect[]>();

/** Rectangles of one frame, decoded once per (agent, grid, frame set, frame). */
function rectsFor(agentId: AgentId, grid: SpriteGrid, frameSet: FrameSet, index: number): SpriteRect[] {
  const key = `${agentId}:${grid}:${frameSet}:${index}`;
  let rects = cache.get(key);
  if (!rects) {
    const data = PIXEL_DATA[agentId];
    // avatarPlan only asks for the working set on the 48 grid, where it exists.
    const frames = frameSet === "working" ? data.working["48"] : data.idle[grid === 32 ? "32" : "48"];
    rects = frameRects(decodeFrame(frames[index], grid), data.palette);
    cache.set(key, rects);
  }
  return rects;
}

/** Stable frame-index arrays per frame count (still: 1, idle loop: 4, working and wake: 8). */
const FRAME_INDEXES: Record<1 | 4 | 8, number[]> = {
  1: [0],
  4: [0, 1, 2, 3],
  8: [0, 1, 2, 3, 4, 5, 6, 7],
};

/** Classes per motion; the keyframes and timings live in app/globals.css. */
const MOTION_CLASS: Record<AvatarMotion, string> = {
  none: "",
  idle: " fs-px-anim",
  working: " fs-px-anim fs-px-work",
  wake: " fs-px-anim fs-px-wake",
};

/**
 * An agent is a pixel creature drawn straight onto the surface, with no tile, chip or box around
 * it, and never a circle, so it cannot be mistaken for a customer's round initials. Sizes are
 * whole-number multiples of the grid. On the night theme a one-cell light outline (the halo token)
 * keeps the darker body tones readable; it is transparent elsewhere and absent while dimmed. The server render, and every moment motion is not allowed (reduced motion, the pause
 * preference, a hidden tab, an offscreen avatar), is idle frame 0 alone: the rest pose.
 * `working` loops the eight working frames (48 grid sizes only); `wake` plays them once and holds
 * the last until the parent sets the state back to `idle`. On 32 grid sizes both play the idle loop.
 */
function AgentAvatar({
  agentId,
  size = 48,
  label = "",
  state = "idle",
}: {
  agentId: AgentId;
  size?: AvatarSize;
  /** Empty when the same agent name is already printed beside the avatar. */
  label?: string;
  state?: AvatarState;
}) {
  const root = useRef<HTMLSpanElement>(null);
  const motionAllowed = useMotionAllowed(root);
  const grid = gridForSize(size);
  const plan = avatarPlan(state, motionAllowed, grid);
  const frames = FRAME_INDEXES[plan.frameCount];
  const accessible = label ? { role: "img", "aria-label": label } : { "aria-hidden": true };
  return (
    <span
      ref={root}
      data-agent={agentId}
      data-state={state}
      style={{
        display: "inline-flex",
        flex: "0 0 auto",
        alignItems: "center",
        justifyContent: "center",
        verticalAlign: "middle",
        width: size,
        height: size,
        filter: haloFilter(size, plan.dimmed),
      }}
    >
      <svg
        {...accessible}
        className={`fs-px${MOTION_CLASS[plan.motion]}${plan.dimmed ? " fs-px-dim" : ""}`}
        viewBox={`0 0 ${grid} ${grid}`}
        width={size}
        height={size}
        shapeRendering="crispEdges"
        focusable="false"
      >
        {frames.map((index) => (
          // Keyed by set and motion, so a wake that hands over to idle starts fresh elements.
          <g key={`${plan.frameSet}:${plan.motion}:${index}`} className={`fs-px-frame fs-px-f${index}`}>
            {rectsFor(agentId, grid, plan.frameSet, index).map((rect) => (
              <rect key={`${rect.y}:${rect.x}`} x={rect.x} y={rect.y} width={rect.w} height={1} fill={rect.fill} />
            ))}
          </g>
        ))}
      </svg>
    </span>
  );
}

export default memo(AgentAvatar);
