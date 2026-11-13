/**
 * Pure rules for the Training library pages: the sentence for each refusal, sizes and page counts,
 * the category filter, the checks made before an upload is sent, the notes under a summary, and
 * the page paths. Only type imports, so the node tests (and proxy.ts) load this file on its own.
 */
import type { TrainingItem, TrainingKind, TrainingSummary, VideoSpeech } from "@/lib/training-api";

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
  training_not_found: "This item is no longer in the library. Refresh the page to see the latest.",
  training_no_text: "This PDF has no text we can read, so it cannot be summarised.",
  training_assistant_unavailable: "We could not reach the assistant. Wait a minute and try again.",
  training_forbidden: MANAGE_ONLY,
  "write permission required": MANAGE_ONLY,
  "machine principal has read-only access": MANAGE_ONLY,
  package_inactive: "Training library is not switched on for this company. Ask your administrator.",
  "human session required": "Sign in to the console to use the Training library.",
  "authentication required": "Your session has ended. Sign in again to continue.",
  "active tenant membership required":
    "Your account is not an active member of this company. Ask your administrator for access.",
  "tenant is not authorized": "You cannot open the Training library for this company.",
  "tenant not found": "We could not find that company. Choose another company.",
  "platform admin must select client_id explicitly": "Choose a company first.",
  "invalid csrf token": "Your session needs a refresh. Reload the page and try again.",
  "invalid request origin": "Your session needs a refresh. Reload the page and try again.",
  csrf_required: "Your session needs a refresh. Reload the page and try again.",
  // Videos (docs/CONSOLE_API.md: "Video uploads", "Video processing", "The video route").
  training_not_video: "Only MP4, M4V, MOV or WebM videos can be added. Choose one of those files.",
  training_piece_invalid: "This file changed while it was being sent. Choose it again to add it.",
  training_piece_corrupt: "A part of the video was damaged on the way. Select Continue to send it again.",
  training_piece_missing: "Some parts of the video did not arrive. Select Continue to send them again.",
  training_upload_closed:
    "This upload was stopped, so nothing more can be added to it. Refresh the page, then add the video again.",
  training_busy: "The library is busy with other uploads. Wait a minute and try again.",
  training_uploads_in_progress:
    "Two videos are already being added. Wait for one to finish, or remove one that stopped, then add this one.",
  training_video_unavailable: "Adding videos is not switched on yet. Ask your administrator.",
  training_summary_automatic:
    "A video's summary is made for you once it has been processed, so there is nothing to summarise by hand.",
  training_retry_not_allowed: "This video cannot be tried again. Add it again as a new upload.",
  training_transcription_unavailable:
    "We could not make the transcript because the speech service did not answer. Select Try again.",
  training_summary_unavailable:
    "The transcript is ready, but we could not write the summary. Select Try again.",
  training_video_unreadable:
    "We could not open this video. It may be damaged. Save it again as an MP4, then add it again.",
  training_video_too_long: "This video is longer than 60 minutes. Cut it into shorter videos, then add them.",
  training_video_codec:
    "We cannot read this video's format. Save it again as an MP4 with H.264 video, then add it again.",
  training_range_not_satisfiable: "We could not play the video from that point. Reload the page and try again.",
};

/** What changes for a video: the size limit and the full-library sentence name videos. */
const VIDEO_REFUSALS: Readonly<Record<string, string>> = {
  training_file_too_large: "This video is over 500 MB. Make it smaller, then add it again.",
  training_library_full:
    "The library has no room for another video. Remove a video you no longer need, then try again.",
};

/**
 * The sentence staff see for a refusal; anything unknown gets a plain retry sentence. `kind` is
 * what was being added: the PDF and video limits differ.
 */
export function refusalCopy(code: string, kind: TrainingKind = "pdf"): string {
  if (kind === "video" && Object.hasOwn(VIDEO_REFUSALS, code)) return VIDEO_REFUSALS[code];
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

/** A video's length in words: "45 seconds", "15 min", "1 h 5 min"; empty when not known yet. */
export function durationText(ms: number | null): string {
  if (ms === null || !Number.isFinite(ms) || ms < 0) return "";
  const total = Math.floor(ms / 1000);
  if (total < 60) {
    const seconds = Math.max(total, 1);
    return seconds === 1 ? "1 second" : `${seconds} seconds`;
  }
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (!hours) return `${minutes} min`;
  return minutes ? `${hours} h ${minutes} min` : `${hours} h`;
}

// ---- Categories and the filter ------------------------------------------------------------------

/** The filter entry for items with no category. */
export const UNCATEGORISED = "Uncategorised";

const categoryKey = (category: string) => category.trim().toLowerCase();

/**
 * Each category once, compared case-insensitively and trimmed, in the spelling first seen
 * (the list is newest first). Alphabetical, with "Uncategorised" last when any item has none,
 * unless a category of that name is already listed: then the one entry covers both.
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
  return uncategorised && !seen.has(categoryKey(UNCATEGORISED)) ? [...names, UNCATEGORISED] : names;
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

/** csa/training/limits.py MAX_FILE_BYTES and MAX_VIDEO_BYTES. */
export const MAX_FILE_BYTES = 25 * 1024 * 1024;
export const MAX_VIDEO_BYTES = 500 * 1024 * 1024;
/** csa/training/limits.py MAX_VIDEO_SECONDS. */
const MAX_VIDEO_SECONDS = 3600;
const UNSUPPORTED = "Only PDF files and MP4, M4V, MOV or WebM videos can be added. Choose one of those files.";

/** What a file name's extension says it is, in any case; null for anything the library cannot take. */
export function fileKind(name: string): TrainingKind | null {
  if (/\.pdf$/i.test(name)) return "pdf";
  return /\.(mp4|m4v|mov|webm)$/i.test(name) ? "video" : null;
}
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
  const kind = fileKind(name);
  if (!kind) return UNSUPPORTED;
  if (file.size === 0) return `This file is empty. Choose another ${kind === "video" ? "video" : "PDF"}.`;
  if (file.size > (kind === "video" ? MAX_VIDEO_BYTES : MAX_FILE_BYTES)) return refusalCopy("training_file_too_large", kind);
  return null;
}

/**
 * The sentence that stops a video over 60 minutes, when the browser could read its length. An
 * unknown length (null: a WebM recorded in the browser has none in its header) is never refused
 * here; the server measures it and refuses with the same sentence.
 */
export function videoLengthProblem(seconds: number | null): string | null {
  return seconds !== null && seconds > MAX_VIDEO_SECONDS ? refusalCopy("training_video_too_long") : null;
}

// ---- Summary ----------------------------------------------------------------------------------

/** Shown next to "Summarise this PDF" before the first summary, and kept under every summary. */
export const PROVIDER_NOTICE = "Slide text is sent to our language-model provider when you make a summary.";
export const TRUNCATED_NOTE = "Summarised from the first part of this PDF.";

/** The note under the summary area: the provider notice, after the truncation sentence when cut. */
export function summaryNote(s: TrainingSummary | null): string {
  return s?.truncated ? `${TRUNCATED_NOTE} ${PROVIDER_NOTICE}` : PROVIDER_NOTICE;
}

// ---- Videos -----------------------------------------------------------------------------------

/** Under the file picker for a video, and the reason for it: the sound leaves for the provider. */
export const SOUND_NOTICE = "The sound in this video is sent to our speech-to-text provider to make the transcript.";
/** Managers only, when the video's picture codec is HEVC. */
export const HEVC_NOTE =
  "Some browsers cannot play this video. If it will not play, save it again as an MP4 with H.264 video, then add it again.";
/** Under a video's summary. */
export const VIDEO_SUMMARY_NOTE = "Written by the assistant from this video's transcript.";
export const SUMMARY_MAKING = "The summary is being made.";
export const SUMMARY_AFTER_UPLOAD = "The summary is made once the video has finished uploading.";
/** The transcript could not be read, so whether a summary exists is unknown. */
export const SUMMARY_UNAVAILABLE = "The summary cannot be shown right now.";
export const NO_SPEECH_TEXT = "This video has no speech, so there is no transcript or summary.";
export const NO_AUDIO_TEXT = "This video has no sound, so there is no transcript or summary.";
export const NO_SUMMARY_TEXT = "This video has no summary.";
const WAITING_TEXT =
  "Waiting to start. Your company has used today's video processing, so this video will start when there is room.";

/** The failures a manager can start again (docs/CONSOLE_API.md, `POST /training/items/{id}/retry`). */
const RETRYABLE = new Set([
  "training_transcription_unavailable",
  "training_summary_unavailable",
  "training_storage_unavailable",
]);

/** The sentence for a failed video's code. */
export function failureCopy(code: string | null): string {
  return refusalCopy(code ?? "");
}

/** "Try again" is offered only for a failed video whose failure is worth another go. */
export function canRetry(item: TrainingItem): boolean {
  return item.kind === "video" && item.status === "failed" && item.failure !== null && RETRYABLE.has(item.failure);
}

/**
 * What a manager is told about a video that is not ready: where it is, or why it failed. Null for
 * a ready video (and for every PDF).
 */
export function videoStateText(item: TrainingItem): string | null {
  const { processing } = item;
  switch (item.status) {
    case "ready":
      return null;
    case "failed":
      return failureCopy(item.failure);
    case "uploading":
      return "Uploading";
    case "uploaded":
    case "transcribing":
    case "summarising":
      break;
  }
  if (processing?.waiting) return WAITING_TEXT;
  if (item.status === "summarising") return "Writing the summary";
  if (item.status === "transcribing" && processing && processing.chunks_total !== null)
    return `Transcribing, ${processing.chunks_done} of ${processing.chunks_total} ${processing.chunks_total === 1 ? "part" : "parts"}`;
  return item.status === "transcribing" ? "Transcribing" : "Waiting to be processed";
}

export type VideoSummaryState =
  | "summary"
  | "making"
  | "uploading"
  | "no_speech"
  | "no_audio"
  | "failed"
  | "loading"
  | "none";

/**
 * What the summary panel shows for a video, and only what is true. A silent video says so, whatever
 * else it has. A failed video has no summary coming. Then a stored summary; then, while the video
 * is still being processed, that the summary is on its way (not while it is still uploading).
 * A ready video with no summary yet waits for the transcript's verdict (`speech`, null until
 * known): without it nothing is claimed ("loading"); with speech and still no summary there is none.
 * A video has no Summarise button: the job writes the summary.
 */
export function videoSummaryState(item: TrainingItem, speech: VideoSpeech | null): VideoSummaryState {
  if (speech === "no_speech" || speech === "no_audio") return speech;
  if (item.status === "failed") return "failed";
  if (item.has_summary) return "summary";
  if (item.status === "uploading") return "uploading";
  if (item.status !== "ready") return "making";
  return speech === null ? "loading" : "none";
}

/**
 * The summary panel's sentence for a state, and whether the panel is busy (something is on its
 * way). A failed video's failure is said once, in the manager's notice above the player, so here
 * it only has no summary. `speechFailed`: the transcript could not be read, so a ready video's
 * verdict will not come; the panel says so briefly instead of staying empty and busy. The
 * `summary` state's sentence depends on the summary read, which the panel makes itself.
 */
export function videoSummaryStatus(
  state: Exclude<VideoSummaryState, "summary">,
  speechFailed: boolean,
): { sentence: string; busy: boolean } {
  switch (state) {
    case "no_speech":
      return { sentence: NO_SPEECH_TEXT, busy: false };
    case "no_audio":
      return { sentence: NO_AUDIO_TEXT, busy: false };
    case "making":
      return { sentence: SUMMARY_MAKING, busy: true };
    case "uploading":
      return { sentence: SUMMARY_AFTER_UPLOAD, busy: false };
    case "failed":
    case "none":
      return { sentence: NO_SUMMARY_TEXT, busy: false };
    case "loading":
      return speechFailed
        ? { sentence: SUMMARY_UNAVAILABLE, busy: false }
        : { sentence: "Loading the summary…", busy: true };
  }
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

/** An item's page opened at `seconds` (`?t=`), keeping a platform admin's company. */
export function watchHref(id: string, seconds: number, platform: boolean, tenant: string | null): string {
  const href = itemHref(id, platform, tenant);
  return `${href}${href.includes("?") ? "&" : "?"}t=${Math.max(0, Math.floor(seconds))}`;
}
