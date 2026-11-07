/**
 * Pure rules for the Training library PDF viewer (components/console/training/PdfViewer.tsx):
 * the sentence for each way opening a PDF can fail, how big to draw a page, the zoom steps, what a
 * swipe means and which pages are near enough to draw. No React, no pdf.js, no DOM.
 */

// ---- States -----------------------------------------------------------------------------------

export const OPENING = "Opening the PDF…";

/** Why a PDF could not be shown. */
export type ViewerFailure = "unavailable" | "gone" | "damaged" | "protected";

const COPY: Readonly<Record<ViewerFailure, string>> = {
  unavailable: "We could not open this PDF. Try again in a minute.",
  gone: "This PDF is no longer in the library.",
  damaged: "This PDF is damaged, so we cannot show it. Ask the person who added it to upload it again.",
  protected: "This PDF is protected, so we cannot show it.",
};

export function viewerMessage(failure: ViewerFailure): string {
  return COPY[failure];
}

/** The file route answered with something other than 2xx. */
export class FileResponseError extends Error {
  constructor(public status: number) {
    super("The PDF file could not be read");
    this.name = "FileResponseError";
  }
}

const field = (value: unknown, key: string): unknown =>
  typeof value === "object" && value !== null ? (value as Record<string, unknown>)[key] : undefined;

/**
 * The failure for an error met while fetching or opening a PDF. pdf.js errors are matched by
 * `name`, because the worker sends them across as copies; a FormatError raised in the worker
 * arrives as an UnknownErrorException whose `details` start with its name.
 */
export function viewerStateFor(error: unknown): ViewerFailure {
  if (field(error, "status") === 404) return "gone";
  const name = field(error, "name");
  if (name === "PasswordException") return "protected";
  if (name === "InvalidPDFException" || name === "FormatError") return "damaged";
  const details = field(error, "details");
  if (name === "UnknownErrorException" && typeof details === "string" && details.startsWith("FormatError"))
    return "damaged";
  return "unavailable";
}

// ---- Sizes ------------------------------------------------------------------------------------

/** 4096 × 4096: under every browser's canvas limit, iOS Safari's included. */
export const MAX_CANVAS_PIXELS = 4096 * 4096;

const positive = (n: number) => Number.isFinite(n) && n > 0;

/**
 * How to draw a `pageW` × `pageH` page (PDF points at scale 1) inside a `boxW` × `boxH` box of
 * CSS pixels, keeping its shape: `scale` is the CSS size, `outputScale` the canvas pixels per CSS
 * pixel (the device pixel ratio, between 1 and 2, lowered if the canvas would be too big). A
 * `boxH` of Infinity fits the width alone. Null while the page or the box has no size.
 */
export function fitScale(
  pageW: number,
  pageH: number,
  boxW: number,
  boxH: number,
  dpr: number,
): { scale: number; outputScale: number } | null {
  if (!positive(pageW) || !positive(pageH) || !positive(boxW) || !(boxH > 0)) return null;
  const scale = Math.min(boxW / pageW, boxH / pageH);
  const ratio = Number.isFinite(dpr) ? Math.min(Math.max(dpr, 1), 2) : 1;
  const cssPixels = pageW * scale * pageH * scale;
  const outputScale = Math.min(ratio, Math.sqrt(MAX_CANVAS_PIXELS / cssPixels));
  return { scale, outputScale };
}

// ---- Zoom (document mode) -------------------------------------------------------------------------

/** Multiples of the fit-width size; 1 is "Fit width". */
export const ZOOM_STEPS: readonly number[] = [0.5, 0.75, 1, 1.25, 1.5, 2, 3];

export function zoomIn(zoom: number): number {
  return ZOOM_STEPS.find((step) => step > zoom) ?? ZOOM_STEPS[ZOOM_STEPS.length - 1];
}

export function zoomOut(zoom: number): number {
  return [...ZOOM_STEPS].reverse().find((step) => step < zoom) ?? ZOOM_STEPS[0];
}

// ---- Touch ------------------------------------------------------------------------------------

/**
 * A finished touch moved `dx`, `dy` CSS pixels: a mostly sideways move of at least `threshold`
 * changes slide, to the left for the next one. Anything else (a tap, a scroll) is left alone.
 */
export function swipeAction(dx: number, dy: number, threshold = 50): "next" | "previous" | null {
  if (!Number.isFinite(dx) || !Number.isFinite(dy)) return null;
  if (Math.abs(dx) < threshold || Math.abs(dx) <= Math.abs(dy) * 1.5) return null;
  return dx < 0 ? "next" : "previous";
}

// ---- Lazy drawing -----------------------------------------------------------------------------

export interface PageWindow {
  first: number;
  last: number;
}

/**
 * The pages to draw: the visible ones and `margin` pages either side, within 1..`total`. Null
 * while none is visible. Pages outside are kept as empty boxes, so a long PDF never holds hundreds
 * of canvases.
 */
export function renderWindow(visible: Iterable<number>, total: number, margin: number): PageWindow | null {
  const pages = [...visible];
  if (pages.length === 0) return null;
  return {
    first: Math.max(1, Math.min(...pages) - margin),
    last: Math.min(Math.max(1, total), Math.max(...pages) + margin),
  };
}

export function inWindow(page: number, window: PageWindow | null): boolean {
  return window !== null && page >= window.first && page <= window.last;
}

// ---- Words and keys ---------------------------------------------------------------------------

export function slideCounter(page: number, total: number): string {
  return `Slide ${page} of ${total}`;
}

/** Whether a key pressed in `target` belongs to that field rather than to the slides. */
export function ignoresKeys(target: { tagName?: string; isContentEditable?: boolean } | null): boolean {
  if (!target) return false;
  return target.isContentEditable === true || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName ?? "");
}
