"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, del, post } from "@/lib/api";
import type { Agent, Client, KbDocument, KbFile, KbGap, KbJob, Me } from "@/lib/identity-contracts";
import { usePublishContext } from "@/lib/page-context";
import { action, field, Heading, Notice, Panel, secondary } from "./ui";

export function KnowledgeWorkspace({ me }: { me: Me }) {
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
    <Heading title="Knowledge base" description="Tenant documents, original sources, ingestion jobs and unanswered questions." />
    {error && <Notice error>{error}</Notice>}
    {platform && <div className="mb-5">
      <label htmlFor="kb-tenant" className="mb-2 block text-sm">Tenant</label>
      <select id="kb-tenant" className={field} value={selected} onChange={(e) => setSelected(e.target.value)}>
        <option value="">{clients ? "Select a tenant" : "Loading tenants..."}</option>
        {clients?.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
      </select>
    </div>}
    {clientId ? <Knowledge key={clientId} query={platform ? `client_id=${encodeURIComponent(clientId)}` : ""}
      canWrite={me.role !== null && me.role !== "read_only"} /> : <Notice>Select a tenant to continue.</Notice>}
  </>;
}

type Snapshot = { agent: Agent; files: KbFile[]; documents: KbDocument[]; jobs: KbJob[]; gaps: KbGap[] };

function Knowledge({ query, canWrite }: { query: string; canWrite: boolean }) {
  const [data, setData] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [loadError, setLoadError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [selection, setSelection] = useState<{ files: File[]; key: string } | null>(null);
  const [confirm, setConfirm] = useState<{ type: "files" | "documents"; id: string; name: string } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const path = (resource: string) => `/console/v1/${resource}?${query}`;
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const request = <T,>(resource: string) => api<T>(`/console/v1/${resource}?${query}`, { signal: controller.signal });
        const [agents, files, documents, jobs, gaps] = await Promise.all([
          request<Agent[]>("agents"), request<KbFile[]>("kb/files"),
          request<KbDocument[]>("kb/documents"),
          request<KbJob[]>("kb/ingest-jobs"), request<KbGap[]>("kb/gaps"),
        ]);
        if (controller.signal.aborted) return;
        const agent = agents.find((row) => row.name === "kb_agent");
        if (!agent) throw new Error("KB capability metadata is unavailable.");
        setData({ agent, files, documents, jobs, gaps });
        setLoadError("");
      } catch (cause) {
        if (!controller.signal.aborted) { setLoadError((cause as Error).message); setData(null); }
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 5000);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [query, revision]);
  const writable = canWrite && data?.agent.capability?.active === true;
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
      setRevision((value) => value + 1);
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
      setRevision((value) => value + 1);
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }
  async function resolve(id: string) {
    setBusy(true); setError("");
    try {
      await post(path(`kb/gaps/${id}/resolve`));
      setRevision((value) => value + 1);
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }
  async function reconcile() {
    setBusy(true); setError("");
    try {
      const result = await post<{ removed_files: number }>(path("kb/storage/reconcile"));
      setMessage(`Storage reconciled: ${result.removed_files} orphaned/deleted/partial files removed. Active sources are retained.`);
      setRevision((value) => value + 1);
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="space-y-5">
    {error && <Notice error>{error}</Notice>}
    {loadError && <Notice error>{loadError}</Notice>}
    {message && <Notice>{message}</Notice>}
    <button className={secondary} disabled={busy} onClick={() => setRevision((value) => value + 1)}>Refresh knowledge base</button>
    <Notice>Polling every 5 seconds. Lists show the latest 50 documents/gaps, 20 jobs and 100 sources.
      Uploads need configured storage and an ingestion worker. A queued job is not proof the worker is running.</Notice>
    {!data ? <Notice>{loadError ? "KB state unavailable. Refresh to retry." : "Loading knowledge base..."}</Notice> : <>
      <Panel title="Embedding compatibility">
        <p>{data.agent.detail}</p>
        <p className="mt-2 text-sm">Model: {data.agent.embedding_model ?? "Not configured"}.
          Compatible chunks: {data.agent.compatible_chunks ?? "Unknown"}.
          Incompatible chunks: {data.agent.incompatible_chunks ?? "Unknown"}.
          No legacy FAQ files are imported automatically.</p>
        {!writable && <p className="mt-3 text-sm">{canWrite ? "Writes require both KB subscription and tenant switch on." : "Read-only access: upload, deletion and gap resolution are unavailable."}</p>}
      </Panel>
      {canWrite && <Panel title="Upload sources">
        <label htmlFor="kb-files" className="mb-2 block text-sm">One to three PDF, DOCX or CSV files, at most 10 MB each</label>
        <input ref={input} id="kb-files" type="file" multiple accept=".pdf,.docx,.csv" disabled={busy || !writable}
          onChange={(event) => {
            const files = Array.from(event.target.files ?? []);
            setError(""); setSelection(null);
            if (!files.length) return;
            if (files.length > 3 || files.some((file) => !/\.(pdf|docx|csv)$/i.test(file.name) || file.size === 0 || file.size > 10 * 1024 * 1024)) {
              setError("Select one to three non-empty PDF, DOCX or CSV files, at most 10 MB each.");
              event.target.value = ""; return;
            }
            setSelection({ files, key: crypto.randomUUID() });
          }} />
        {selection && <p className="mt-3 text-sm">{selection.files.map((file) => file.name).join(", ")}</p>}
        <button className={`${action} mt-4`} disabled={busy || !writable || !selection} onClick={upload}>{busy ? "Working..." : "Queue upload / retry"}</button>
      </Panel>}
      {confirm && <Panel title="Confirm deletion">
        <p>Delete {confirm.name}? Deleting a source with a linked document removes that document, its searchable chunks and every duplicate source linked to it. An already-authorized download may finish.</p>
        <div className="mt-4 flex gap-3">
          <button className={action} disabled={busy || !writable} onClick={remove}>Confirm delete</button>
          <button className={secondary} disabled={busy} onClick={() => setConfirm(null)}>Cancel</button>
        </div>
      </Panel>}
      <Panel title="Ingestion jobs">
        {!data.jobs.length && <p>No recorded jobs.</p>}
        {data.jobs.map((job) => <article key={job.id} className="mb-4 border-b border-ink-200 pb-4 text-sm">
          <p className="break-all font-semibold">{job.id}: {job.status} (attempts: {job.attempts})</p>
          <p>{job.files.map((file) => file.name).join(", ")}</p>
          {job.error && <p className="text-red-800">{job.error}</p>}
          {job.meta.results.map((result, index) => <p key={index}>
            {result.file}: {result.status}{result.version !== null && `, version ${result.version}`}
            {result.chunks !== null && `, ${result.chunks} chunks`}{result.error && ` — ${result.error}`}
          </p>)}
        </article>)}
      </Panel>
      <Panel title="Original sources">
        {canWrite && <button className={`${secondary} mb-4`} disabled={busy} onClick={reconcile}>Reconcile orphaned storage</button>}
        {!data.files.length && <p>No uploaded sources. Text and legacy documents may have no downloadable source.</p>}
        {data.files.map((file) => <article key={file.id} className="mb-4 border-b border-ink-200 pb-4 text-sm">
          <p className="font-semibold">{file.name}</p>
          <p>{file.media_type}, {file.size_bytes.toLocaleString()} bytes. {file.document_id ? `Document ${file.document_id}` : "No linked document yet."}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            <a className={secondary} href={path(`kb/files/${file.id}/download`)}>Download original</a>
            {canWrite && <button className={secondary} disabled={busy || !writable} onClick={() => setConfirm({ type: "files", id: file.id, name: file.name })}>Delete source</button>}
          </div>
        </article>)}
      </Panel>
      <Panel title="Documents and versions">
        {!data.documents.length && <p>No ingested documents.</p>}
        {data.documents.map((document) => <article key={document.id} className="mb-4 border-b border-ink-200 pb-4 text-sm">
          <h3 className="font-semibold">{document.title}</h3>
          <p>Version {document.version}, {document.is_active ? "active" : "superseded"}, {document.chunk_count} chunks.
            Model: {document.embed_model ?? "Unlabelled"}. {document.category ?? "Uncategorised"} / {document.language} / {document.kind}.</p>
          <p>Source: {document.file_name ?? document.source_uri ?? "Text / legacy source"}</p>
          {canWrite && <button className={`${secondary} mt-2`} disabled={busy || !writable}
            onClick={() => setConfirm({ type: "documents", id: document.id, name: document.title })}>Delete document</button>}
        </article>)}
      </Panel>
      <Panel title="Unanswered questions">
        {!data.gaps.length && <p>No recorded unresolved gaps.</p>}
        {data.gaps.map((gap) => <article key={gap.id} className="mb-4 border-b border-ink-200 pb-4 text-sm">
          <p>{gap.query}</p><p>{gap.hits} hits. Last seen: {new Date(gap.last_seen).toLocaleString()}</p>
          {canWrite && <button className={`${secondary} mt-2`} disabled={busy || !writable} onClick={() => resolve(gap.id)}>Mark resolved</button>}
        </article>)}
      </Panel>
    </>}
  </div>;
}
