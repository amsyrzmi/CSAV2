"use client";

import { useId, useState, type ReactNode } from "react";
import { useMotionAllowed } from "@/components/console/fs/useMotionAllowed";

/**
 * A row with a plain summary on the left and a "Show details" button on the right. Only the
 * button toggles: links and buttons inside `summary` stay clickable. The technical text sits
 * in a group linked by `aria-controls`; it stays mounted (so a test can find it) but is hidden
 * and inert while closed. Controlled when `open` is given, otherwise it keeps its own state.
 * The height animates only while decorative motion is allowed (never under reduced motion,
 * a hidden tab or the "Reduce animations" preference). `toggleLabel` names what the button
 * opens for screen readers ("Show details for order 12"); sighted readers see "Show details".
 */
export default function DetailsRow({
  summary,
  children,
  open,
  defaultOpen = false,
  onOpenChange,
  testId,
  toggleLabel,
}: {
  summary: ReactNode;
  children: ReactNode;
  open?: boolean;
  defaultOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  testId?: string;
  toggleLabel?: string;
}) {
  const [inner, setInner] = useState(defaultOpen);
  const isOpen = open ?? inner;
  const regionId = useId();
  const animate = useMotionAllowed();

  const toggle = () => {
    const next = !isOpen;
    if (open === undefined) setInner(next);
    onOpenChange?.(next);
  };

  return (
    <div data-testid={testId} data-open={isOpen ? "true" : "false"} className="min-w-0">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <div className="min-w-0 flex-1 basis-48 [overflow-wrap:anywhere]">{summary}</div>
        <button
          type="button"
          aria-expanded={isOpen}
          aria-controls={regionId}
          data-testid={testId ? `${testId}-toggle` : undefined}
          onClick={toggle}
          className="inline-flex min-h-10 shrink-0 items-center gap-1.5 rounded-[var(--fs-radius-control)] px-2 text-[color:var(--fs-primary)] hover:bg-[var(--fs-surface-blue)] pointer-coarse:min-h-11"
        >
          {/* Sized on a span: the tokens' unlayered `font: inherit` on buttons beats utilities. */}
          <span className="text-[length:var(--fs-text-sm)] font-medium">
            {isOpen ? "Hide details" : "Show details"}
            {toggleLabel ? <span className="sr-only"> for {toggleLabel}</span> : null}
          </span>
          <svg
            aria-hidden="true"
            viewBox="0 0 24 24"
            width="16"
            height="16"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="shrink-0"
            style={{
              transform: isOpen ? "rotate(180deg)" : "none",
              transition: animate ? "transform 150ms var(--fs-ease-out)" : "none",
            }}
          >
            <path d="m6 9 6 6 6-6" />
          </svg>
        </button>
      </div>
      <div
        id={regionId}
        role="group"
        aria-label={toggleLabel ? `Details for ${toggleLabel}` : "Details"}
        data-testid={testId ? `${testId}-details` : undefined}
        inert={!isOpen}
        style={{
          display: "grid",
          gridTemplateRows: isOpen ? "1fr" : "0fr",
          visibility: isOpen ? "visible" : "hidden",
          transition: animate
            ? `grid-template-rows 150ms var(--fs-ease-out), visibility 0s linear ${isOpen ? "0s" : "150ms"}`
            : "none",
        }}
      >
        <div className="min-h-0 overflow-hidden">
          <div className="px-1.5 pb-1.5 pt-2 text-sm text-[color:var(--fs-text)] [overflow-wrap:anywhere]">
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}
