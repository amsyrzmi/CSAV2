"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { del } from "@/lib/api";
import type { Agent, KbDocument, KbFile, KbGap, KbJob } from "@/lib/identity-contracts";
import { groupSources, looseFiles, pausedReason, pollingNeeded, type Card } from "@/lib/knowledge-view";
import { DetailsRow } from "../kit";
import { action, Notice, Panel, secondary } from "../ui";
import KnowledgeCard, { type CardActions, type Source } from "./KnowledgeCard";
import LockedNotice from "./LockedNotice";
import SectionError from "./SectionError";
import { agentStateOf, lockedSlot, useKbSlot, type Slot } from "./useKbSlot";

const box =
  "rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3";

type Confirm = { type: "files" | "documents"; id: string; name: string };

/**
 * What your assistant knows: the cards of everything added, with the original files behind them.
 * It reads every source list, refreshes by itself only while a file is being read, and owns the
 * delete confirmation and the feedback after it.
 */
export default function LibrarySection({
  query,
  canWrite,
  addHref,
}: {
  query: string;
  canWrite: boolean;
  addHref: string;
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

  async function remove() {
    if (!confirm) return;
    setBusy(true); setError(""); setMessage("");
    try {
      await del(path(`kb/${confirm.type}/${confirm.id}`));
      setConfirm(null); setMessage("Deleted. Queued work cannot recreate this source or its deleted content.");
      reloadSources();
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }

  const actions: CardActions = {
    canWrite, writable, busy, pausedReason: pausedReason(agentState, writable), addHref,
    downloadHref: (id) => path(`kb/files/${id}/download`),
    onDelete: (type, id, name) => setConfirm({ type, id, name }),
  };
  const fileRows = files.slot.data ?? [];
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

  if (locked) return <LockedNotice slot={locked} onRefresh={reloadAll} />;

  return <div className="space-y-6">
    {confirm && <Panel title="Confirm deletion">
      <div data-testid="kb-confirm">
        <p>Delete {confirm.name}? Deleting a source with a linked document removes that document, its searchable chunks and every duplicate source linked to it. An already-authorized download may finish.</p>
        <div className="mt-4 flex gap-3">
          <button className={action} disabled={busy || !writable} onClick={remove}>Confirm delete</button>
          <button ref={cancel} className={secondary} disabled={busy} onClick={() => setConfirm(null)}>Cancel</button>
        </div>
      </div>
    </Panel>}
    <div ref={feedback} className="space-y-3 empty:hidden">
      {error && <Notice error>{error}</Notice>}
      {message && <Notice>{message}</Notice>}
    </div>

    <section data-testid="kb-library" className="space-y-5">
      <div className="flex flex-wrap items-center justify-end gap-3">
        <button className={secondary} disabled={busy} onClick={reloadAll}>Refresh business knowledge</button>
      </div>
      {retry(jobs.slot, "jobs", "the files being read", jobs.load)}
      {groups.attention.length > 0 && <div data-testid="kb-attention" className="space-y-3">
        <h3 className="font-semibold">Needs attention</h3>
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
          <p>No ingested documents.</p>
          {canWrite && <p className="mt-1 text-[color:var(--fs-text-secondary)]">
            <Link data-testid="kb-empty-add-link" className="font-medium text-[color:var(--fs-primary)] underline" href={addHref}>Add a file</Link> and your assistant will read it.
          </p>}
        </div>}
        {cardList(groups.library, "document")}
      </div>
      {retry(files.slot, "files", "the original files", files.load)}
      <div className={box}>
        <DetailsRow testId="kb-other-files" toggleLabel="other uploaded files" summary={<p className="font-medium">Other uploaded files</p>}>
          {!files.slot.data ? <p>{files.slot.error ? "The list of files could not be loaded." : "Loading..."}</p>
            : !files.slot.data.length ? <p>No uploaded sources. Text and legacy documents may have no downloadable source.</p>
            : !loose.length ? <p>Every uploaded file is shown on its card above.</p>
            : <div className="space-y-4">{loose.map((file) => <article key={file.id}>
              <p className="font-semibold">{file.name}</p>
              <p>{file.media_type}, {file.size_bytes.toLocaleString()} bytes. {file.document_id ? `Document ${file.document_id}` : "No linked document yet."}</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <a className={secondary} href={path(`kb/files/${file.id}/download`)}>Download original</a>
                {canWrite && <button className={secondary} disabled={busy || !writable} onClick={() => setConfirm({ type: "files", id: file.id, name: file.name })}>Delete source</button>}
              </div>
            </article>)}</div>}
        </DetailsRow>
      </div>
    </section>
  </div>;
}
