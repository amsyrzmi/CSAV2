"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent } from "react";
import { useSearchParams } from "next/navigation";
import { api, del, post } from "@/lib/api";
import type { Agent, Client, KbDocument, KbFile, KbGap, KbJob, Me } from "@/lib/identity-contracts";
import { addedText, groupSources, looseFiles, pausedReason, pollingNeeded, type AgentState, type Card } from "@/lib/knowledge-view";
import type { KnowledgeSection } from "@/lib/navigation";
import { usePublishContext } from "@/lib/page-context";
import { DetailsRow, PageIntro } from "./kit";
import AddKnowledge from "./knowledge/AddKnowledge";
import { Maintenance, Settings } from "./knowledge/Disclosures";
import KnowledgeCard, { type CardActions, type Source } from "./knowledge/KnowledgeCard";
import SectionError from "./knowledge/SectionError";
import { useKbSlot, type Slot } from "./knowledge/useKbSlot";
import { action, field, Notice, Panel, secondary } from "./ui";

// `section` names the sub-page the route serves; the page body does not branch on it yet.
export function KnowledgeWorkspace({ me }: { me: Me; section?: KnowledgeSection }) {
  usePublishContext({ tab: "knowledge_base" });
  const search = useSearchParams();
  const platform = me.role === "platform_admin";
  const [clients, setClients] = useState<Client[] | null>(null);
  const [selected, setSelected] = useState(search.get("client_id") ?? "");
  const [error, setError] = useState("");
  useEffect(() => {
    if (!platform) return;
    const controller = new AbortController();
    api<Client[]>("/console/v1/admin/clients", { signal: controller.signal })
      .then(setClients).catch((cause: Error) => {
        if (!controller.signal.aborted) setError(cause.message);
      });
    return () => controller.abort();
  }, [platform]);
  const clientId = platform ? selected : me.active_client_id;
  return <>
    <h1 className="page-title mb-2 font-(family-name:--fs-font-heading) text-(length:--fs-text-page) font-semibold">Knowledge base</h1>
    <PageIntro>Tenant documents, original sources, ingestion jobs and unanswered questions.</PageIntro>
    {error && <Notice error>{error}</Notice>}
    {platform && <div className="mb-5">
      <label htmlFor="kb-tenant" className="mb-2 block text-sm">Tenant</label>
      <select id="kb-tenant" className={field} value={selected} onChange={(e) => setSelected(e.target.value)}>
        <option value="">{clients ? "Select a tenant" : "Loading tenants..."}</option>
        {clients?.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
      </select>
    </div>}
    {clientId ? <Knowledge key={clientId} query={platform ? `client_id=${encodeURIComponent(clientId)}` : ""}
      canWrite={me.role !== null && me.role !== "read_only"} servicesHref={platform ? "/services" : "/portal/services"} /> : <Notice>Select a tenant to continue.</Notice>}
  </>;
}

const box =
  "rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3";

type Confirm = { type: "files" | "documents"; id: string; name: string };

function agentStateOf(slot: Slot<Agent[]>, row: Agent | undefined): AgentState {
  if (row) return "ready";
  // Loaded, but the list has no kb_agent row: not a failed fetch.
  if (slot.data) return "missing";
  return slot.error ? "failed" : "loading";
}

function Knowledge({ query, canWrite, servicesHref }: { query: string; canWrite: boolean; servicesHref: string }) {
  const agents = useKbSlot<Agent[]>("agents", query);
  const files = useKbSlot<KbFile[]>("kb/files", query);
  const documents = useKbSlot<KbDocument[]>("kb/documents", query);
  const jobs = useKbSlot<KbJob[]>("kb/ingest-jobs", query);
  const gaps = useKbSlot<KbGap[]>("kb/gaps", query);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [selection, setSelection] = useState<{ files: File[]; key: string } | null>(null);
  const [confirm, setConfirm] = useState<Confirm | null>(null);
  const input = useRef<HTMLInputElement>(null);
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
  const locked = [documents.slot, jobs.slot, gaps.slot].find((slot) => slot.code === "package_inactive");

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

  function choose(event: ChangeEvent<HTMLInputElement>) {
    const chosen = Array.from(event.target.files ?? []);
    setError(""); setSelection(null);
    if (!chosen.length) return;
    if (chosen.length > 3 || chosen.some((file) => !/\.(pdf|docx|csv)$/i.test(file.name) || file.size === 0 || file.size > 10 * 1024 * 1024)) {
      setError("Select one to three non-empty PDF, DOCX or CSV files, at most 10 MB each.");
      event.target.value = ""; return;
    }
    setSelection({ files: chosen, key: crypto.randomUUID() });
  }
  async function upload() {
    if (!selection) return;
    setBusy(true); setError(""); setMessage("");
    const form = new FormData();
    for (const file of selection.files) form.append("files", file);
    try {
      const result = await api<{ job_id: string; duplicate_request: boolean }>(path("kb/uploads"), {
        method: "POST", headers: { "Idempotency-Key": selection.key }, body: form,
      });
      setMessage(`${result.duplicate_request ? "Existing" : "Queued"} job ${result.job_id}. Queued is not yet searchable.`);
      setSelection(null);
      if (input.current) input.current.value = "";
      reloadSources();
    } catch (cause) {
      setError(`${(cause as Error).message}. Retry keeps the same idempotency key; do not reselect files for an uncertain result.`);
    } finally { setBusy(false); }
  }
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
  async function resolve(id: string) {
    setBusy(true); setError("");
    try {
      await post(path(`kb/gaps/${id}/resolve`));
      void loadGaps();
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }
  async function reconcile() {
    setBusy(true); setError("");
    try {
      const result = await post<{ removed_files: number }>(path("kb/storage/reconcile"));
      setMessage(`Storage reconciled: ${result.removed_files} orphaned/deleted/partial files removed. Active sources are retained.`);
      reloadSources();
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }

  const actions: CardActions = {
    canWrite, writable, busy, pausedReason: pausedReason(agentState, writable),
    downloadHref: (id) => path(`kb/files/${id}/download`),
    onDelete: (type, id, name) => setConfirm({ type, id, name }),
    onAddAgain: () => { input.current?.scrollIntoView({ block: "center" }); input.current?.focus(); },
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

  if (locked) return <div className="space-y-5">
    <Notice error testId="kb-locked">{locked.error}</Notice>
    <button className={secondary} onClick={reloadAll}>Refresh knowledge base</button>
  </div>;

  return <div className="space-y-6">
    {canWrite && <AddKnowledge inputRef={input} agent={agentState} writable={writable} busy={busy}
      selection={selection?.files ?? null} onChange={choose} onUpload={upload} onRetryAgent={() => void loadAgents()} />}
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
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="font-(family-name:--fs-font-heading) text-lg font-semibold">What your assistant knows</h2>
        <button className={secondary} disabled={busy} onClick={reloadAll}>Refresh knowledge base</button>
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
        {documents.slot.data === null && !documents.slot.error && <Notice>Loading knowledge base...</Notice>}
        {documents.slot.data && !documents.slot.data.length && <div className="text-sm">
          <p>No ingested documents.</p>
          {canWrite && <p className="mt-1 text-[color:var(--fs-text-secondary)]">Add a file above and your assistant will read it.</p>}
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

    <section data-testid="kb-gaps" className="space-y-3">
      <h2 className="font-(family-name:--fs-font-heading) text-lg font-semibold">Unanswered questions</h2>
      <p className="text-sm text-[color:var(--fs-text-secondary)]">
        Customers asked these and your assistant had no answer. Add a file that covers them, then mark each one resolved.
      </p>
      {retry(gaps.slot, "gaps", "the unanswered questions", gaps.load)}
      {gaps.slot.data === null && !gaps.slot.error && <p className="text-sm">Loading...</p>}
      {gaps.slot.data && !gaps.slot.data.length && <p className="text-sm">No recorded unresolved gaps.</p>}
      {gaps.slot.data && gaps.slot.data.length > 0 && <ul className="divide-y divide-[color:var(--fs-border)] rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)]">
        {gaps.slot.data.map((gap) => <li key={gap.id} data-testid={`kb-gap-${gap.id}`} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 p-3 text-sm">
          <div className="flex min-w-0 flex-1 basis-56 items-start gap-3">
            <svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" className="mt-0.5 shrink-0 text-[color:var(--fs-text-secondary)]"><circle cx="12" cy="12" r="9" /></svg>
            <div className="min-w-0 [overflow-wrap:anywhere]">
              <p className="font-medium">{gap.query}</p>
              <p className="text-[color:var(--fs-text-secondary)]">{gap.hits} hits. Last seen: {addedText(gap.last_seen)}</p>
            </div>
          </div>
          {canWrite && <button className={secondary} disabled={busy || !writable} onClick={() => resolve(gap.id)}>Mark resolved</button>}
        </li>)}
      </ul>}
    </section>

    <Maintenance agent={agent ?? null} agentState={agentState} canWrite={canWrite} writable={writable} busy={busy}
      onReconcile={reconcile} onRetryAgent={() => void loadAgents()} />
    <Settings agent={agent ?? null} agentState={agentState} servicesHref={servicesHref} onRetryAgent={() => void loadAgents()} />
  </div>;
}
