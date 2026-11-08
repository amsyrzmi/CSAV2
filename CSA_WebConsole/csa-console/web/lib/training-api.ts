import { api, del, patch, post } from "./api";

/**
 * Mirrors of the training library schemas in docs/console_openapi.json (csa/training/models.py):
 * `ItemOut`, `ItemList`, `ItemPatch`, `SummaryOut`. Every route is under `/console/v1/training`.
 * A refusal throws `ApiError` with the server's code on `code`; `training-view.refusalCopy` turns
 * it into the sentence staff see.
 */
export type TrainingViewMode = "slides" | "document";

export interface TrainingItem {
  id: string;
  title: string;
  description: string;
  /** Free text, "" when none; compared case-insensitively (`training-view.categoriesOf`). */
  category: string;
  /** The stored default; any reader may flip it for themselves in the viewer. */
  view_mode: TrainingViewMode;
  page_count: number;
  byte_size: number;
  created_at: string;
  /** Null when a platform admin uploaded it or the name could not be read. */
  uploaded_by_name: string | null;
  has_summary: boolean;
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
