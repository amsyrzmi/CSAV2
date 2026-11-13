import { api, ApiError, decode, del, patch, post } from "./api";
import type { VideoUpload } from "./training-video";

/**
 * Mirrors of the training library schemas in docs/console_openapi.json (csa/training/models.py):
 * `ItemOut`, `ItemList`, `ItemPatch`, `SummaryOut`. Every route is under `/console/v1/training`.
 * A refusal throws `ApiError` with the server's code on `code`; `training-view.refusalCopy` turns
 * it into the sentence staff see.
 */
export type TrainingViewMode = "slides" | "document";
export type TrainingKind = "pdf" | "video";
export type VideoStatus = "uploading" | "uploaded" | "transcribing" | "summarising" | "ready" | "failed";
export type VideoSpeech = "speech" | "no_speech" | "no_audio";

/** A video's progress, for managers only (`ItemOut.processing`). */
export interface TrainingProcessing {
  stage: "transcribing" | "summarising";
  chunks_done: number;
  /** Null until known. */
  chunks_total: number | null;
  /** The company's daily processing allowance is used up; the video waits. */
  waiting: boolean;
}

export interface TrainingItem {
  id: string;
  kind: TrainingKind;
  title: string;
  description: string;
  /** Free text, "" when none; compared case-insensitively (`training-view.categoriesOf`). */
  category: string;
  /** The stored default; any reader may flip it for themselves in the viewer. Null for a video. */
  view_mode: TrainingViewMode | null;
  /** Null for a video. */
  page_count: number | null;
  byte_size: number;
  created_at: string;
  /** Null when a platform admin uploaded it or the name could not be read. */
  uploaded_by_name: string | null;
  has_summary: boolean;
  /** Always `ready` for a PDF. */
  status: VideoStatus;
  /** A video's length, once known. */
  duration_ms: number | null;
  /** Managers only: a `training_` code on a `failed` video. */
  failure: string | null;
  /** Managers only: while the video is `uploaded`, `transcribing` or `summarising`. */
  processing: TrainingProcessing | null;
  /** Managers only: `hevc` means some browsers cannot play this video. */
  notice: "hevc" | null;
}

export interface TrainingList {
  /** Newest first. */
  items: TrainingItem[];
  /** Each non-empty category once, alphabetical. */
  categories: string[];
  /** Whether this caller may add, edit, remove and regenerate (admins and the HR owner). */
  can_manage: boolean;
}

/** A field left out keeps its value; "" clears `description` or `category`. */
export interface TrainingPatch {
  title?: string;
  description?: string;
  category?: string;
  view_mode?: TrainingViewMode;
}

/**
 * A stored summary. `summary` and `key_points` are language-model output, shown to every member
 * of the company: render them only as text (`training-view.summaryText`), never as HTML or
 * Markdown.
 */
export interface TrainingSummary {
  summary: string;
  key_points: string[];
  /** The PDF's text was cut before it was sent. */
  truncated: boolean;
  model: string;
  summarised_at: string;
}

const BASE = "/console/v1/training/items";

/**
 * `tenantId` is null for a tenant member (the session names the company) and the chosen company
 * for a platform admin, who must always send `client_id`.
 */
export function trainingPath(tenantId: string | null, rest = ""): string {
  const query = new URLSearchParams();
  if (tenantId) query.set("client_id", tenantId);
  return `${BASE}${rest}${query.size ? `?${query}` : ""}`;
}

const item = (id: string) => `/${encodeURIComponent(id)}`;

export function listItems(tenantId: string | null): Promise<TrainingList> {
  return api(trainingPath(tenantId));
}

/**
 * Step one of the two-step upload: the file alone, as the multipart part `file`. `key` is the
 * Idempotency-Key UUID: one per file the person picks, kept for retries of that file, so a retry
 * after a lost answer returns the first item. Step two is `patchItem` with the details.
 */
export function uploadItem(tenantId: string | null, file: File, key: string): Promise<TrainingItem> {
  const form = new FormData();
  form.append("file", file);
  return api(trainingPath(tenantId), { method: "POST", body: form, headers: { "Idempotency-Key": key } });
}

export function patchItem(tenantId: string | null, id: string, body: TrainingPatch): Promise<TrainingItem> {
  return patch(trainingPath(tenantId, item(id)), body);
}

export function removeItem(tenantId: string | null, id: string): Promise<void> {
  return del(trainingPath(tenantId, item(id)));
}

/** The same-origin PDF route; the viewer fetches it whole (the console forbids framing it). */
export function fileUrl(tenantId: string | null, id: string): string {
  return trainingPath(tenantId, `${item(id)}/file`);
}

/** The stored summary, or null when none has been made. Never calls the model. */
export async function getSummary(tenantId: string | null, id: string): Promise<TrainingSummary | null> {
  const found = await api<TrainingSummary | { summary: null }>(trainingPath(tenantId, `${item(id)}/summary`));
  return found.summary === null ? null : (found as TrainingSummary);
}

/**
 * The stored summary, made first when there is none. `regenerate` (managers only) always makes a
 * new one. The body is required by the server, so a plain ask sends `{}`.
 */
export function makeSummary(tenantId: string | null, id: string, regenerate = false): Promise<TrainingSummary> {
  return post(trainingPath(tenantId, `${item(id)}/summary`), regenerate ? { regenerate: true } : {});
}

// ---- Videos ------------------------------------------------------------------------------------

export interface TranscriptLine {
  start_ms: number;
  end_ms: number;
  /** Speech-to-text output: render it only as text. */
  text: string;
}

/** `TranscriptOut`; `language` and `speech` are null until transcribing has decided them. */
export interface TrainingTranscript {
  language: string | null;
  speech: VideoSpeech | null;
  segments: TranscriptLine[];
}

/** `PlaybackOut`: where to play from until `expires_at` (15 minutes). Never log or store `url`. */
export interface TrainingPlayback {
  url: string;
  expires_at: string;
  media_type: "video/mp4" | "video/webm";
}

const VIDEOS = "/console/v1/training/videos";

function videoPath(tenantId: string | null, rest = ""): string {
  const query = new URLSearchParams();
  if (tenantId) query.set("client_id", tenantId);
  return `${VIDEOS}${rest}${query.size ? `?${query}` : ""}`;
}

/**
 * Starts a video upload. `key` is the Idempotency-Key UUID, one per picked file and kept for retries:
 * the same key answers the same upload with the pieces it already holds.
 */
export function startVideo(tenantId: string | null, file: File, key: string): Promise<VideoUpload> {
  return api(videoPath(tenantId), {
    method: "POST",
    body: JSON.stringify({
      file_name: file.name,
      byte_size: file.size,
      media_type: file.type || "application/octet-stream",
    }),
    headers: { "Idempotency-Key": key },
  });
}

/**
 * One piece, raw, with its SHA-256. A 503 carries its `Retry-After` seconds in `ApiError.body` as
 * `retry_after`, which the upload engine waits for.
 */
export async function putPiece(
  tenantId: string | null,
  itemId: string,
  number: number,
  piece: Blob,
  sha256: string,
): Promise<void> {
  const csrf = await decode<{ csrf_token: string }>(
    await fetch("/console/v1/auth/csrf", { credentials: "same-origin", cache: "no-store" }),
  );
  const response = await fetch(videoPath(tenantId, `/${encodeURIComponent(itemId)}/pieces/${number}`), {
    method: "PUT",
    body: piece,
    headers: {
      "Content-Type": "application/octet-stream",
      "X-Piece-SHA256": sha256,
      "X-CSRF-Token": csrf.csrf_token,
    },
    credentials: "same-origin",
    cache: "no-store",
  });
  const retryAfter = Number(response.headers.get("Retry-After"));
  try {
    await decode<unknown>(response);
  } catch (cause) {
    if (cause instanceof ApiError && Number.isFinite(retryAfter) && retryAfter > 0)
      cause.body = { ...cause.body, retry_after: retryAfter };
    throw cause;
  }
}

/** The plan and the piece numbers the server holds, to resume. */
export function uploadStatus(tenantId: string | null, itemId: string): Promise<VideoUpload> {
  return api(videoPath(tenantId, `/${encodeURIComponent(itemId)}/upload`));
}

export function completeVideo(tenantId: string | null, itemId: string): Promise<TrainingItem> {
  return post(videoPath(tenantId, `/${encodeURIComponent(itemId)}/complete`));
}

/** Managers: start a failed video's processing again (only for the codes worth another go). */
export function retryItem(tenantId: string | null, id: string): Promise<TrainingItem> {
  return post(trainingPath(tenantId, `${item(id)}/retry`));
}

export function getPlayback(tenantId: string | null, id: string): Promise<TrainingPlayback> {
  return api(trainingPath(tenantId, `${item(id)}/playback`), { cache: "no-store" });
}

export function getTranscript(tenantId: string | null, id: string): Promise<TrainingTranscript> {
  return api(trainingPath(tenantId, `${item(id)}/transcript`));
}
