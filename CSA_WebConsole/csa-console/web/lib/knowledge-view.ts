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

export function chipLabel(chip: Chip): string {
  return CHIP_LABELS[chip];
}

/** Read the job state. Only the two in-progress states read; anything unknown needs a look. */
export function jobChip(status: string): Chip | null {
  if (status === "queued" || status === "running") return "reading";
  if (status === "done") return null;
  return "needs_attention";
}

export function jobStateText(status: string): string {
  if (status === "queued") return "Waiting to be read";
  if (status === "running") return "Being read now";
  if (status === "done") return "Finished";
  if (status === "partial") return "Finished, but some files could not be read";
  return "Could not be read";
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

function documentCard(document: KbDocument, files: KbFile[], jobs: KbJob[], loaded: Loaded): Card {
  const own = files.filter((file) => file.document_id === document.id);
  const job = jobs.find((row) => row.meta.results.some((result) => result.document_id === document.id));
  return {
    id: document.id,
    title: document.title,
    kind: kindText(document.file_name ?? own[0]?.name, document.kind),
    addedAt: document.created_at,
    chip: "ready",
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
  const pieces = job.meta.results.reduce((sum, result) => sum + (result.chunks ?? 0), 0);
  const details = [
    { label: "Size", value: sizeOf(own.filter((file) => !covered.has(file.id)), loaded.files) },
    { label: "Job state", value: jobStateText(job.status) },
    { label: "Searchable pieces", value: pieces ? String(pieces) : "None yet" },
  ];
  const trouble = problems(job);
  if (trouble) details.push({ label: "What went wrong", value: trouble });
  return {
    id: job.id,
    title: names.join(", ") || "Upload",
    kind: kindText(names[0], null),
    addedAt: job.created_at,
    chip,
    details,
    fileIds: own.map((file) => file.id).filter((id) => !covered.has(id)),
    foldedJobIds: [],
  };
}

/**
 * The cards of the page. Documents are In your library; queued or running jobs are Being read;
 * every other unfinished state (failed, partial and anything the server may add later) is
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
