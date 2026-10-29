"use client";

import { memo, useRef } from "react";
import type { AgentId } from "@/lib/agent-avatars";
import {
  avatarPlan,
  decodeFrame,
  frameRects,
  gridForSize,
  type AvatarSize,
  type AvatarState,
  type SpriteGrid,
  type SpriteRect,
} from "@/lib/pixel-avatars";
import { PIXEL_DATA } from "@/lib/pixel-avatars-data";
import { useMotionAllowed } from "./useMotionAllowed";

export type { AvatarSize, AvatarState };

const cache = new Map<string, SpriteRect[]>();

/** Rectangles of one frame, decoded once per (agent, grid, frame set, frame). */
function rectsFor(agentId: AgentId, grid: SpriteGrid, frameSet: "idle", index: number): SpriteRect[] {
  const key = `${agentId}:${grid}:${frameSet}:${index}`;
  let rects = cache.get(key);
  if (!rects) {
    const data = PIXEL_DATA[agentId];
    rects = frameRects(decodeFrame(data[frameSet][grid === 32 ? "32" : "48"][index], grid), data.palette);
    cache.set(key, rects);
  }
  return rects;
}

/** Stable arrays: the idle loop is four frames; everything else shows frame 0 alone. */
const FIRST_FRAME_ONLY = [0];
const ALL_FRAMES = [0, 1, 2, 3];

/**
 * An agent is a pixel creature on a rounded tile, never a circle, so it cannot be mistaken for a
 * customer's round initials. The sprite fills the tile; sizes are whole-number multiples of the
 * grid. The server render, and every moment motion is not allowed (reduced motion, the pause
 * preference, a hidden tab, an offscreen avatar), is frame 0 alone: the rest pose.
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
  const tile = useRef<HTMLSpanElement>(null);
  const motionAllowed = useMotionAllowed(tile);
  const grid = gridForSize(size);
  const plan = avatarPlan(state, motionAllowed);
  const frames = plan.animate ? ALL_FRAMES : FIRST_FRAME_ONLY;
  const accessible = label ? { role: "img", "aria-label": label } : { "aria-hidden": true };
  return (
    <span
      ref={tile}
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
        borderRadius: 10,
        background: "var(--fs-tile)",
        overflow: "hidden",
      }}
    >
      <svg
        {...accessible}
        className={`fs-px${plan.animate ? " fs-px-anim" : ""}${plan.dimmed ? " fs-px-dim" : ""}`}
        viewBox={`0 0 ${grid} ${grid}`}
        width={size}
        height={size}
        shapeRendering="crispEdges"
        focusable="false"
      >
        {frames.map((index) => (
          <g key={index} className={`fs-px-frame fs-px-f${index}`}>
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
