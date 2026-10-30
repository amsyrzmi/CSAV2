"use client";

import { useSyncExternalStore } from "react";
import AgentAvatar from "./AgentAvatar";
import { useMotionAllowed } from "./useMotionAllowed";
import type { AgentId } from "@/lib/agent-avatars";

/** The walking row, left to right. */
const WALKERS: readonly AgentId[] = ["support", "orders", "tickets", "bookings", "knowledge"];

const subscribeNever = () => () => {};

/**
 * The route-level loading screen: five agents bob in a staggered wave over a scrolling dashed
 * ground line, with the word "Loading" and animated dots below. The sprites are the 96 px size
 * (the 48 grid at 2x); below 560 px CSS halves them to 48 px (the 48 grid at 1x, exact, so the
 * pixels stay crisp) so five fit, with no script and no hydration difference. The agents are in
 * the `rest` pose and a wrapper does the bobbing.
 *
 * When a full-screen load streams, this is the server-rendered fallback of a pending Suspense
 * boundary, which React does not hydrate until the page itself arrives. So the animation cannot
 * wait for a hook: `data-motion="auto"` (server render, and until mounted) lets the CSS in
 * app/globals.css animate unless the visitor prefers reduced motion or the root layout's first
 * script marked the document as Calm (`data-calm`, from the stored pause preference). Once
 * mounted, the hook decides and `data-motion` is "on" or "off" (reduced motion, a hidden tab,
 * the Calm preference). The word is always there, and the dots are a pseudo-element, so the
 * status text is exactly "Loading".
 */
export default function FullPageLoading({ fullScreen = false }: { fullScreen?: boolean }) {
  const allowed = useMotionAllowed();
  const mounted = useSyncExternalStore(subscribeNever, () => true, () => false);
  const motion = mounted ? (allowed ? "on" : "off") : "auto";
  return (
    <div
      role="status"
      data-testid="loading-screen"
      data-motion={motion}
      className={fullScreen ? "fs-load fs-load-full" : "fs-load"}
    >
      <div className="fs-load-block">
        <div className="fs-load-row">
          {WALKERS.map((agent, index) => (
            <div
              key={agent}
              data-testid="loading-walker"
              className="fs-load-walker"
              style={{ animationDelay: `${index * 0.1}s` }}
            >
              <div className="fs-load-sprite">
                <AgentAvatar agentId={agent} size={96} state="rest" />
              </div>
            </div>
          ))}
        </div>
        <div data-testid="loading-ground" className="fs-load-ground" aria-hidden="true" />
        <p data-testid="loading-word" className="fs-load-word">
          Loading
        </p>
      </div>
    </div>
  );
}
