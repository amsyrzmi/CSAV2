"use client";

import { useId } from "react";
import { useMotionReport } from "@/components/console/fs/useMotionAllowed";
import { BrandMark } from "@/components/console/shell/BrandMark";

/**
 * The Mito mark: two rounded lobes joined by a pinched waist, a cobalt nucleus in each, drawn
 * as vector geometry (the original concept art was generated PNGs, no longer in the repo; this is the
 * production drawing). The lobes are two circles run through a "gooey" filter inside a mask,
 * so the waist is organic and the shape stays connected; the two apertures are cut out of the
 * mask, so they show whatever is behind the mark.
 *
 * The animation is the name: cell division. Every few seconds the lobes ease apart along the
 * diagonal until the waist thins to a thread, then draw back together, while the nuclei pulse.
 * It follows the same motion rules as the other brand animation: CSS animates by default
 * (data-motion="auto", also on the server render) unless reduced motion is asked for or the
 * document is Calm; once mounted the shared motion hook reports "on" or "off" (hidden tab,
 * paused) and "off" stops it. A still mark is the resting shape.
 */
export function MitoMark({
  size = 32,
  animated = true,
  ink = "var(--fs-text)",
  nucleus = "var(--fs-primary)",
  title,
}: {
  size?: number;
  animated?: boolean;
  /** The lobes' colour. Defaults to the shell text colour; the sign-in panel passes its own. */
  ink?: string;
  /** The nuclei's colour. */
  nucleus?: string;
  /** An accessible name. Without it the mark is decorative. */
  title?: string;
}) {
  const id = useId().replace(/[^a-zA-Z0-9]/g, "");
  const report = useMotionReport();
  const motion = !animated ? "off" : report === null ? "auto" : report ? "on" : "off";
  const goo = `mito-goo-${id}`;
  const cut = `mito-cut-${id}`;
  return (
    <svg
      viewBox="0 0 100 100"
      width={size}
      height={size}
      fill="none"
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable="false"
      data-motion={motion}
      data-testid="mito-mark"
      className="mito-mark shrink-0 overflow-visible"
    >
      <defs>
        <filter id={goo} x="-20%" y="-20%" width="140%" height="140%" colorInterpolationFilters="sRGB">
          <feGaussianBlur in="SourceGraphic" stdDeviation="4.5" result="blur" />
          <feColorMatrix
            in="blur"
            mode="matrix"
            values="1 0 0 0 0  0 1 0 0 0  0 0 1 0 0  0 0 0 24 -10"
          />
        </filter>
        <mask id={cut} maskUnits="userSpaceOnUse" x="-10" y="-10" width="120" height="120">
          <g filter={`url(#${goo})`}>
            <circle className="mito-lobe mito-lobe-a" cx="36" cy="34" r="25" fill="white" />
            <circle className="mito-lobe mito-lobe-b" cx="64" cy="66" r="25" fill="white" />
          </g>
          <circle className="mito-lobe mito-lobe-a" cx="36" cy="34" r="14" fill="black" />
          <circle className="mito-lobe mito-lobe-b" cx="64" cy="66" r="14" fill="black" />
        </mask>
      </defs>
      <rect x="-10" y="-10" width="120" height="120" fill={ink} mask={`url(#${cut})`} />
      <g className="mito-lobe mito-lobe-a">
        <circle className="mito-nucleus" cx="36" cy="34" r="7.5" fill={nucleus} />
      </g>
      <g className="mito-lobe mito-lobe-b">
        <circle className="mito-nucleus mito-nucleus-b" cx="64" cy="66" r="7.5" fill={nucleus} />
      </g>
    </svg>
  );
}

/** The word, in the brand face: rounded, bold, lowercase. */
export function MitoWordmark({ className = "text-lg" }: { className?: string }) {
  return (
    <span className={`brand-wordmark font-extrabold leading-none tracking-tight ${className}`}>mito</span>
  );
}

/**
 * "by [fourstacks mark] fourstacks": the parent company's own logo carries the "by fourstacks".
 * Colours come from the caller so it works on the themed shell and on the cobalt sign-in panel.
 */
export function ByFourstacks({
  size = 14,
  ink,
  accent,
  className = "text-[11px]",
}: {
  size?: number;
  ink?: string;
  accent?: string;
  className?: string;
}) {
  return (
    <span data-testid="by-fourstacks" className={`inline-flex items-center gap-1.5 leading-none ${className}`}>
      <span>by</span>
      <BrandMark size={size} ink={ink} accent={accent} />
      <span className="brand-wordmark font-semibold">fourstacks</span>
    </span>
  );
}
