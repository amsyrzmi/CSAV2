/**
 * The pdf.js setup for the Training library viewer. Browser only: call `loadPdfjs()` from an
 * effect in a client component, never at module scope or on the server.
 *
 * pdfjs-dist 6.4.299 (Apache-2.0), exact pin in package.json.
 *
 * Worker: the default bundler import. `new URL("pdfjs-dist/build/pdf.worker.min.mjs",
 * import.meta.url)` makes webpack (`next build --webpack`, the Dockerfile build) and Turbopack
 * (`next build`) emit the worker as its own file under `.next/static/media/`, which the standalone
 * server serves from `/_next/static/media/pdf.worker.min.<hash>.mjs` (proxy.ts never checks
 * `_next/static`). Proved in Task 10 on both builds: Chromium started it as a real module worker
 * and rendered a synthetic PDF. No copy under `public/` is needed.
 *
 * Options: the whole file is fetched first and handed over as bytes (no range requests, no
 * streaming), XFA forms are off, and the viewer draws pages to a canvas only, so no annotation
 * links or form scripts run. pdf.js 6 has no `isEvalSupported` option any more: it no longer
 * generates code with eval or Function at all.
 *
 * Known gap: no `wasmUrl`, `cMapUrl` or `standardFontDataUrl` is served, so JPEG 2000 images and
 * ICC colour profiles (decoded by pdf.js's WebAssembly) may not draw (docs/TRAINING_LIBRARY.md).
 */
import type { DocumentInitParameters } from "pdfjs-dist/types/src/display/api";

type Pdfjs = typeof import("pdfjs-dist");

let loaded: Promise<Pdfjs> | null = null;

/** pdf.js with its worker set, loaded once per page. */
export function loadPdfjs(): Promise<Pdfjs> {
  loaded ??= import("pdfjs-dist").then((pdfjs) => {
    pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();
    return pdfjs;
  });
  return loaded;
}

/** The `getDocument` parameters for a PDF already fetched whole into `data`. */
export function pdfSource(data: ArrayBuffer | Uint8Array): DocumentInitParameters {
  return {
    data,
    disableRange: true,
    disableStream: true,
    disableAutoFetch: true,
    enableXfa: false,
    stopAtErrors: false,
  };
}
