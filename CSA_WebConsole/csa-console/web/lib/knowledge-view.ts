/**
 * Pure rules for the Knowledge base page: which cards the sources make, what chip each one
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
};

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

function sizeOf(files: KbFile[]): string {
  return files.length ? formatBytes(files.reduce((sum, file) => sum + file.size_bytes, 0)) : "Not stored";
}

function documentCard(document: KbDocument, files: KbFile[], jobs: KbJob[]): Card {
  const own = files.filter((file) => file.document_id === document.id);
  const job = jobs.find((row) => row.meta.results.some((result) => result.document_id === document.id));
  return {
    id: document.id,
    title: document.title,
    kind: kindText(document.file_name ?? own[0]?.name, document.kind),
    addedAt: document.created_at,
    chip: "ready",
    details: [
      { label: "Size", value: sizeOf(own) },
      { label: "Searchable pieces", value: String(document.chunk_count) },
      { label: "Reading job", value: job ? jobStateText(job.status) : "Not recorded" },
    ],
    fileIds: own.map((file) => file.id),
  };
}

function problems(job: KbJob): string {
  const lines: string[] = [];
  if (job.error) lines.push(job.error);
  for (const result of job.meta.results) if (result.error) lines.push(`${result.file}: ${result.error}`);
  return lines.join("; ");
}

function jobCard(job: KbJob, chip: Chip, files: KbFile[]): Card {
  const known = new Map(files.map((file) => [file.id, file] as const));
  const own = job.files.flatMap((entry) => (entry.file_id && known.has(entry.file_id) ? [known.get(entry.file_id)!] : []));
  const names = job.files.map((entry) => entry.name);
  const pieces = job.meta.results.reduce((sum, result) => sum + (result.chunks ?? 0), 0);
  const details = [
    { label: "Size", value: sizeOf(own) },
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
    fileIds: own.map((file) => file.id),
  };
}

/**
 * The cards of the page. Documents are In your library; queued or running jobs are Being read;
 * every other unfinished state (failed, partial and anything the server may add later) is
 * Needs attention, so a new state is never hidden. A finished job makes no card: its document
 * is already in the library.
 */
export function groupSources(files: KbFile[], documents: KbDocument[], jobs: KbJob[]): Groups {
  const groups: Groups = { library: [], reading: [], attention: [] };
  for (const document of documents) groups.library.push(documentCard(document, files, jobs));
  for (const job of jobs) {
    const chip = jobChip(job.status);
    if (chip === "reading") groups.reading.push(jobCard(job, chip, files));
    else if (chip === "needs_attention") groups.attention.push(jobCard(job, chip, files));
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
