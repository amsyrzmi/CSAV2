/**
 * Pure rules for the Training library's add and edit dialog and its summary panel: the form's
 * checks (the server's limits from csa/training/models.py), the PATCH body, the one-key-per-file
 * rule for uploads, the dates and facts on a row, and what the summary panel shows. Runtime
 * imports only from libraries that have none of their own, so the node tests load it alone.
 */
import type { TrainingItem, TrainingPatch, TrainingSummary, TrainingViewMode } from "@/lib/training-api";
import { formatWhen } from "@/lib/email-recipients";
import { shortDate } from "@/lib/services-view";
import {
  durationText,
  pagesText,
  PROVIDER_NOTICE,
  refusalCopy,
  sizeText,
  summaryNote,
  summaryText,
  UNCATEGORISED,
  type PlainText,
} from "@/lib/training-view";

// ---- The form ---------------------------------------------------------------------------------

/** csa/training/models.py TITLE_MAX_CHARS, DESCRIPTION_MAX_CHARS, CATEGORY_MAX_CHARS. */
export const TITLE_MAX = 120;
export const DESCRIPTION_MAX = 1000;
export const CATEGORY_MAX = 60;

export interface ItemForm {
  title: string;
  description: string;
  category: string;
  /** Null for a new PDF until the person chooses: the server then picks from the first page. */
  view_mode: TrainingViewMode | null;
}

export const EMPTY_FORM: ItemForm = { title: "", description: "", category: "", view_mode: null };

export function formFromItem(item: TrainingItem): ItemForm {
  return { title: item.title, description: item.description, category: item.category, view_mode: item.view_mode };
}

/** Characters as the server counts them (code points, not UTF-16 units). */
const length = (text: string) => [...text].length;
// Unicode category Cc: what the server refuses ("control characters are not allowed").
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/;
const CONTROL_BUT_BREAKS = /[\u0000-\u0008\u000b-\u001f\u007f-\u009f]/;
/** A textarea may hand back CR LF; the server takes line feeds only. */
const lineFeeds = (text: string) => text.replace(/\r\n?/g, "\n");

/** The title the server gives an upload: the file name without its extension, at most 120 characters. */
export function titleFromFileName(name: string): string {
  return [...name.trim().replace(/\.(pdf|mp4|m4v|mov|webm)$/i, "")].slice(0, TITLE_MAX).join("").trim();
}

export type FormProblems = Partial<Record<"title" | "description" | "category", string>>;

/** One sentence per field that the server would refuse; empty when the form can be sent. */
export function formProblems(form: ItemForm): FormProblems {
  const problems: FormProblems = {};
  const title = form.title.trim();
  if (!title) problems.title = "Enter a title.";
  else if (length(title) > TITLE_MAX) problems.title = `Keep the title to ${TITLE_MAX} characters or fewer.`;
  else if (CONTROL.test(title)) problems.title = "Remove the line breaks and hidden characters from the title.";
  const description = lineFeeds(form.description).trim();
  if (length(description) > DESCRIPTION_MAX)
    problems.description = "Keep the description to 1,000 characters or fewer.";
  else if (CONTROL_BUT_BREAKS.test(description))
    problems.description = "Remove the hidden characters from the description.";
  const category = form.category.trim();
  if (length(category) > CATEGORY_MAX) problems.category = `Keep the category to ${CATEGORY_MAX} characters or fewer.`;
  else if (CONTROL.test(category))
    problems.category = "Remove the line breaks and hidden characters from the category.";
  return problems;
}

/**
 * The PATCH body: each value trimmed, and with `before` (the item as the server has it) only what
 * differs. An empty result means there is nothing to send (the server refuses an empty PATCH).
 */
export function formToPatch(form: ItemForm, before: TrainingItem | null): TrainingPatch {
  const next = {
    title: form.title.trim(),
    description: lineFeeds(form.description).trim(),
    category: form.category.trim(),
  };
  const patch: TrainingPatch = {};
  for (const key of ["title", "description", "category"] as const)
    if (!before || before[key] !== next[key]) patch[key] = next[key];
  if (form.view_mode && (!before || before.view_mode !== form.view_mode)) patch.view_mode = form.view_mode;
  return patch;
}

/** Step two of an upload failed: the item is there under the title from its file name. */
export const PATCH_FAILED = "The PDF was added under its file name. Edit its details to rename it.";
export const VIDEO_PATCH_FAILED = "The video was added under its file name. Edit its details to rename it.";

// ---- One idempotency key per picked file ------------------------------------------------------

/** What tells one picked file from another without reading it. */
export interface FilePick {
  name: string;
  size: number;
  lastModified: number;
}

export interface UploadAttempt {
  file: FilePick;
  key: string;
}

const samePick = (a: FilePick, b: FilePick) =>
  a.name === b.name && a.size === b.size && a.lastModified === b.lastModified;

/**
 * The attempt for `file`: the previous one (its key too) when it is the same file, so a retry
 * after a lost answer returns the first item; else a new key from `newKey`.
 */
export function attemptFor(previous: UploadAttempt | null, file: FilePick, newKey: () => string): UploadAttempt {
  if (previous && samePick(previous.file, file)) return previous;
  return { file: { name: file.name, size: file.size, lastModified: file.lastModified }, key: newKey() };
}

/**
 * What to keep after a failed upload. The key stays for the retry, unless the server said it
 * was used for other content: then the next try starts with a new key.
 */
export function afterUploadFailure(attempt: UploadAttempt | null, code: string): UploadAttempt | null {
  return code === "training_key_conflict" ? null : attempt;
}

/**
 * Whether a failed upload may still have been stored: no answer at all (`code` empty, the
 * network) or a server error (`status` 500 or more). The list is then read again, so a first
 * copy that landed shows. `status` is null when there was no HTTP answer.
 */
export function uploadMayHaveLanded(code: string, status: number | null): boolean {
  return code === "" || (status !== null && status >= 500);
}

// ---- Dates and the facts on a row --------------------------------------------------------------

/** "Added 7 Nov 2026" in the viewer's zone; empty when the date cannot be read. */
export function addedText(item: TrainingItem): string {
  const day = shortDate(item.created_at);
  return day ? `Added ${day}` : "";
}

/** "Made on 7 Nov 2026, 09:15" (docs/WRITING_GUIDE.md); empty when the date cannot be read. */
export function madeOnText(iso: string, timeZone?: string): string {
  if (Number.isNaN(Date.parse(iso))) return "";
  return `Made on ${formatWhen(iso, timeZone)}`;
}

/** The short facts under a row's title, in reading order, without the ones that are missing. */
export function itemFacts(item: TrainingItem): string[] {
  const facts = [
    item.category.trim() || UNCATEGORISED,
    item.kind === "video"
      ? ["Video", durationText(item.duration_ms)].filter(Boolean).join(", ")
      : pagesText(item.page_count ?? 0),
    sizeText(item.byte_size),
    addedText(item),
    item.uploaded_by_name ? `Added by ${item.uploaded_by_name}` : "",
  ];
  return facts.filter(Boolean);
}

// ---- Download ---------------------------------------------------------------------------------

// Characters Windows, macOS or Linux refuse in a file name, and the control range.
const UNSAFE_IN_NAME = /[\u0000-\u001f\u007f-\u009f/\\:*?"<>|]/g;

/** The saved file's name: the item's title made safe for a computer, ending in `.pdf`. */
export function downloadName(title: string): string {
  const name = title.replace(UNSAFE_IN_NAME, "-").trim().replace(/^[.\s]+|[.\s]+$/g, "");
  if (!name) return "training.pdf";
  return /\.pdf$/i.test(name) ? name : `${name}.pdf`;
}

// ---- The summary panel --------------------------------------------------------------------------

/** Where the panel is: reading a stored summary, showing what it has, or asking for one. */
export type SummaryState =
  | { phase: "reading" }
  | { phase: "ready"; summary: TrainingSummary | null; error: string | null }
  | { phase: "making"; summary: TrainingSummary | null; regenerate: boolean };

export interface SummaryView {
  /** Something is on its way: the panel is `aria-busy` and `status` says what. */
  busy: boolean;
  status: string | null;
  /** "Summarise this PDF": shown until a summary exists. */
  ask: boolean;
  askDisabled: boolean;
  /** The provider notice next to the button, every time until a summary exists. */
  notice: string | null;
  /** The stored summary, as text only. */
  stored: {
    paragraphs: PlainText[];
    points: PlainText[];
    note: string;
    madeOn: string;
    model: string;
  } | null;
  /** "Make a new summary": managers only, once one exists. */
  regenerate: boolean;
  regenerateDisabled: boolean;
  /** The sentence for the last failure (the code stays on `ApiError.code`). */
  error: string | null;
}

export function summaryView(state: SummaryState, canManage: boolean, timeZone?: string): SummaryView {
  if (state.phase === "reading")
    return {
      busy: true, status: "Loading the summary…", ask: false, askDisabled: true, notice: null, stored: null,
      regenerate: false, regenerateDisabled: true, error: null,
    };
  const summary = state.summary;
  const making = state.phase === "making";
  const stored = summary
    ? { ...summaryText(summary), note: summaryNote(summary), madeOn: madeOnText(summary.summarised_at, timeZone), model: summary.model }
    : null;
  return {
    busy: making,
    status: making ? (state.regenerate ? "Making a new summary…" : "Making the summary…") : null,
    ask: !summary,
    askDisabled: making,
    notice: summary ? null : PROVIDER_NOTICE,
    stored,
    regenerate: canManage && summary !== null,
    regenerateDisabled: making,
    error: state.phase === "ready" && state.error !== null ? refusalCopy(state.error) : null,
  };
}
