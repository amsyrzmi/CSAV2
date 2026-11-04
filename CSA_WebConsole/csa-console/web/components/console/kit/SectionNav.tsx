"use client";

import Link from "next/link";
import { useEffect, type RefObject } from "react";
import type { NavChild } from "@/lib/navigation";

// Set by a click on a section-bar link; the next page that mounts reads it once and moves focus
// to its own section heading, so a keyboard or screen-reader user lands where the page changed.
let arrivedBySectionBar = false;

/** Focus `ref` (without scrolling) on mount, but only when a section-bar link brought us here. */
export function useSectionArrivalFocus(ref: RefObject<HTMLElement | null>): void {
  useEffect(() => {
    if (!arrivedBySectionBar) return;
    arrivedBySectionBar = false;
    ref.current?.focus({ preventScroll: true });
  }, [ref]);
}

const link =
  "inline-flex min-h-10 items-center border-b-[3px] px-3 py-1 text-[length:var(--fs-text-md)] pointer-coarse:min-h-11 " +
  "transition-[border-color,color] duration-150 ease-(--fs-ease-out) motion-reduce:transition-none " +
  "focus-visible:outline-3 focus-visible:outline-offset-[-3px] focus-visible:outline-[color:var(--fs-focus)]";
const idle =
  "border-transparent font-medium text-[color:var(--fs-text-secondary)] hover:border-[color:var(--fs-border-control)] hover:text-[color:var(--fs-text)]";
// Current is bold with a thicker bar, so colour is never the only signal.
const current = "border-[color:var(--fs-primary)] font-bold text-[color:var(--fs-text)]";

/**
 * The in-page section bar: a wrapping row of tab-style links under a page heading. `query` is
 * appended to every link (a platform administrator's `client_id=…` travels this way).
 */
export default function SectionNav({
  label,
  items,
  current: currentKey,
  query,
  testId,
}: {
  label: string;
  items: NavChild[];
  current: string;
  query?: string;
  testId: string;
}) {
  return (
    <nav
      aria-label={label}
      data-testid={testId}
      className="mb-4 flex flex-wrap gap-x-1 gap-y-1 border-b border-[color:var(--fs-border)]"
    >
      {items.map((item) => {
        const on = item.key === currentKey;
        return (
          <Link
            key={item.key}
            href={query ? `${item.href}?${query}` : item.href}
            data-testid={item.testId}
            aria-current={on ? "page" : undefined}
            onClick={(event) => {
              // Only a plain left click that really moves to another page asks for the focus move.
              const plain = event.button === 0 && !event.metaKey && !event.ctrlKey && !event.shiftKey
                && !event.altKey && !event.defaultPrevented;
              arrivedBySectionBar = plain && !on;
            }}
            className={`${link} ${on ? current : idle}`}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
