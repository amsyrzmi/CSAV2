"use client";

import { useMotionReport } from "@/components/console/fs/useMotionAllowed";

/** The four stacked layers of public/brand/fourstacks-mark.svg, bottom to top. */
const LAYERS = [
  { d: "m4 38 20 9 20-9-7-4-13 6-13-6Z", accent: false },
  { d: "m4 28 20 9 20-9-7-4-13 6-13-6Z", accent: false },
  { d: "m4 18 20 9 20-9-7-4-13 6-13-6Z", accent: true },
  { d: "M24 2 4 11l20 9 20-9Z", accent: false },
] as const;

/**
 * The FourStacks mark in the brand-panel colours (--fs-auth-logo, with --fs-auth-logo-accent on the
 * third layer). The layers drop into place one after another (fs-logo-drop in globals.css). The
 * server render already carries the class, and CSS runs it unless reduced motion is asked for or
 * the document is marked Calm, so the drop starts at first paint and does not blink at hydration.
 * Once the hook has reported, data-motion is "on" or "off" from the same hook the other animations use, and
 * "off" (paused, hidden tab) stops it. Until it reports it is "auto": CSS alone decides.
 */
export function FourStacksLogo({
  size = 120,
  animated = true,
}: {
  size?: number;
  animated?: boolean;
}) {
  // null until the hook has reported once: stay "auto" until then, so the answer arriving never
  // passes through "off" (which would stop and restart the drop).
  const report = useMotionReport();
  const motion = !animated ? "off" : report === null ? "auto" : report ? "on" : "off";
  return (
    <svg
      viewBox="0 0 48 52"
      width={size}
      height={(size * 52) / 48}
      fill="none"
      aria-hidden="true"
      focusable="false"
      data-motion={motion}
      className={animated ? "fs-logo-drop" : undefined}
    >
      {LAYERS.map((layer) => (
        <path
          key={layer.d}
          d={layer.d}
          fill={layer.accent ? "var(--fs-auth-logo-accent)" : "var(--fs-auth-logo)"}
        />
      ))}
    </svg>
  );
}
