"use client";

import Link from "next/link";
import { useState, type ReactNode } from "react";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import type { NavLink } from "@/lib/navigation";
import type { RailState } from "@/lib/shell-state";
import { BrandMark } from "./BrandMark";
import { navIcon } from "./icons";

interface Tip {
  label: string;
  top: number;
  left: number;
}

const ITEM =
  "relative flex h-10 items-center rounded-[10px] text-sm font-medium text-[color:var(--fs-text-secondary)] outline-offset-2 hover:bg-[var(--fs-tile)] hover:text-[color:var(--fs-text)] focus-visible:outline-2 focus-visible:outline-[color:var(--fs-focus)] aria-[current=page]:bg-[var(--fs-surface)] aria-[current=page]:font-semibold aria-[current=page]:text-[color:var(--fs-text)] aria-[current=page]:shadow-[var(--fs-shadow-card)] [&_svg]:shrink-0 aria-[current=page]:[&_svg]:text-[color:var(--fs-primary)]";

/**
 * The collapse toggle (first in the tab order), the brand mark and navigation. Collapsed
 * (56 px) every link is an icon whose visible label text is only hidden from sight, so its
 * accessible name is always the exact label; a tooltip with the same words shows on hover and
 * keyboard focus. Expanded (240 px) the label is on
 * screen. The choice lives in the parent, which also writes the `fs-rail` cookie.
 */
export function Rail({
  links,
  path,
  home,
  state,
  onToggle,
}: {
  links: NavLink[];
  path: string;
  home: string;
  state: RailState;
  onToggle: () => void;
}) {
  const expanded = state === "expanded";
  const [tip, setTip] = useState<Tip | null>(null);
  // A fixed tooltip is not clipped by the scrolling nav and does not move the layout.
  const tipFor = (label: string) => ({
    onMouseEnter: (event: React.MouseEvent<HTMLElement>) => show(label, event.currentTarget),
    onFocus: (event: React.FocusEvent<HTMLElement>) => show(label, event.currentTarget),
    onMouseLeave: () => setTip(null),
    onBlur: () => setTip(null),
    onKeyDown: (event: React.KeyboardEvent) => {
      if (event.key === "Escape") setTip(null);
    },
  });
  function show(label: string, element: HTMLElement) {
    if (expanded) return;
    const box = element.getBoundingClientRect();
    setTip({ label, top: box.top + box.height / 2, left: box.right + 8 });
  }
  const itemSize = expanded ? "w-full gap-3 px-3" : "w-10 justify-center";
  const text = (label: string): ReactNode => (
    <span className={expanded ? "truncate" : "sr-only"}>{label}</span>
  );

  return (
    <aside
      data-testid="shell-rail"
      data-rail={state}
      className={`hidden shrink-0 shadow-[inset_-1px_0_0_var(--fs-border)] bg-[var(--fs-rail)] lg:block ${expanded ? "w-60" : "w-14"}`}
    >
      <div
        onScroll={() => setTip(null)}
        className="flex flex-col gap-1 p-2 lg:sticky lg:top-0 lg:max-h-dvh lg:overflow-y-auto"
      >
        <button
          type="button"
          data-testid="shell-rail-toggle"
          // The name already says what a press does ("Expand" / "Collapse navigation"); adding
          // aria-expanded would announce the state twice ("Collapse navigation, expanded").
          onClick={() => {
            setTip(null);
            onToggle();
          }}
          className={`${ITEM} ${itemSize}`}
          {...tipFor(expanded ? "Collapse navigation" : "Expand navigation")}
        >
          {expanded ? <PanelLeftClose size={18} aria-hidden="true" /> : <PanelLeftOpen size={18} aria-hidden="true" />}
          <span className={expanded ? "truncate" : "sr-only"}>
            {expanded ? "Collapse navigation" : "Expand navigation"}
          </span>
        </button>
        <Link
          href={home}
          aria-label="fourstacks home"
          className={`${ITEM} h-12 ${expanded ? "w-full gap-3 px-1" : "w-10 justify-center"}`}
        >
          <BrandMark size={32} />
          {expanded && (
            <span className="brand-wordmark truncate text-lg font-semibold">fourstacks.</span>
          )}
        </Link>
        <nav aria-label="Main navigation" className="flex flex-col gap-1">
          {links.map(({ href, label, key }) => {
            const Icon = navIcon(key);
            return (
              <Link
                key={href}
                href={href}
                aria-current={path === href ? "page" : undefined}
                className={`${ITEM} ${itemSize}`}
                {...tipFor(label)}
              >
                <Icon size={18} aria-hidden="true" />
                {text(label)}
              </Link>
            );
          })}
        </nav>
      </div>
      {tip && (
        <span
          role="tooltip"
          style={{ top: tip.top, left: tip.left }}
          className="pointer-events-none fixed z-50 -translate-y-1/2 whitespace-nowrap rounded-md bg-[var(--fs-text)] px-2 py-1 text-xs font-medium text-[color:var(--fs-surface)] shadow-[var(--fs-shadow-popover)]"
        >
          {tip.label}
        </span>
      )}
    </aside>
  );
}
