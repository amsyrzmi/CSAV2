"use client";

import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy } from "pdfjs-dist";
import { inWindow, renderWindow } from "@/lib/training-viewer-logic";
import PageCanvas from "./PageCanvas";

const THUMB_HEIGHT = 64;
const ring =
  "focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]";

/**
 * The strip of small slides under the slides view. Only the thumbnails in or near the strip's
 * visible part are drawn; the current one is marked and scrolled into view.
 */
export default function Thumbnails({
  doc,
  total,
  current,
  aspect,
  dpr,
  onPick,
}: {
  doc: PDFDocumentProxy;
  total: number;
  current: number;
  /** Width over height of the first page; every thumbnail box takes this shape. */
  aspect: number;
  dpr: number;
  onPick: (page: number) => void;
}) {
  const strip = useRef<HTMLUListElement>(null);
  const [visible, setVisible] = useState<ReadonlySet<number>>(() => new Set());
  const width = Math.round(Math.min(Math.max(THUMB_HEIGHT * aspect, 40), 128));

  useEffect(() => {
    const root = strip.current;
    if (!root || typeof IntersectionObserver !== "function") return;
    const observer = new IntersectionObserver(
      (entries) =>
        setVisible((before) => {
          const after = new Set(before);
          for (const entry of entries) {
            const page = Number((entry.target as HTMLElement).dataset.page);
            if (entry.isIntersecting) after.add(page);
            else after.delete(page);
          }
          return after;
        }),
      { root, rootMargin: "0px 160px" },
    );
    root.querySelectorAll("[data-page]").forEach((item) => observer.observe(item));
    return () => observer.disconnect();
  }, [total]);

  useEffect(() => {
    const root = strip.current;
    const item = root?.querySelector<HTMLElement>(`[data-page="${current}"]`);
    if (!root || !item) return;
    // Scroll the strip only, never the page.
    const left = item.offsetLeft - (root.clientWidth - item.offsetWidth) / 2;
    const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    root.scrollTo({ left: Math.max(0, left), behavior: reduce ? "auto" : "smooth" });
  }, [current]);

  // Before the observer's first report (or without one), draw the thumbnails around the current slide.
  const drawn = renderWindow(visible.size ? visible : [current], total, 3);
  const pages = Array.from({ length: total }, (_, index) => index + 1);

  return (
    <nav aria-label="Slide thumbnails" className="mt-3">
      <ul ref={strip} className="relative flex gap-2 overflow-x-auto px-1 py-2">
        {pages.map((page) => {
          const isCurrent = page === current;
          return (
            <li key={page} data-page={page} className="shrink-0">
              <button
                type="button"
                data-testid={`training-thumb-${page}`}
                aria-label={`Go to slide ${page}`}
                aria-current={isCurrent ? "true" : undefined}
                onClick={() => onPick(page)}
                className={`flex flex-col items-center gap-1 rounded-[var(--fs-radius-control)] border-2 p-1 text-xs transition-[border-color] duration-150 ease-(--fs-ease-out) motion-reduce:transition-none ${ring} ${
                  isCurrent
                    ? "border-[color:var(--fs-primary)] text-ink-900"
                    : "border-transparent text-[color:var(--fs-text-secondary)] hover:border-[color:var(--fs-border-strong)]"
                }`}
              >
                <span
                  className="flex items-center justify-center overflow-hidden bg-[var(--fs-ink-100)]"
                  style={{ width, height: THUMB_HEIGHT }}
                >
                  {inWindow(page, drawn) && (
                    <PageCanvas doc={doc} pageNumber={page} boxW={width} boxH={THUMB_HEIGHT} dpr={dpr} />
                  )}
                </span>
                <span aria-hidden="true">{page}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
