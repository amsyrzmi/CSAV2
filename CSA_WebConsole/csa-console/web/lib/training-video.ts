/**
 * Pure rules and the upload engine for videos in the Training library: how a file is cut into
 * pieces, how pieces are sent (three at once, a failed piece alone, with the server's retry
 * rules), the time stamps on a transcript, and the `?t=` seek. No React and no runtime imports,
 * so the node tests load this file on its own; the browser calls (`fetch`, `crypto.subtle`,
 * `<video>`) are passed in or used only when a function runs in the page.
 *
 * The server's piece contract (docs/CONSOLE_API.md, "Video uploads"): a raw body, `X-Piece-SHA256`
 * in hex, exactly `piece_size` bytes for every piece but the last, 503 `training_busy` with
 * `Retry-After` when the gateway holds four pieces, 422 `training_piece_corrupt` (send that piece
 * again), 409 `training_piece_missing` at completion (send what is missing, complete again) and
 * 409 `training_upload_closed` or 404 when the upload was removed.
 */
import type { TrainingItem } from "@/lib/training-api";

/** csa/training/limits.py MAX_VIDEO_SECONDS. */
export const MAX_VIDEO_SECONDS = 3600;
/** Pieces in flight at once from one browser. The gateway holds four in all. */
export const MAX_ACTIVE_PIECES = 3;
/** The wait before each of the three retries of a failed piece. */
export const BACKOFF_MS: readonly number[] = [1000, 2000, 4000];
const DEFAULT_BUSY_MS = 2000;
const MAX_BUSY_MS = 30_000;
/** Waits for room on the gateway that one piece may spend before they count as failures. */
const MAX_BUSY_WAITS = 20;

// ---- Pieces -----------------------------------------------------------------------------------

export interface PiecePlan {
  /** From 1. */
  number: number;
  start: number;
  /** Exclusive. */
  end: number;
}

/** Pieces `1..n` of `pieceSize` bytes, the last the remainder. A file of no bytes has none. */
export function planPieces(size: number, pieceSize: number): PiecePlan[] {
  const pieces: PiecePlan[] = [];
  for (let start = 0, number = 1; start < size; start += pieceSize, number += 1)
    pieces.push({ number, start, end: Math.min(start + pieceSize, size) });
  return pieces;
}

/** Lower-case hex SHA-256 of a piece, read through `crypto.subtle` one piece at a time. */
export async function sha256Hex(blob: Blob): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

// ---- Upload -----------------------------------------------------------------------------------

/** `VideoUploadOut` (docs/CONSOLE_API.md): the plan and the piece numbers the server holds. */
export interface VideoUpload {
  item: TrainingItem;
  piece_size: number;
  piece_count: number;
  received: number[];
}

/** What a resume needs to know about an upload. */
export interface UploadPlan {
  itemId: string;
  pieceSize: number;
  pieceCount: number;
}

/** The part of a file the engine reads: its size and slices (a `File`). */
export interface Sliceable {
  size: number;
  slice(start: number, end: number, type?: string): Blob;
}

export interface UploadDeps {
  start(): Promise<VideoUpload>;
  put(itemId: string, number: number, piece: Blob, sha256: string): Promise<void>;
  status(itemId: string): Promise<VideoUpload>;
  complete(itemId: string): Promise<TrainingItem>;
  sha256(piece: Blob): Promise<string>;
  sleep(ms: number): Promise<void>;
}

export interface UploadProgress {
  /** Pieces the server holds. */
  done: number;
  total: number;
}

/**
 * The upload did not finish but can: `missing` are the pieces still to send (none when only the
 * completion is left). `resumeVideo` carries on from `plan`.
 */
export class UploadStopped extends Error {
  constructor(
    public missing: number[],
    public plan: UploadPlan,
  ) {
    super("upload_stopped");
  }
}

interface Failure {
  status?: number;
  code?: string;
  body?: { retry_after?: unknown };
}

const failureOf = (error: unknown): Failure =>
  typeof error === "object" && error !== null ? (error as Failure) : {};

/** Retrying will not help: the person is not allowed, or the upload is gone. */
function isFatal(error: unknown): boolean {
  const { status } = failureOf(error);
  if (status === undefined) return false;
  if (status === 422 && failureOf(error).code === "training_piece_corrupt") return false;
  return status >= 400 && status < 500 && status !== 408 && status !== 429;
}

function busyWait(error: unknown): number | null {
  const { status, code, body } = failureOf(error);
  if (status !== 503 || code !== "training_busy") return null;
  const seconds = Number(body?.retry_after);
  return Number.isFinite(seconds) && seconds > 0 ? Math.min(seconds * 1000, MAX_BUSY_MS) : DEFAULT_BUSY_MS;
}

/**
 * One piece, until the server holds it: a refusal for room waits `Retry-After`; any other failure
 * waits 1 s, 2 s, then 4 s and tries again; the fourth failure gives the piece up (returns
 * false). A refusal that retrying cannot mend is thrown as it is.
 */
async function sendPiece(
  file: Sliceable,
  piece: PiecePlan,
  plan: UploadPlan,
  deps: UploadDeps,
): Promise<boolean> {
  const blob = file.slice(piece.start, piece.end, "application/octet-stream");
  const sha = await deps.sha256(blob);
  let failures = 0;
  let busy = 0;
  for (;;) {
    try {
      await deps.put(plan.itemId, piece.number, blob, sha);
      return true;
    } catch (error) {
      if (isFatal(error)) throw error;
      const wait = busyWait(error);
      if (wait !== null && busy < MAX_BUSY_WAITS) {
        busy += 1;
        await deps.sleep(wait);
        continue;
      }
      if (failures >= BACKOFF_MS.length) return false;
      await deps.sleep(BACKOFF_MS[failures]);
      failures += 1;
    }
  }
}

/**
 * Sends `numbers` (up to three at a time), reporting each stored piece. After a piece is given up
 * nothing new starts; the ones in flight finish, and the pieces not stored are returned.
 */
async function sendAll(
  file: Sliceable,
  numbers: number[],
  held: Set<number>,
  plan: UploadPlan,
  deps: UploadDeps,
  onProgress: (progress: UploadProgress) => void,
): Promise<number[]> {
  const pieces = planPieces(file.size, plan.pieceSize);
  const queue = numbers.map((number) => pieces[number - 1]).filter(Boolean);
  let stopped = false;
  let fatal: { error: unknown } | null = null;
  const worker = async () => {
    for (let piece = queue.shift(); piece && !stopped; piece = queue.shift()) {
      try {
        if (!(await sendPiece(file, piece, plan, deps))) {
          stopped = true;
          return;
        }
      } catch (error) {
        stopped = true;
        fatal ??= { error };
        return;
      }
      held.add(piece.number);
      onProgress({ done: held.size, total: plan.pieceCount });
    }
  };
  await Promise.all(Array.from({ length: Math.min(MAX_ACTIVE_PIECES, queue.length) }, worker));
  if (fatal) throw (fatal as { error: unknown }).error;
  return numbers.filter((number) => !held.has(number));
}

const missingFrom = (plan: UploadPlan, held: Iterable<number>): number[] => {
  const have = new Set(held);
  return Array.from({ length: plan.pieceCount }, (_, index) => index + 1).filter((n) => !have.has(n));
};

/**
 * Completes the upload. A 409 `training_piece_missing` asks what the server holds, sends those
 * pieces and completes once more; a second one, or no answer at all, stops the upload so the
 * person can continue. Any other refusal is thrown as it is.
 */
async function finish(
  file: Sliceable,
  plan: UploadPlan,
  held: Set<number>,
  deps: UploadDeps,
  onProgress: (progress: UploadProgress) => void,
): Promise<TrainingItem> {
  for (let round = 0; ; round += 1) {
    try {
      return await deps.complete(plan.itemId);
    } catch (error) {
      const { status, code } = failureOf(error);
      if (status === 409 && code === "training_piece_missing") {
        const stored = await deps.status(plan.itemId);
        held.clear();
        for (const number of stored.received) held.add(number);
        const missing = missingFrom(plan, held);
        if (round >= 1) throw new UploadStopped(missing, plan);
        const left = await sendAll(file, missing, held, plan, deps, onProgress);
        if (left.length) throw new UploadStopped(left, plan);
        continue;
      }
      if (status === undefined || status >= 500) throw new UploadStopped([], plan);
      throw error;
    }
  }
}

async function run(
  file: Sliceable,
  plan: UploadPlan,
  received: number[],
  deps: UploadDeps,
  onProgress: (progress: UploadProgress) => void,
): Promise<TrainingItem> {
  const held = new Set(received);
  onProgress({ done: held.size, total: plan.pieceCount });
  const left = await sendAll(file, missingFrom(plan, held), held, plan, deps, onProgress);
  if (left.length) throw new UploadStopped(left, plan);
  return finish(file, plan, held, deps, onProgress);
}

/**
 * Starts the upload and sends every piece the server does not hold, then completes it. Throws
 * `UploadStopped` when pieces could not be sent (continue with `resumeVideo`), or the server's own
 * error when the upload was removed or is refused.
 */
export async function uploadVideo(
  file: Sliceable,
  deps: UploadDeps,
  onProgress: (progress: UploadProgress) => void,
): Promise<TrainingItem> {
  const started = await deps.start();
  const plan = { itemId: started.item.id, pieceSize: started.piece_size, pieceCount: started.piece_count };
  return run(file, plan, started.received, deps, onProgress);
}

/** Asks the server which pieces it holds and sends only the others, then completes. */
export async function resumeVideo(
  file: Sliceable,
  plan: UploadPlan,
  deps: UploadDeps,
  onProgress: (progress: UploadProgress) => void,
): Promise<TrainingItem> {
  const stored = await deps.status(plan.itemId);
  return run(file, plan, stored.received, deps, onProgress);
}

// ---- Length of the video ------------------------------------------------------------------------

/**
 * The length the browser read from the file, or null when it is not a usable number. A WebM
 * recorded in the browser has no length in its header, so `video.duration` is `Infinity` (or NaN):
 * that is unknown, never a wrong number and never a refusal; the server measures it and refuses a
 * video over 60 minutes with its own sentence.
 */
export function usableDuration(seconds: number | null | undefined): number | null {
  return typeof seconds === "number" && Number.isFinite(seconds) && seconds > 0 ? seconds : null;
}

/**
 * Reads the length from the file's own header in a detached `<video>`, in seconds, or null when
 * the browser cannot say (no metadata within `timeoutMs`, an unplayable codec, no length).
 * Browser only.
 */
export function readVideoSeconds(file: Blob, timeoutMs = 5000): Promise<number | null> {
  return new Promise((resolve) => {
    const video = document.createElement("video");
    const url = URL.createObjectURL(file);
    let timer = 0;
    const done = (seconds: number | null) => {
      window.clearTimeout(timer);
      video.removeAttribute("src");
      video.load();
      URL.revokeObjectURL(url);
      resolve(seconds);
    };
    timer = window.setTimeout(() => done(null), timeoutMs);
    video.preload = "metadata";
    video.muted = true;
    video.onloadedmetadata = () => done(usableDuration(video.duration));
    video.onerror = () => done(null);
    video.src = url;
  });
}

// ---- Transcript ---------------------------------------------------------------------------------

/** `m:ss`, and `h:mm:ss` from an hour. Whole seconds, rounded down. */
export function stamp(ms: number): string {
  const total = Number.isFinite(ms) && ms > 0 ? Math.floor(ms / 1000) : 0;
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = String(total % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${seconds}` : `${minutes}:${seconds}`;
}

/** `t` from a query string (with or without the `?`): a plain non-negative whole number only. */
export function secondsFromQuery(search: string): number | null {
  const value = new URLSearchParams(search).get("t");
  if (value === null || !/^\d{1,9}$/.test(value)) return null;
  return Number(value);
}

/**
 * Index of the last line that has started at `seconds` (of two lines that start together, the
 * later one), or -1 before the first line. `lines` are in time order.
 */
export function currentLine(lines: readonly { start_ms: number }[], seconds: number): number {
  if (!Number.isFinite(seconds)) return -1;
  const at = seconds * 1000;
  let low = 0;
  let high = lines.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (lines[middle].start_ms <= at) low = middle + 1;
    else high = middle;
  }
  return low - 1;
}
