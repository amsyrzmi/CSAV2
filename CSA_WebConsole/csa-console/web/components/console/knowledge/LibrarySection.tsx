"use client";

import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { del, post } from "@/lib/api";
import type { Agent, KbDocument, KbFile, KbGap, KbJob } from "@/lib/identity-contracts";
import {
  atLimit, countText, formatBytes, groupSources, kbStatusLine, kindText, LIBRARY_PREVIEW, LIST_LIMITS, looseFiles, pausedReason,
  pollingNeeded, type Card,
} from "@/lib/knowledge-view";
import { DetailsRow, TechDetailsProvider, TechnicalDetails, TechSwitch } from "../kit";
import { action, Notice, Panel, secondary } from "../ui";
import KnowledgeCard, { type CardActions, type Source } from "./KnowledgeCard";
import LockedNotice from "./LockedNotice";
import SectionError from "./SectionError";
import { agentStateOf, lockedSlot, useKbSlot, type Slot } from "./useKbSlot";

const box =
  "rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3";

/** What the open confirmation will do. A "files" delete is `linked` when its source belongs to a document. */
type Confirm =
  | { type: "files" | "documents"; id: string; name: string; linked: boolean }
  | { type: "dismiss"; id: string; name: string }
  | { type: "clear" };

type Removed = { storage_cleanup: "done" | "pending" };

const SWEEP_PENDING = "We'll finish clearing the stored file shortly.";

function confirmText(confirm: Confirm): string {
  if (confirm.type === "dismiss") return "This takes the message off your list. Nothing your assistant knows changes.";
  if (confirm.type === "clear") return "This takes every failed upload off your list. Nothing your assistant knows changes.";
  if (!confirm.linked) {
    return `Delete ${confirm.name}? This removes the uploaded file and closes any upload that was still waiting on it. An already-authorized download may finish.`;
  }
  return `Delete ${confirm.name}? Deleting a source with a linked document removes that document, its searchable chunks and every duplicate source linked to it. An already-authorized download may finish.`;
}

/**
 * What your assistant knows: the cards of everything added, with the original files behind them.
 * It reads every source list, refreshes by itself only while a file is being read, and owns the
 * delete confirmation and the feedback after it.
 */
export default function LibrarySection({
  query,
  canWrite,
  addHref,
  questionsHref,
}: {
  query: string;
  canWrite: boolean;
  addHref: string;
  questionsHref: string;
}) {
  const agents = useKbSlot<Agent[]>("agents", query);
  const files = useKbSlot<KbFile[]>("kb/files", query);
  const documents = useKbSlot<KbDocument[]>("kb/documents", query);
  const jobs = useKbSlot<KbJob[]>("kb/ingest-jobs", query);
  const gaps = useKbSlot<KbGap[]>("kb/gaps", query);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const [showAll, setShowAll] = useState(false);
  const readyList = useId();
  const feedback = useRef<HTMLDivElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const path = (resource: string) => `/console/v1/${resource}?${query}`;

  const agent = agents.slot.data?.find((row) => row.name === "kb_agent");
  const agentState = agentStateOf(agents.slot, agent);
  const writable = canWrite && agent?.capability?.active === true;
  const groups = useMemo(
    () => groupSources(files.slot.data ?? [], documents.slot.data ?? [], jobs.slot.data ?? [],
      { files: files.slot.data !== null, jobs: jobs.slot.data !== null }),
    [files.slot.data, documents.slot.data, jobs.slot.data],
  );
  const reading = pollingNeeded(groups);
  const locked = lockedSlot(documents.slot, jobs.slot, gaps.slot);

  const loadFiles = files.load;
  const loadDocuments = documents.load;
  const loadJobs = jobs.load;
  const loadGaps = gaps.load;
  const loadAgents = agents.load;
  const reloadSources = useCallback(() => {
    void loadFiles(); void loadDocuments(); void loadJobs();
  }, [loadFiles, loadDocuments, loadJobs]);
  const reloadAll = useCallback(() => {
    reloadSources(); void loadGaps(); void loadAgents();
  }, [reloadSources, loadGaps, loadAgents]);

  // The page refreshes by itself only while a file is being read, and stops when none is.
  useEffect(() => {
    if (!reading) return;
    const timer = setInterval(reloadSources, 5000);
    return () => clearInterval(timer);
  }, [reading, reloadSources]);
  useEffect(() => {
    if (error || message) feedback.current?.scrollIntoView({ block: "nearest" });
  }, [error, message]);
  useEffect(() => { if (confirm) cancel.current?.focus(); }, [confirm]);

  /** Runs one action: clears the old feedback, reports a server error in the one Notice and reads the sources again. */
  async function run(work: () => Promise<string>) {
    setBusy(true); setError(""); setMessage("");
    try {
      const done = await work();
      setConfirm(null); setMessage(done);
      reloadSources();
    } catch (cause) {
      // A refused action (for example a job that moved on) means the list on screen is stale.
      setError((cause as Error).message); setConfirm(null); reloadSources();
    } finally { setBusy(false); }
  }

  async function confirmed() {
    if (!confirm) return;
    if (confirm.type === "dismiss") {
      await run(async () => {
        await post(path(`kb/ingest-jobs/${confirm.id}/dismiss`));
        return "Removed from your list.";
      });
    } else if (confirm.type === "clear") {
      await run(async () => {
        const result = await post<{ dismissed: number }>(path("kb/ingest-jobs/dismiss-failed"));
        const count = result?.dismissed ?? 0;
        return count === 0 ? "Nothing was left to remove." : `Removed ${count} failed upload${count === 1 ? "" : "s"} from your list.`;
      });
    } else {
      const target = confirm;
      await run(async () => {
        const result = await del<Removed>(path(`kb/${target.type}/${target.id}`));
        const pending = result?.storage_cleanup === "pending";
        if (target.linked) {
          const done = "Deleted. Your assistant no longer uses this file.";
          return pending ? `${done} ${SWEEP_PENDING}` : done;
        }
        // "The stored file is gone" would contradict a sweep that is still to finish.
        return pending ? `Deleted. ${SWEEP_PENDING}` : "Deleted. The stored file is gone.";
      });
    }
  }

  async function retryJob(id: string) {
    await run(async () => {
      await post(path(`kb/ingest-jobs/${id}/retry`));
      return "Trying again. It will be read shortly.";
    });
  }

  async function cancelJob(id: string) {
    await run(async () => {
      await post(path(`kb/ingest-jobs/${id}/cancel`));
      return "Cancelled. Nothing was added.";
    });
  }

  const actions: CardActions = {
    canWrite, writable, busy, pausedReason: pausedReason(agentState, writable), addHref,
    agentOff: agent !== undefined && agent.capability?.active !== true,
    downloadHref: (id) => path(`kb/files/${id}/download`),
    onDelete: (type, id, name) => askDelete(type, id, name),
    onRetry: (id) => void retryJob(id),
    onDismiss: (id, name) => setConfirm({ type: "dismiss", id, name }),
    onCancel: (id) => void cancelJob(id),
  };
  const fileRows = files.slot.data ?? [];
  // A source that feeds a document (or a document itself) keeps the wording about linked documents; a stray upload does not.
  function askDelete(type: "files" | "documents", id: string, name: string) {
    const linked = type === "documents" || fileRows.find((file) => file.id === id)?.document_id != null;
    setConfirm({ type, id, name, linked });
  }
  const sourceOf = (card: Card, kind: "document" | "job"): Source | null => {
    if (kind === "document") {
      const document = documents.slot.data?.find((row) => row.id === card.id);
      const foldedJobs = card.foldedJobIds.flatMap((id) => jobs.slot.data?.filter((row) => row.id === id) ?? []);
      return document ? { type: "document", document, foldedJobs } : null;
    }
    const job = jobs.slot.data?.find((row) => row.id === card.id);
    return job ? { type: "job", job } : null;
  };
  const cardList = (cards: Card[], kind: "document" | "job") => (
    <ul className="space-y-3">
      {cards.map((card) => {
        const source = sourceOf(card, kind);
        return source && <KnowledgeCard key={card.id} card={card} source={source} files={fileRows} actions={actions} />;
      })}
    </ul>
  );
  const retry = (slot: Slot<unknown>, name: string, what: string, load: () => Promise<void>) => slot.error && (
    <SectionError name={name} what={what} message={slot.error} stale={slot.data !== null} onRetry={() => void load()} />
  );
  const loose = looseFiles(fileRows, groups);
  const counting = documents.slot.data === null && !documents.slot.error;
  const known = documents.slot.data ? documents.slot.data.filter((row) => row.is_active).length : null;
  const attention = jobs.slot.data ? groups.attention.length : null;
  const questions = gaps.slot.data ? gaps.slot.data.length : null;
  // Each list is read with a limit; a list exactly at it may hold more than it shows.
  const knownCapped = atLimit(documents.slot.data?.length ?? null, LIST_LIMITS.documents);
  const questionsCapped = atLimit(gaps.slot.data?.length ?? null, LIST_LIMITS.gaps);
  const needCapped = atLimit(jobs.slot.data?.length ?? null, LIST_LIMITS.jobs) || questionsCapped;
  const shownLibrary = showAll ? groups.library : groups.library.slice(0, LIBRARY_PREVIEW);

  if (locked) return <LockedNotice slot={locked} onRefresh={reloadAll} />;

  return <TechDetailsProvider><div className="space-y-6">
    {confirm && <Panel title={confirm.type === "dismiss" || confirm.type === "clear" ? "Confirm removal" : "Confirm deletion"}>
      <div data-testid="kb-confirm">
        <p>{confirmText(confirm)}</p>
        <div className="mt-4 flex gap-3">
          <button className={action} disabled={busy || !canWrite} onClick={() => void confirmed()}>
            {confirm.type === "dismiss" ? "Remove" : confirm.type === "clear" ? "Clear all" : "Confirm delete"}
          </button>
          <button ref={cancel} className={secondary} disabled={busy} onClick={() => setConfirm(null)}>Cancel</button>
        </div>
      </div>
    </Panel>}
    <div ref={feedback} className="space-y-3 empty:hidden">
      {error && <Notice error>{error}</Notice>}
      {message && <Notice>{message}</Notice>}
    </div>

    <section data-testid="kb-library" className="space-y-5">
      <p data-testid="kb-status" className="text-[length:var(--fs-text-lg)] font-medium">
        {counting ? "Counting what your assistant knows..." : kbStatusLine({ known, attention, questions }, { known: knownCapped, need: needCapped })}
      </p>
      <div className="flex flex-wrap items-center gap-3">
        {canWrite && <Link data-testid="kb-add-knowledge" className={action} href={addHref}>Add knowledge</Link>}
        <button className={secondary} disabled={busy} onClick={reloadAll}>Refresh business knowledge</button>
        <TechSwitch testId="kb-tech-all" />
      </div>
      {questions !== null && questions > 0 && <div data-testid="kb-questions-callout"
        className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border-control)] bg-[var(--fs-surface-blue)] p-3">
        <p className="font-medium">Questions your assistant couldn&apos;t answer: {countText(questions, questionsCapped)}</p>
        <Link className={secondary} href={questionsHref}>{canWrite ? "Add an answer" : "See the questions"}</Link>
      </div>}
      {retry(jobs.slot, "jobs", "the files being read", jobs.load)}
      {groups.attention.length > 0 && <div data-testid="kb-attention" className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h3 className="font-semibold">Needs attention</h3>
          {canWrite && groups.attention.length > 1 && (
            <button type="button" className={secondary} disabled={busy} onClick={() => setConfirm({ type: "clear" })}>Clear all that failed</button>
          )}
        </div>
        {cardList(groups.attention, "job")}
      </div>}
      {groups.reading.length > 0 && <div data-testid="kb-reading" className="space-y-3">
        <h3 className="font-semibold">Being read</h3>
        {cardList(groups.reading, "job")}
      </div>}
      <div data-testid="kb-ready" className="space-y-3">
        <h3 className="font-semibold">In your library</h3>
        {retry(documents.slot, "documents", "your library", documents.load)}
        {documents.slot.data === null && !documents.slot.error && <Notice>Loading business knowledge...</Notice>}
        {documents.slot.data && !documents.slot.data.length && <div className="text-sm">
          <p>No files yet.</p>
          {canWrite && <p className="mt-1 text-[color:var(--fs-text-secondary)]">
            <Link data-testid="kb-empty-add-link" className="font-medium text-[color:var(--fs-primary)] underline" href={addHref}>Add a file</Link> and your assistant will read it.
          </p>}
        </div>}
        <div id={readyList}>{cardList(shownLibrary, "document")}</div>
        {groups.library.length > LIBRARY_PREVIEW && <button type="button" className={secondary}
          data-testid="kb-ready-show-all" aria-expanded={showAll} aria-controls={readyList}
          onClick={() => setShowAll((current) => !current)}>
          {showAll ? "Show fewer" : `Show all ${countText(groups.library.length, knownCapped)}`}
        </button>}
      </div>
      {retry(files.slot, "files", "the original files", files.load)}
      <div className={box}>
        <DetailsRow testId="kb-other-files" toggleLabel="other uploaded files" summary={<p className="font-medium">Other uploaded files</p>}>
          {!files.slot.data ? <p>{files.slot.error ? "The list of files could not be loaded." : "Loading..."}</p>
            : !files.slot.data.length ? <p>No uploaded sources. Text and legacy documents may have no downloadable source.</p>
            : !loose.length ? <p>Every uploaded file is shown on its card above.</p>
            : <div className="space-y-4">{loose.map((file) => <article key={file.id}>
              <p className="font-semibold">{file.name}</p>
              <p>{kindText(file.name, null)}, {formatBytes(file.size_bytes)}. {file.document_id ? "Read by your assistant" : "Not read yet"}</p>
              <TechnicalDetails testId={`kb-other-file-${file.id}-tech`}>
                <p>{file.media_type}, {file.size_bytes.toLocaleString()} bytes. {file.document_id ? `Document ${file.document_id}` : "No linked document yet."}</p>
              </TechnicalDetails>
              <div className="mt-2 flex flex-wrap gap-2">
                <a className={secondary} href={path(`kb/files/${file.id}/download`)}>Download original</a>
                {canWrite && <button className={secondary} disabled={busy} onClick={() => askDelete("files", file.id, file.name)}>Delete source</button>}
              </div>
            </article>)}</div>}
        </DetailsRow>
      </div>
    </section>
  </div></TechDetailsProvider>;
}
