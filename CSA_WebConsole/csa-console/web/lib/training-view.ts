/**
 * Pure rules for the Training library pages: the sentence for each refusal, sizes and page counts,
 * the category filter, the checks made before an upload is sent, the notes under a summary, and
 * the page paths. Only type imports, so the node tests (and proxy.ts) load this file on its own.
 */
import type { TrainingItem, TrainingSummary } from "@/lib/training-api";

// ---- Refusals ---------------------------------------------------------------------------------

const MANAGE_ONLY = "Only admins and your company's HR owner can change the library.";
const DAMAGED = "We could not open this PDF. It may be damaged. Save it again as a PDF, then add it again.";
const FALLBACK = "We could not do that. Try again in a minute.";

/**
 * One sentence per code (docs/CONSOLE_API.md, "Error codes the web must map"), plus the console's
 * own refusals a training call can meet. The code itself stays on `ApiError.code` for support.
 */
const REFUSALS: Readonly<Record<string, string>> = {
  training_not_pdf: "Only PDF files can be added. Choose a PDF file.",
  training_pdf_damaged: DAMAGED,
  training_pdf_unreadable: DAMAGED,
  training_pdf_protected: "This PDF is protected with a password. Remove the password, then add it again.",
  training_pdf_active_content:
    "This PDF contains scripts, so it cannot be added. Save it again as a plain PDF, then add it again.",
  training_file_too_large: "This PDF is over 25 MB. Make it smaller, then add it again.",
  training_pdf_too_many_pages: "This PDF has more than 300 pages. Split it into smaller PDFs, then add them.",
  training_library_full: "The library is full. Remove a PDF you no longer need, then try again.",
  training_name_invalid: "We cannot use this file name. Rename the file, then add it again.",
  training_upload_invalid: "We could not read that upload. Choose one PDF and try again.",
  training_key_conflict: "This upload was mixed up with an earlier one. Choose the file again to add it.",
  training_storage_unavailable: "We could not reach the library's storage. Wait a minute and try again.",
  training_parser_unavailable: "We could not check the PDF just now. Wait a minute and try again.",
  training_not_found: "This PDF is no longer in the library. Refresh the page to see the latest.",
  training_no_text: "This PDF has no text we can read, so it cannot be summarised.",
  training_assistant_unavailable: "We could not reach the assistant. Wait a minute and try again.",
  training_forbidden: MANAGE_ONLY,
  "write permission required": MANAGE_ONLY,
  "machine principal has read-only access": MANAGE_ONLY,
  package_inactive: "Training library is not switched on for this company. Ask your administrator.",
  "human session required": "Sign in to the console to use the Training library.",
  "active tenant membership required":
    "Your account is not an active member of this company. Ask your administrator for access.",
  "tenant is not authorized": "You cannot open the Training library for this company.",
  "tenant not found": "We could not find that company. Choose another company.",
  "platform admin must select client_id explicitly": "Choose a company first.",
  "invalid csrf token": "Your session needs a refresh. Reload the page and try again.",
  "invalid request origin": "Your session needs a refresh. Reload the page and try again.",
  csrf_required: "Your session needs a refresh. Reload the page and try again.",
};

/** The sentence staff see for a refusal; anything unknown gets a plain retry sentence. */
export function refusalCopy(code: string): string {
  return Object.hasOwn(REFUSALS, code) ? REFUSALS[code] : FALLBACK;
}

// ---- Sizes and counts -------------------------------------------------------------------------

export function sizeText(bytes: number): string {
  const value = Number.isFinite(bytes) && bytes > 0 ? bytes : 0;
  if (value < 1024) return `${Math.round(value)} bytes`;
  if (value < 1024 * 1024) return `${Math.round(value / 1024)} KB`;
  const mb = value / (1024 * 1024);
  return `${mb < 10 ? Number(mb.toFixed(1)) : Math.round(mb)} MB`;
}

export function pagesText(n: number): string {
  return n === 1 ? "1 page" : `${n} pages`;
}

// ---- Categories and the filter ------------------------------------------------------------------

/** The filter entry for items with no category. */
export const UNCATEGORISED = "Uncategorised";

const categoryKey = (category: string) => category.trim().toLowerCase();

/**
 * Each category once, compared case-insensitively and trimmed, in the spelling first seen
 * (the list is newest first). Alphabetical, with "Uncategorised" last when any item has none.
 */
export function categoriesOf(items: readonly TrainingItem[]): string[] {
  const seen = new Map<string, string>();
  let uncategorised = false;
  for (const item of items) {
    const key = categoryKey(item.category);
    if (!key) uncategorised = true;
    else if (!seen.has(key)) seen.set(key, item.category.trim());
  }
  const names = [...seen.values()].sort((a, b) => a.localeCompare(b, "en", { sensitivity: "base" }));
  return uncategorised ? [...names, UNCATEGORISED] : names;
}

export interface TrainingFilter {
  /** A name from `categoriesOf`, or null/"" for every category. */
  category?: string | null;
  /** Words in the title; case and outer spaces are ignored. */
  query?: string | null;
}

export function filterItems(items: readonly TrainingItem[], filter: TrainingFilter): TrainingItem[] {
  const wanted = filter.category ? categoryKey(filter.category) : null;
  const query = (filter.query ?? "").trim().toLowerCase();
  return items.filter((item) => {
    if (wanted !== null) {
      const key = categoryKey(item.category);
      if (wanted === categoryKey(UNCATEGORISED) ? key !== "" && key !== wanted : key !== wanted) return false;
    }
    return !query || item.title.toLowerCase().includes(query);
  });
}

// ---- Upload -----------------------------------------------------------------------------------

/** csa/training/limits.py MAX_FILE_BYTES. */
export const MAX_FILE_BYTES = 25 * 1024 * 1024;
const MAX_NAME_LENGTH = 255;
// The server's name rule: one segment, no slashes, no control, separator or bidi characters.
const UNSAFE_NAME = /[\u0000-\u001f\u007f-\u009f/\\\u2028\u2029\u200e\u200f\u202a-\u202e\u2066-\u2069]/;

/**
 * The sentence that stops an upload before it is sent, else null. The server checks the content
 * too (a renamed file is refused there); this saves the round trip for the plain cases.
 */
export function uploadProblem(file: { name: string; size: number }): string | null {
  const name = file.name;
  if (!name.trim() || name.length > MAX_NAME_LENGTH || name === "." || name === ".." || UNSAFE_NAME.test(name))
    return refusalCopy("training_name_invalid");
  if (!/\.pdf$/i.test(name)) return refusalCopy("training_not_pdf");
  if (file.size === 0) return "This file is empty. Choose another PDF.";
  if (file.size > MAX_FILE_BYTES) return refusalCopy("training_file_too_large");
  return null;
}

// ---- Summary ----------------------------------------------------------------------------------

/** Shown next to "Summarise this PDF" before the first summary, and kept under every summary. */
export const PROVIDER_NOTICE = "Slide text is sent to our language-model provider when you make a summary.";
export const TRUNCATED_NOTE = "Summarised from the first part of this PDF.";

/** The note under the summary area: the provider notice, after the truncation sentence when cut. */
export function summaryNote(s: TrainingSummary | null): string {
  return s?.truncated ? `${TRUNCATED_NOTE} ${PROVIDER_NOTICE}` : PROVIDER_NOTICE;
}

/**
 * Language-model output about a file a member uploaded. Render it only as a React text child
 * (React escapes it), never through `dangerouslySetInnerHTML`, a Markdown renderer or an attribute
 * that the browser interprets (`href`, `src`, `style`).
 */
export type PlainText = string & { readonly [plainTextBrand]: true };
declare const plainTextBrand: unique symbol;

export interface PlainSummary {
  paragraphs: PlainText[];
  points: PlainText[];
}

const plain = (text: string) => text.trim() as PlainText;

/** The summary split into its paragraphs and points, trimmed, empty ones dropped. Markup stays text. */
export function summaryText(s: TrainingSummary): PlainSummary {
  return {
    paragraphs: s.summary.split(/\n\s*\n/).map(plain).filter(Boolean),
    points: s.key_points.map(plain).filter(Boolean),
  };
}

// ---- Paths ------------------------------------------------------------------------------------

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** An item id as the routes take it: a UUID. */
export function isItemId(id: string): boolean {
  return UUID.test(id);
}

/** Exactly the pages the app serves: the list and one item, under `/training` or `/portal/training`. */
export function isTrainingPagePath(pathname: string): boolean {
  for (const base of ["/portal/training", "/training"]) {
    if (pathname === base) return true;
    if (pathname.startsWith(`${base}/`)) return isItemId(pathname.slice(base.length + 1));
  }
  return false;
}

/**
 * The list (`id` null) or an item's page. A platform admin works under `/training` and keeps the
 * company in `client_id`; everyone else under `/portal/training`.
 */
export function itemHref(id: string | null, platform: boolean, tenant: string | null): string {
  const path = `${platform ? "/training" : "/portal/training"}${id ? `/${encodeURIComponent(id)}` : ""}`;
  if (!platform || !tenant) return path;
  return `${path}?${new URLSearchParams({ client_id: tenant })}`;
}
