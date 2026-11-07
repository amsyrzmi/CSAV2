"use client";

import { useEffect, useRef } from "react";
import type { PDFDocumentProxy, RenderTask } from "pdfjs-dist";
import { fitScale } from "@/lib/training-viewer-logic";

/** Hand a canvas's memory back at once instead of waiting for the garbage collector. */
function release(canvas: Element) {
  if (canvas instanceof HTMLCanvasElement) {
    canvas.width = 0;
    canvas.height = 0;
  }
}

/**
 * One PDF page drawn to a canvas, fitted inside `boxW` × `boxH` CSS pixels (`boxH` Infinity fits
 * the width). Every draw goes to a new canvas that replaces the old one only when it is finished,
 * so the old page stays on screen meanwhile and no two draws ever share a canvas; a draw still
 * running when the page, the size or the document changes is cancelled. Canvas only: no text or
 * annotation layer, so nothing in the PDF is clickable and no link is followed.
 */
export default function PageCanvas({
  doc,
  pageNumber,
  boxW,
  boxH,
  dpr,
  label,
  testId,
  onSize,
  className = "",
}: {
  doc: PDFDocumentProxy;
  pageNumber: number;
  boxW: number;
  boxH: number;
  dpr: number;
  /** What a screen reader hears for the page; leave it out when the page sits inside a named control. */
  label?: string;
  /** Set on the canvas itself. */
  testId?: string;
  /** The page's size at scale 1, once known. Keep it stable (useCallback). */
  onSize?: (pageNumber: number, width: number, height: number) => void;
  className?: string;
}) {
  const holder = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    const host = holder.current;
    if (!host) return;
    let cancelled = false;
    let task: RenderTask | null = null;
    doc
      .getPage(pageNumber)
      .then((page) => {
        if (cancelled) return;
        const base = page.getViewport({ scale: 1 });
        onSize?.(pageNumber, base.width, base.height);
        const fit = fitScale(base.width, base.height, boxW, boxH, dpr);
        if (!fit) return;
        const viewport = page.getViewport({ scale: fit.scale });
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.floor(viewport.width * fit.outputScale));
        canvas.height = Math.max(1, Math.floor(viewport.height * fit.outputScale));
        canvas.style.width = `${Math.floor(viewport.width)}px`;
        canvas.style.height = `${Math.floor(viewport.height)}px`;
        canvas.style.display = "block";
        if (testId) canvas.dataset.testid = testId;
        const transform =
          fit.outputScale === 1 ? undefined : [fit.outputScale, 0, 0, fit.outputScale, 0, 0];
        const running = page.render({ canvas, viewport, transform });
        task = running;
        return running.promise.then(() => {
          if (cancelled) {
            release(canvas);
            return;
          }
          const old = [...host.children];
          host.replaceChildren(canvas);
          old.forEach(release);
        });
      })
      .catch(() => {
        // A cancelled draw, a document closed while drawing, or a page pdf.js cannot draw: the
        // box keeps whatever it showed. The document-level failures are reported by the viewer.
      });
    return () => {
      cancelled = true;
      task?.cancel();
    };
  }, [doc, pageNumber, boxW, boxH, dpr, testId, onSize]);

  useEffect(() => {
    const host = holder.current;
    return () => {
      if (host) [...host.children].forEach(release);
    };
  }, []);

  return (
    // A span, so the page may sit inside a button (a thumbnail).
    <span
      ref={holder}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={`block ${className}`}
    />
  );
}
