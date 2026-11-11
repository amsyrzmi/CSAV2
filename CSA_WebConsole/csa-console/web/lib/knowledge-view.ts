/**
 * Pure rules for the Business knowledge pages: which cards the sources make, what chip each one
 * carries, whether anything is still being read (so the page keeps refreshing), and the plain
 * words and figures shown in the opened details. The raw server values stay on the page,
 * inside the details, exactly as they always were.
 */
import type { KbDocument, KbFile, KbJob } from "@/lib/identity-contracts";

export type Chip = "ready" | "reading" | "needs_attention";

export type Card = {
  id: string;
  title: string;
  kind: string;
  addedAt: string | null;
  chip: Chip;
  details: { label: string; value: string }[];
  /** The three plain lines of the first level: when it was added, how big it is, what the assistant can use of it. */
  plain: { label: string; value: string }[];
  /** The uploaded originals that belong to this card, newest first as the files list sends them. */
  fileIds: string[];
  /** Jobs that failed or finished in part for a file already in this library card; their note sits in the details. */
  foldedJobIds: string[];
};

/** Which lists have loaded. A list that has not (or failed) shows "Unknown", never an empty figure. */
export type Loaded = { files: boolean; jobs: boolean };

export type AgentState = "loading" | "ready" | "failed" | "missing";

/** The one plain reason adding files is paused, or null when it is not. Shared by the Add area and the cards. */
export function pausedReason(agent: AgentState, writable: boolean): string | null {
  if (agent === "ready") {
    return writable
      ? null
      : "Adding is switched off for your assistant right now. Ask your administrator to switch it on in Services.";
  }
  if (agent === "failed") return "We could not check whether adding is switched on, so it is paused.";
  if (agent === "missing") return "The knowledge assistant settings are not available, so adding is paused.";
  return "Checking whether adding is switched on...";
}

export type Groups = { library: Card[]; reading: Card[]; attention: Card[] };

const CHIP_LABELS: Record<Chip, string> = {
  ready: "Ready",
  reading: "Reading",
  needs_attention: "Needs attention",
};

/** The one message for a selection the upload cannot take; the picker and a drop both show it. */
export const SELECTION_MESSAGE = "Select one to three non-empty PDF, DOCX or CSV files, at most 10 MB each.";

/** The upload rule: one to three PDF, DOCX or CSV files, none empty, none over 10 MB. Null when the selection is fine. */
export function validateSelection(files: { name: string; size: number }[]): string | null {
  const bad = files.length > 3
    || files.some((file) => !/\.(pdf|docx|csv)$/i.test(file.name) || file.size === 0 || file.size > 10 * 1024 * 1024);
  return bad ? SELECTION_MESSAGE : null;
}

export type ProgressWord = "Waiting" | "Reading" | "Ready" | "Partly read" | "Could not be read" | "Removed";

/** One word for where an uploaded file is, from its job status (null while the job is not in the list yet). */
export function progressWord(status: string | null): ProgressWord {
  if (status === null || status === "queued") return "Waiting";
  if (status === "processing") return "Reading";
  if (status === "done") return "Ready";
  if (status === "partial") return "Partly read";
  if (status === "cancelled") return "Removed";
  return "Could not be read";
}

/** How many cards of "In your library" show before "Show all". */
export const LIBRARY_PREVIEW = 5;

/** The title of an answer saved for a question: its first 255 characters, never cutting a character in two. */
export function answerTitle(query: string): string {
  return Array.from(query).slice(0, 255).join("");
}

/** How many rows each list read returns at most; a list exactly this long may hold more. */
export const LIST_LIMITS = { documents: 50, gaps: 50, jobs: 20 } as const;

/** True when a list is exactly at its limit, so a count taken from it may understate. */
export function atLimit(rows: number | null, limit: number): boolean {
  return rows !== null && rows >= limit;
}

/** A count that is only a lower bound is written "at least N". */
export function countText(count: number, atLeast: boolean): string {
  return atLeast ? `at least ${count}` : String(count);
}

/**
 * The one sentence under the page title. `known` is the count of active documents, `attention`
 * the cards needing attention and `questions` the unanswered questions; a count whose list did
 * not load is null and is never written as 0. `known` and `need` come from lists capped at
 * `LIST_LIMITS`; when such a list is exactly full, `atLeast` says so ("at least 50").
 */
export function kbStatusLine(
  input: { known: number | null; attention: number | null; questions: number | null },
  atLeast: { known?: boolean; need?: boolean } = {},
): string {
  const { known, attention, questions } = input;
  if (known === null) return "We couldn't count everything just now.";
  const first = `Your assistant knows ${countText(known, atLeast.known === true)} thing${known === 1 && !atLeast.known ? "" : "s"}.`;
  if (attention === null || questions === null) return first;
  const need = attention + questions;
  if (need === 0) return `${first} Nothing needs your attention.`;
  if (atLeast.need) return `${first} At least ${need} need your attention.`;
  return `${first} ${need} need${need === 1 ? "s" : ""} your attention.`;
}

export function chipLabel(chip: Chip): string {
  return CHIP_LABELS[chip];
}

/**
 * Read the job state. Only the two in-progress states read; a finished or removed job makes no
 * card; anything unknown needs a look, so a new server state is never hidden.
 */
export function jobChip(status: string): Chip | null {
  if (status === "queued" || status === "processing") return "reading";
  if (status === "done" || status === "cancelled") return null;
  return "needs_attention";
}

export function jobStateText(status: string): string {
  if (status === "queued") return "Waiting to be read";
  if (status === "processing") return "Being read now";
  if (status === "cancelled") return "Removed";
  if (status === "done") return "Finished";
  if (status === "partial") return "Finished, but some files could not be read";
  return "Could not be read";
}

/** The plain line for a job that failed: how many tries it had. The exact line stays in Technical details. */
export function couldNotReadText(attempts: number): string {
  const tries = Number.isFinite(attempts) && attempts > 0 ? Math.round(attempts) : 0;
  return tries === 0 ? "Could not be read" : `Could not be read after ${tries} ${tries === 1 ? "try" : "tries"}`;
}

/** The plain note on a card that needs attention. The exact server line stays in Technical details. */
export function attentionNote(job: KbJob): string {
  if (!job.original_stored) return "The original was removed, so this can't be tried again. Remove it.";
  if (job.status === "partial") {
    return "Some of this file couldn't be read. The rest is in your assistant. Removing this deletes the uploaded file that couldn't be read.";
  }
  return "We couldn't read this file. You can try again, or remove it, which also deletes the uploaded file.";
}

export type JobActions = { canRetry: boolean; canRemove: boolean; canCancel: boolean };

/** What a job's card offers: Try again only for an upload whose original is stored, Remove once it has ended, Cancel while it waits. */
export function jobActions(job: KbJob): JobActions {
  const ended = job.status === "failed" || job.status === "partial";
  return {
    canRetry: ended && job.original_stored && job.source === "upload",
    canRemove: ended || job.status === "cancelled",
    canCancel: job.status === "queued",
  };
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 1024) return `${Math.max(0, Math.round(bytes) || 0)} bytes`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const text = value >= 10 || Number.isInteger(value) ? String(Math.round(value)) : value.toFixed(1);
  return `${text} ${units[unit]}`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const two = (value: number) => String(value).padStart(2, "0");

/** "2 Nov 2026, 09:05" in the viewer's zone. */
export function addedText(at: string | null | undefined): string {
  if (!at) return "Date not recorded";
  const time = new Date(at);
  if (Number.isNaN(time.getTime())) return "Date not recorded";
  return `${time.getDate()} ${MONTHS[time.getMonth()]} ${time.getFullYear()}, ${two(time.getHours())}:${two(time.getMinutes())}`;
}

/** A plain type for a card: from the file name, else from the stored kind. */
export function kindText(fileName: string | null | undefined, kind: string | null | undefined): string {
  const extension = /\.([a-z0-9]+)$/i.exec(fileName ?? "")?.[1]?.toLowerCase();
  if (extension === "pdf") return "PDF";
  if (extension === "docx") return "Word document";
  if (extension === "csv") return "Questions and answers";
  return kind === "faq" ? "Questions and answers" : "Document";
}

function sizeOf(files: KbFile[], loaded: boolean): string {
  if (!loaded) return "Unknown";
  return files.length ? formatBytes(files.reduce((sum, file) => sum + file.size_bytes, 0)) : "Not stored";
}

function plainRows(addedAt: string | null, size: string, use: string): { label: string; value: string }[] {
  return [
    { label: "Added", value: addedText(addedAt) },
    { label: "Size", value: size },
    { label: "What your assistant can use", value: use },
  ];
}

function documentCard(document: KbDocument, files: KbFile[], jobs: KbJob[], loaded: Loaded): Card {
  const own = files.filter((file) => file.document_id === document.id);
  const job = jobs.find((row) => row.meta.results.some((result) => result.document_id === document.id));
  return {
    id: document.id,
    title: document.title,
    kind: kindText(document.file_name ?? own[0]?.name, document.kind),
    addedAt: document.created_at,
    chip: "ready",
    plain: plainRows(document.created_at, sizeOf(own, loaded.files),
      `${document.chunk_count} part${document.chunk_count === 1 ? "" : "s"} your assistant can look up`),
    details: [
      { label: "Size", value: sizeOf(own, loaded.files) },
      { label: "Searchable pieces", value: String(document.chunk_count) },
      { label: "Reading job", value: !loaded.jobs ? "Unknown" : job ? jobStateText(job.status) : "Not recorded" },
    ],
    fileIds: own.map((file) => file.id),
    foldedJobIds: [],
  };
}

function problems(job: KbJob): string {
  const lines: string[] = [];
  if (job.error) lines.push(job.error);
  for (const result of job.meta.results) if (result.error) lines.push(`${result.file}: ${result.error}`);
  return lines.join("; ");
}

function jobCard(job: KbJob, chip: Chip, files: KbFile[], loaded: Loaded, covered: Set<string> = new Set()): Card {
  const known = new Map(files.map((file) => [file.id, file] as const));
  const own = job.files.flatMap((entry) => (entry.file_id && known.has(entry.file_id) ? [known.get(entry.file_id)!] : []));
  const names = job.files.map((entry) => entry.name);
  // A retry leaves the old results in place until the worker finishes; a waiting or reading job shows none of them.
  const current = chip !== "reading";
  const pieces = current ? job.meta.results.reduce((sum, result) => sum + (result.chunks ?? 0), 0) : 0;
  const size = sizeOf(own.filter((file) => !covered.has(file.id)), loaded.files);
  const details = [
    { label: "Size", value: size },
    { label: "Job state", value: jobStateText(job.status) },
    { label: "Searchable pieces", value: pieces ? String(pieces) : "None yet" },
  ];
  const trouble = current ? problems(job) : "";
  if (trouble) details.push({ label: "What went wrong", value: trouble });
  return {
    id: job.id,
    title: names.join(", ") || "Upload",
    kind: kindText(names[0], null),
    addedAt: job.created_at,
    chip,
    plain: plainRows(job.created_at, size,
      chip === "reading" ? "Nothing yet. It is still reading." : "Nothing. It could not read this file."),
    details,
    fileIds: own.map((file) => file.id).filter((id) => !covered.has(id)),
    foldedJobIds: [],
  };
}

/**
 * The cards of the page. Documents are In your library; queued or processing jobs are Being read;
 * every other unfinished state (failed, partial and anything the server may add later; a cancelled job is the exception and makes no card) is
 * Needs attention, so a new state is never hidden. A finished job makes no card: its document
 * is already in the library.
 */
export function groupSources(
  files: KbFile[],
  documents: KbDocument[],
  jobs: KbJob[],
  loaded: Loaded = { files: true, jobs: true },
): Groups {
  const groups: Groups = { library: [], reading: [], attention: [] };
  for (const document of documents) groups.library.push(documentCard(document, files, jobs, loaded));
  const owner = new Map<string, Card>();
  for (const card of groups.library) for (const id of card.fileIds) owner.set(id, card);
  const known = new Set(files.map((file) => file.id));
  for (const job of jobs) {
    const chip = jobChip(job.status);
    if (chip === "reading") groups.reading.push(jobCard(job, chip, files, loaded));
    else if (chip === "needs_attention") {
      // A file that already has a library card must not appear on a second card with a second Delete.
      const ids = job.files.flatMap((entry) => (entry.file_id && known.has(entry.file_id) ? [entry.file_id] : []));
      const covered = new Set(ids.filter((id) => owner.has(id)));
      for (const id of covered) {
        const home = owner.get(id)!;
        if (home.foldedJobIds.includes(job.id)) continue;
        home.foldedJobIds.push(job.id);
        const trouble = problems(job);
        if (trouble && !home.details.some((row) => row.label === "What went wrong")) {
          home.details.push({ label: "What went wrong", value: trouble });
        }
      }
      const allCovered = job.files.length > 0 && job.files.every((entry) => entry.file_id !== null && covered.has(entry.file_id));
      if (!allCovered) groups.attention.push(jobCard(job, chip, files, loaded, covered));
    }
  }
  return groups;
}

/** The page refreshes by itself only while something is being read. */
export function pollingNeeded(groups: Groups): boolean {
  return groups.reading.length > 0;
}

/** Uploaded files that no card shows, so they still have a download and a delete. */
export function looseFiles(files: KbFile[], groups: Groups): KbFile[] {
  const shown = new Set([...groups.library, ...groups.reading, ...groups.attention].flatMap((card) => card.fileIds));
  return files.filter((file) => !shown.has(file.id));
}

export const SWEEP_PENDING = "We'll finish clearing the stored file shortly.";

/**
 * The sentence after Remove, Cancel or Clear all. Only an explicit "done" may say a file was deleted;
 * "pending" says the sweep is still to finish, and anything else (a missing field from an older
 * gateway) claims nothing about the file.
 */
export function removedSentence(
  action: "dismiss" | "cancel" | "clear",
  cleanup: string | null | undefined,
  count = 0,
): string {
  if (action === "clear" && count === 0) return "Nothing was left to remove.";
  const pending = SWEEP_PENDING;
  if (action === "clear") {
    const head = `Removed ${count} failed upload${count === 1 ? "" : "s"} from your list.`;
    if (cleanup === "pending") return `${head} ${pending}`;
    return cleanup === "done" ? `${head} The uploaded files are deleted too.` : head;
  }
  if (action === "cancel") {
    if (cleanup === "pending") return `Cancelled. ${pending}`;
    return cleanup === "done"
      ? "Cancelled. The uploaded files that weren't read are deleted; what your assistant already learned stays."
      : "Cancelled.";
  }
  if (cleanup === "pending") return `Removed from your list. ${pending}`;
  return cleanup === "done" ? "Removed from your list. The uploaded file is deleted too." : "Removed.";
}
