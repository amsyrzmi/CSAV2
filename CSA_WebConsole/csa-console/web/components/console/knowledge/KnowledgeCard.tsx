"use client";

import Link from "next/link";
import type { KbDocument, KbFile, KbJob } from "@/lib/identity-contracts";
import { addedText, attentionNote, chipLabel, jobActions, jobStateText, type Card, type Chip } from "@/lib/knowledge-view";
import StatusLabel, { type StatusTone } from "../fs/StatusLabel";
import { DetailsRow, TechnicalDetails, useTechOpen } from "../kit";
import { secondary } from "../ui";

const TONES: Record<Chip, StatusTone> = { ready: "success", reading: "info", needs_attention: "attention" };

export type Source =
  | { type: "document"; document: KbDocument; foldedJobs: KbJob[] }
  | { type: "job"; job: KbJob };

export type CardActions = {
  canWrite: boolean;
  writable: boolean;
  busy: boolean;
  downloadHref: (fileId: string) => string;
  onDelete: (type: "files" | "documents", id: string, name: string) => void;
  /** Where "Choose the file again" goes: the Add page, with the tenant query for an administrator. */
  addHref: string;
  /** Why adding files is paused, in plain words, or null. */
  pausedReason: string | null;
  /** True only when the knowledge assistant's row says it is switched off (not while it is still loading). */
  agentOff: boolean;
  onRetry: (jobId: string) => void;
  onDismiss: (jobId: string, name: string) => void;
  onCancel: (jobId: string) => void;
};

const box =
  "rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3";

function summaryLine(card: Card, source: Source, agentOff: boolean): string | null {
  if (source.type === "document") return source.document.is_active ? null : "An older version. Your assistant no longer uses it.";
  if (card.chip === "reading") {
    // A queued job waits for the knowledge assistant; with it switched off, nothing will pick the job up.
    const state = source.job.status === "queued" && agentOff
      ? "Paused: the knowledge assistant is switched off"
      : jobStateText(source.job.status);
    return `${state}. Your assistant cannot answer from it yet.`;
  }
  return attentionNote(source.job);
}

/** One document or job as a card: plain words and a chip, the file actions in its Details and the exact lines in Technical details. */
export default function KnowledgeCard({
  card,
  source,
  files,
  actions,
}: {
  card: Card;
  source: Source;
  files: KbFile[];
  actions: CardActions;
}) {
  const own = card.fileIds.flatMap((id) => files.filter((file) => file.id === id));
  const note = summaryLine(card, source, actions.agentOff);
  const job = source.type === "job" ? source.job : null;
  const jobTodo = job ? jobActions(job) : null;
  const testId = `kb-card-${card.id}`;
  const { canWrite, writable, busy } = actions;
  const [open, setOpen] = useTechOpen();
  return (
    <li className={box}>
      <DetailsRow
        testId={testId}
        toggleLabel={card.title}
        open={open}
        onOpenChange={setOpen}
        summary={
          <div className="min-w-0">
            <p data-testid={`${testId}-title`} className="font-medium">{card.title}</p>
            <p className="text-sm text-[color:var(--fs-text-secondary)]">
              {card.kind} · Added {addedText(card.addedAt)}
            </p>
            {note && <p className="mt-1 text-sm">{note}</p>}
            <div className="mt-1.5 flex flex-wrap items-center gap-2">
              <span data-testid={`${testId}-chip`}>
                <StatusLabel tone={TONES[card.chip]}>{chipLabel(card.chip)}</StatusLabel>
              </span>
              {job && jobTodo && canWrite && (
                <>
                  {jobTodo.canRetry && (
                    <button type="button" className={secondary} data-testid={`${testId}-retry`}
                      disabled={busy || actions.pausedReason !== null} onClick={() => actions.onRetry(job.id)}>Try again</button>
                  )}
                  {jobTodo.canRemove && (
                    <button type="button" className={secondary} data-testid={`${testId}-remove`}
                      disabled={busy} onClick={() => actions.onDismiss(job.id, card.title)}>Remove</button>
                  )}
                  {jobTodo.canCancel && (
                    <button type="button" className={secondary} data-testid={`${testId}-cancel`}
                      disabled={busy} onClick={() => actions.onCancel(job.id)}>Cancel</button>
                  )}
                  {card.chip === "needs_attention" && !job.original_stored && (
                    busy || !writable ? (
                      <button type="button" className={secondary} disabled>Choose the file again</button>
                    ) : (
                      <Link className={secondary} href={actions.addHref} data-testid={`${testId}-add-again`}>Choose the file again</Link>
                    )
                  )}
                </>
              )}
            </div>
            {card.chip === "needs_attention" && job && (jobTodo?.canRetry || !job.original_stored) && canWrite && actions.pausedReason && (
              <p data-testid={`${testId}-paused`} className="mt-1 text-sm text-[color:var(--fs-text-secondary)]">{actions.pausedReason}</p>
            )}
          </div>
        }
      >
        <dl data-testid={`${testId}-plain`} className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1">
          {card.plain.map((row) => (
            <div key={row.label} className="contents">
              <dt className="text-[color:var(--fs-text-secondary)]">{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
        </dl>
        {(own.length > 0 || (source.type === "document" && canWrite)) && (
          <div data-testid={`${testId}-actions`} className="mt-3 space-y-2">
            {own.map((file) => (
              <div key={file.id} className="flex flex-wrap items-center gap-2">
                <span className="min-w-0 font-medium [overflow-wrap:anywhere]">{file.name}</span>
                <a className={secondary} href={actions.downloadHref(file.id)}>Download original</a>
                {canWrite && (
                  <button type="button" className={secondary} disabled={busy}
                    onClick={() => actions.onDelete("files", file.id, file.name)}>Delete source</button>
                )}
              </div>
            ))}
            {source.type === "document" && canWrite && (
              <button type="button" className={secondary} disabled={busy}
                onClick={() => actions.onDelete("documents", source.document.id, source.document.title)}>Delete document</button>
            )}
          </div>
        )}
        <TechnicalDetails testId={`${testId}-tech`}>
          <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1">
            {card.details.map((row) => (
              <div key={row.label} className="contents">
                <dt className="text-[color:var(--fs-text-secondary)]">{row.label}</dt>
                <dd>{row.value}</dd>
              </div>
            ))}
          </dl>
          <div className="mt-3 space-y-1 border-t border-[color:var(--fs-border)] pt-3 text-[color:var(--fs-text-secondary)]">
            {source.type === "document" ? <DocumentLines document={source.document} /> : <JobLines job={source.job} />}
            {source.type === "document" && source.foldedJobs.map((job) => <div key={job.id} className="pt-2"><JobLines job={job} /></div>)}
            {own.length > 0 && <p className="pt-2 font-semibold text-[color:var(--fs-text)]">Original sources</p>}
            {own.map((file) => (
              <div key={file.id} className="pt-1">
                <p className="font-semibold text-[color:var(--fs-text)]">{file.name}</p>
                <p>
                  {file.media_type}, {file.size_bytes.toLocaleString()} bytes.{" "}
                  {file.document_id ? `Document ${file.document_id}` : "No linked document yet."}
                </p>
              </div>
            ))}
          </div>
        </TechnicalDetails>
      </DetailsRow>
    </li>
  );
}

function DocumentLines({ document }: { document: KbDocument }) {
  return (
    <>
      <p>
        Version {document.version}, {document.is_active ? "active" : "superseded"}, {document.chunk_count} chunks.
        Model: {document.embed_model ?? "Unlabelled"}. {document.category ?? "Uncategorised"} / {document.language} / {document.kind}.
      </p>
      <p>Source: {document.file_name ?? document.source_uri ?? "Text / legacy source"}</p>
    </>
  );
}

function JobLines({ job }: { job: KbJob }) {
  return (
    <>
      <p className="break-all font-semibold text-[color:var(--fs-text)]">{job.id}: {job.status} (attempts: {job.attempts})</p>
      <p>{job.files.map((file) => file.name).join(", ")}</p>
      {job.error && <p className="text-[color:var(--fs-danger)]">{job.error}</p>}
      {/* A retried job keeps its old results until a worker finishes it; they are not this run's. */}
      {(job.status === "queued" || job.status === "processing" ? [] : job.meta.results).map((result, index) => (
        <p key={index}>
          {result.file}: {result.status}{result.version !== null && `, version ${result.version}`}
          {result.chunks !== null && `, ${result.chunks} chunks`}{result.error && ` — ${result.error}`}
        </p>
      ))}
    </>
  );
}
