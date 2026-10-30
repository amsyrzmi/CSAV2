"use client";

import { useMotionAllowed } from "@/components/console/fs/useMotionAllowed";

/** The four stacked layers of public/brand/fourstacks-mark.svg, bottom to top. */
const LAYERS = [
  { d: "m4 38 20 9 20-9-7-4-13 6-13-6Z", accent: false },
  { d: "m4 28 20 9 20-9-7-4-13 6-13-6Z", accent: false },
  { d: "m4 18 20 9 20-9-7-4-13 6-13-6Z", accent: true },
  { d: "M24 2 4 11l20 9 20-9Z", accent: false },
] as const;

/**
 * The FourStacks mark in the brand-panel colours (--fs-auth-logo, with --fs-auth-logo-accent on the
 * third layer). The server render and the first paint are the finished mark; once mounted, and only
 * while decorative motion is allowed, the layers drop into place one after another (fs-logo-drop in
 * globals.css). Reduced motion, a hidden tab and the Calm motion preference all leave it still.
 */
export function FourStacksLogo({
  size = 120,
  animated = true,
}: {
  size?: number;
  animated?: boolean;
}) {
  const allowed = useMotionAllowed();
  const dropping = animated && allowed;
  return (
    <svg
      viewBox="0 0 48 52"
      width={size}
      height={(size * 52) / 48}
      fill="none"
      aria-hidden="true"
      focusable="false"
      data-motion={dropping ? "on" : "off"}
      className={dropping ? "fs-logo-drop" : undefined}
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
