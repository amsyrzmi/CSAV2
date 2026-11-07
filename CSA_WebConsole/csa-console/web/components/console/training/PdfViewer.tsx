"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type RefObject } from "react";
import { ChevronLeft, ChevronRight, Maximize, Minimize, MoveHorizontal, ZoomIn, ZoomOut } from "lucide-react";
import type { PDFDocumentLoadingTask, PDFDocumentProxy } from "pdfjs-dist";
import { fileUrl, type TrainingViewMode } from "@/lib/training-api";
import { loadPdfjs, pdfSource } from "@/lib/training-pdf";
import { clampPage, keyToAction, pageForAction } from "@/lib/training-slides";
import {
  FileResponseError,
  OPENING,
  ZOOM_STEPS,
  ignoresKeys,
  inWindow,
  renderWindow,
  slideCounter,
  swipeAction,
  viewerMessage,
  viewerStateFor,
  zoomIn,
  zoomOut,
  type ViewerFailure,
} from "@/lib/training-viewer-logic";
import { action, Notice, secondary } from "../ui";
import PageCanvas from "./PageCanvas";
import Thumbnails from "./Thumbnails";

const still = "motion-reduce:transition-none motion-reduce:active:scale-100";
const pressedButton = (pressed: boolean) => `${pressed ? action : secondary} ${still}`;
const plainButton = `${secondary} ${still}`;
const ring =
  "focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]";

interface PageSize {
  w: number;
  h: number;
}

type Opened = { key: string; doc: PDFDocumentProxy; total: number; first: PageSize };
type Failed = { key: string; failure: ViewerFailure };

// ---- Browser state ------------------------------------------------------------------------------

const onFullscreenChange = (notify: () => void) => {
  document.addEventListener("fullscreenchange", notify);
  return () => document.removeEventListener("fullscreenchange", notify);
};
const never = () => () => {};

/** The element shown full screen, or null; null on the server. */
function useFullscreenElement(): Element | null {
  return useSyncExternalStore(onFullscreenChange, () => document.fullscreenElement, () => null);
}

/** Whether this browser lets a page go full screen (iPhone Safari does not); false on the server. */
function useFullscreenEnabled(): boolean {
  return useSyncExternalStore(never, () => document.fullscreenEnabled === true, () => false);
}

interface Box {
  width: number;
  height: number;
  dpr: number;
}

/**
 * The content size of `ref`'s element (inside its padding and scrollbar) and the device pixel
 * ratio. The first size is reported at once; later ones (a window resize, a rotation, full screen)
 * after the size has settled, so a drag-resize does not redraw every frame.
 */
function useBox(ref: RefObject<HTMLElement | null>): Box | null {
  const [box, setBox] = useState<Box | null>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let measured = false;
    const apply = () => {
      const style = getComputedStyle(element);
      const next = {
        width: Math.floor(element.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight)),
        height: Math.floor(element.clientHeight - parseFloat(style.paddingTop) - parseFloat(style.paddingBottom)),
        dpr: window.devicePixelRatio || 1,
      };
      setBox((before) =>
        before && before.width === next.width && before.height === next.height && before.dpr === next.dpr
          ? before
          : next,
      );
    };
    const schedule = () => {
      if (!measured) {
        measured = true;
        apply();
        return;
      }
      clearTimeout(timer);
      timer = setTimeout(apply, 120);
    };
    const observer = typeof ResizeObserver === "function" ? new ResizeObserver(schedule) : null;
    observer?.observe(element);
    if (!observer) timer = setTimeout(apply, 0);
    // A zoom or a move to another screen changes the pixel ratio; browsers report it as a resize.
    window.addEventListener("resize", schedule);
    return () => {
      observer?.disconnect();
      window.removeEventListener("resize", schedule);
      clearTimeout(timer);
    };
  }, [ref]);
  return box;
}

// ---- The viewer -------------------------------------------------------------------------------

/**
 * A Training library PDF, drawn with pdf.js to canvases. Slides: one page fitted to the box, with
 * previous and next, arrow keys, swipes, a counter, thumbnails and full screen. Document: every
 * page stacked at the width of the box, drawn as it nears the screen, with zoom. `mode` is the
 * reader's own choice; `onMode` asks the page to change it.
 *
 * The file is fetched whole from the same-origin file route and handed to pdf.js as bytes: no
 * blob URL, no frame. Nothing about the PDF (its title, its bytes, pdf.js's error text) is logged
 * or shown; each failure has one plain sentence.
 */
export function PdfViewer({
  tenantId,
  itemId,
  title,
  mode,
  onMode,
}: {
  /** Null for a member (the session names the company); the chosen company for a platform admin. */
  tenantId: string | null;
  itemId: string;
  title: string;
  mode: TrainingViewMode;
  onMode: (mode: TrainingViewMode) => void;
}) {
  const [attempt, setAttempt] = useState(0);
  const key = `${tenantId ?? ""}/${itemId}/${attempt}`;
  const [result, setResult] = useState<Opened | Failed | null>(null);
  const [place, setPlace] = useState({ key, page: 1 });
  const [zoom, setZoom] = useState(1);
  const [viewer, setViewer] = useState<HTMLElement | null>(null);
  const fullscreenElement = useFullscreenElement();
  const canFullscreen = useFullscreenEnabled();
  const fullscreen = viewer !== null && fullscreenElement === viewer;

  useEffect(() => {
    const controller = new AbortController();
    let task: PDFDocumentLoadingTask | null = null;
    const openKey = `${tenantId ?? ""}/${itemId}/${attempt}`;
    (async () => {
      const response = await fetch(fileUrl(tenantId, itemId), {
        credentials: "same-origin",
        cache: "no-store",
        signal: controller.signal,
      });
      if (!response.ok) throw new FileResponseError(response.status);
      // Anything but a PDF (a sign-in page after a lapsed session, say) is a failed read, not damage.
      if (!(response.headers.get("Content-Type") ?? "").startsWith("application/pdf"))
        throw new FileResponseError(response.status);
      const bytes = new Uint8Array(await response.arrayBuffer());
      const pdfjs = await loadPdfjs();
      if (controller.signal.aborted) return;
      task = pdfjs.getDocument(pdfSource(bytes));
      const doc = await task.promise;
      const first = (await doc.getPage(1)).getViewport({ scale: 1 });
      if (controller.signal.aborted) return;
      setResult({ key: openKey, doc, total: doc.numPages, first: { w: first.width, h: first.height } });
    })().catch((error: unknown) => {
      if (!controller.signal.aborted) setResult({ key: openKey, failure: viewerStateFor(error) });
    });
    return () => {
      controller.abort();
      // Ends the worker and the document with it, and every page draw still running.
      void task?.destroy();
    };
  }, [tenantId, itemId, attempt]);

  const current = result?.key === key ? result : null;
  const opened = current && "doc" in current ? current : null;
  const total = opened?.total ?? 1;
  const page = clampPage(place.key === key ? place.page : 1, total);
  const goTo = useCallback((next: number) => setPlace({ key, page: next }), [key]);

  const toggleFullscreen = useCallback(() => {
    if (!viewer) return;
    if (document.fullscreenElement) void document.exitFullscreen().catch(() => {});
    else void viewer.requestFullscreen?.().catch(() => {});
  }, [viewer]);

  // Slide keys: anywhere on the page except while typing in a field outside the viewer.
  useEffect(() => {
    if (mode !== "slides" || !opened || !viewer) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented) return;
      const slideAction = keyToAction(event.key, event);
      if (!slideAction) return;
      const target = event.target instanceof Element ? event.target : null;
      const inside = target !== null && viewer.contains(target);
      if (!inside && (ignoresKeys(target as HTMLElement | null) || target?.closest("dialog, [role=dialog]"))) return;
      event.preventDefault();
      if (slideAction === "fullscreen") {
        if (canFullscreen) toggleFullscreen();
        return;
      }
      setPlace((before) => ({
        key: opened.key,
        page: pageForAction(slideAction, before.key === opened.key ? before.page : 1, opened.total),
      }));
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [mode, opened, viewer, canFullscreen, toggleFullscreen]);

  return (
    <section
      ref={setViewer}
      aria-label={title}
      data-testid="training-viewer"
      className={`card min-w-0 p-3 sm:p-4 ${
        fullscreen ? "flex h-full flex-col overflow-auto rounded-none bg-[var(--fs-bg)]" : "rounded-[14px]"
      }`}
    >
      {/* In document mode the bar stays in view while the pages scroll, so changing the zoom or the
          mode never sends the reader back to the top. */}
      <div
        className={`mb-3 flex flex-wrap items-center gap-2 ${
          opened && mode === "document"
            ? "sticky top-0 z-10 -mx-3 bg-[var(--fs-surface)] px-3 py-2 sm:-mx-4 sm:px-4"
            : ""
        }`}
      >
        <div role="group" aria-label="Show as" className="flex gap-2">
          <button type="button" data-testid="training-mode-slides" aria-pressed={mode === "slides"}
            className={pressedButton(mode === "slides")} onClick={() => onMode("slides")}>
            Slides
          </button>
          <button type="button" data-testid="training-mode-document" aria-pressed={mode === "document"}
            className={pressedButton(mode === "document")} onClick={() => onMode("document")}>
            Document
          </button>
        </div>
        {opened && mode === "document" && (
          <div role="group" aria-label="Zoom" className="flex gap-2">
            <button type="button" data-testid="training-zoom-out" className={plainButton}
              disabled={zoom <= ZOOM_STEPS[0]} onClick={() => setZoom(zoomOut)}>
              <ZoomOut aria-hidden="true" size={18} />
              <span className="sr-only sm:not-sr-only">Zoom out</span>
            </button>
            <button type="button" data-testid="training-zoom-in" className={plainButton}
              disabled={zoom >= ZOOM_STEPS[ZOOM_STEPS.length - 1]} onClick={() => setZoom(zoomIn)}>
              <ZoomIn aria-hidden="true" size={18} />
              <span className="sr-only sm:not-sr-only">Zoom in</span>
            </button>
            <button type="button" data-testid="training-zoom-fit" className={plainButton}
              disabled={zoom === 1} onClick={() => setZoom(1)}>
              <MoveHorizontal aria-hidden="true" size={18} />
              Fit width
            </button>
          </div>
        )}
        {opened && canFullscreen && (
          <button type="button" data-testid="training-fullscreen" className={`${plainButton} ml-auto`}
            onClick={toggleFullscreen}>
            {fullscreen ? <Minimize aria-hidden="true" size={18} /> : <Maximize aria-hidden="true" size={18} />}
            {fullscreen ? "Exit full screen" : "Full screen"}
          </button>
        )}
      </div>

      {!current && <Notice testId="training-viewer-loading">{OPENING}</Notice>}
      {current && "failure" in current && (
        <div className="flex flex-wrap items-center gap-3">
          <div className="min-w-0 flex-1">
            <Notice error testId="training-viewer-error">{viewerMessage(current.failure)}</Notice>
          </div>
          {current.failure === "unavailable" && (
            <button type="button" data-testid="training-viewer-retry" className={plainButton}
              onClick={() => setAttempt((n) => n + 1)}>
              Try again
            </button>
          )}
        </div>
      )}
      {opened && mode === "slides" && (
        <SlidesView opened={opened} page={page} onPage={goTo} fullscreen={fullscreen} />
      )}
      {opened && mode === "document" && (
        <DocumentView opened={opened} zoom={zoom} startPage={page} onPage={goTo} fullscreen={fullscreen} />
      )}
    </section>
  );
}

export default PdfViewer;

// ---- Slides -----------------------------------------------------------------------------------

function SlidesView({
  opened,
  page,
  onPage,
  fullscreen,
}: {
  opened: Opened;
  page: number;
  onPage: (page: number) => void;
  fullscreen: boolean;
}) {
  const stage = useRef<HTMLDivElement>(null);
  const box = useBox(stage);
  const touch = useRef<{ x: number; y: number } | null>(null);
  const { doc, total, first } = opened;
  const go = (slideAction: "next" | "previous") => onPage(pageForAction(slideAction, page, total));

  return (
    <div className={fullscreen ? "flex min-h-0 flex-1 flex-col" : undefined}>
      <div
        ref={stage}
        tabIndex={0}
        role="group"
        aria-label="Slides. Use the arrow keys to change slide."
        style={fullscreen ? undefined : { aspectRatio: `${first.w} / ${first.h}` }}
        className={`flex w-full touch-pan-y items-center justify-center overflow-hidden rounded-[var(--fs-radius-control)] bg-[var(--fs-ink-100)] ${ring} ${
          fullscreen ? "min-h-0 flex-1" : "max-h-[70dvh]"
        }`}
        onTouchStart={(event) => {
          const point = event.touches.length === 1 ? event.touches[0] : null;
          touch.current = point ? { x: point.clientX, y: point.clientY } : null;
        }}
        onTouchEnd={(event) => {
          const start = touch.current;
          const point = event.changedTouches[0];
          touch.current = null;
          if (!start || !point) return;
          const swipe = swipeAction(point.clientX - start.x, point.clientY - start.y);
          if (swipe) go(swipe);
        }}
      >
        {box && (
          <PageCanvas doc={doc} pageNumber={page} boxW={box.width} boxH={box.height} dpr={box.dpr}
            label={slideCounter(page, total)} testId="training-page-canvas" />
        )}
      </div>
      <div className="mt-3 flex items-center justify-between gap-2">
        <button type="button" data-testid="training-prev" className={plainButton} disabled={page <= 1}
          onClick={() => go("previous")}>
          <ChevronLeft aria-hidden="true" size={18} />
          <span className="sr-only sm:not-sr-only">Previous slide</span>
        </button>
        <p id="training-counter" data-testid="training-counter" aria-live="polite" className="text-sm">
          {slideCounter(page, total)}
        </p>
        <button type="button" data-testid="training-next" className={plainButton} disabled={page >= total}
          onClick={() => go("next")}>
          <span className="sr-only sm:not-sr-only">Next slide</span>
          <ChevronRight aria-hidden="true" size={18} />
        </button>
      </div>
      {total > 1 && (
        <Thumbnails doc={doc} total={total} current={page} aspect={first.w / first.h} dpr={box?.dpr ?? 1}
          onPick={onPage} />
      )}
    </div>
  );
}

// ---- Document ---------------------------------------------------------------------------------

function DocumentView({
  opened,
  zoom,
  startPage,
  onPage,
  fullscreen,
}: {
  opened: Opened;
  zoom: number;
  /** Shown first, so switching from slides keeps the reader's place. */
  startPage: number;
  /** The top page on screen, so switching back to slides keeps the place too. */
  onPage: (page: number) => void;
  fullscreen: boolean;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const box = useBox(scroller);
  const { doc, total, first } = opened;
  const [visible, setVisible] = useState<ReadonlySet<number>>(() => new Set());
  const [sizes, setSizes] = useState<Readonly<Record<number, PageSize>>>({});
  const start = useRef(startPage);
  const placed = useRef(false);

  const onSize = useCallback((n: number, w: number, h: number) => {
    setSizes((before) => {
      const known = before[n] ?? first;
      return known.w === w && known.h === h ? before : { ...before, [n]: { w, h } };
    });
  }, [first]);

  // Which pages are on screen. In full screen the viewer scrolls; otherwise the page does.
  useEffect(() => {
    const root = scroller.current;
    if (!root || typeof IntersectionObserver !== "function") return;
    const seen = new Set<number>();
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          const n = Number((entry.target as HTMLElement).dataset.page);
          if (entry.isIntersecting) seen.add(n);
          else seen.delete(n);
        }
        setVisible(new Set(seen));
        if (seen.size) onPage(Math.min(...seen));
      },
      { root: fullscreen ? root : null },
    );
    root.querySelectorAll("[data-page]").forEach((item) => observer.observe(item));
    return () => observer.disconnect();
  }, [total, fullscreen, onPage]);

  // Once the pages have a size, open at the page the reader was on.
  useEffect(() => {
    if (placed.current || !box) return;
    placed.current = true;
    if (start.current > 1)
      scroller.current?.querySelector(`[data-page="${start.current}"]`)?.scrollIntoView({ block: "start" });
  }, [box]);

  const drawn = renderWindow(visible.size ? visible : [startPage], total, 2);
  const width = box ? Math.floor(box.width * zoom) : 0;
  const pages = Array.from({ length: total }, (_, index) => index + 1);

  return (
    <div
      ref={scroller}
      className={`overflow-auto rounded-[var(--fs-radius-control)] bg-[var(--fs-ink-100)] p-2 sm:p-4 ${
        fullscreen ? "min-h-0 flex-1" : ""
      }`}
    >
      <ol aria-label="Pages" className="mx-auto flex w-max min-w-full flex-col items-center gap-3 sm:gap-4">
        {pages.map((n) => {
          const size = sizes[n] ?? first;
          const height = width ? Math.floor((size.h * width) / size.w) : 0;
          return (
            <li key={n} data-page={n} className="scroll-mt-20 shadow-[var(--fs-shadow-card)]"
              style={{ width: width || "100%", height: height || undefined, minHeight: height ? undefined : 160 }}>
              {box && inWindow(n, drawn) && (
                <PageCanvas doc={doc} pageNumber={n} boxW={width} boxH={Number.POSITIVE_INFINITY} dpr={box.dpr}
                  label={`Page ${n} of ${total}`} testId="training-page-canvas" onSize={onSize} />
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
