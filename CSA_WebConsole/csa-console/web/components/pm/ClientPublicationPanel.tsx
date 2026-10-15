"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { api, del, post } from "@/lib/api";
import { Banner, Button, Card } from "@/components/ui/kit";
import { label, textarea } from "@/components/ui/input";

type Row = { id: string; title: string; revision_number?: number; current_revision_id?: string | null; current_revision?: { id: string } | null };
type PickData = { milestones: Row[]; tasks: { items: Row[] }; notes: { items: Row[] }; artifacts: Row[] };
type Selection = { summary: string; milestone_ids: string[]; task_ids: string[]; note_revision_ids: string[]; artifact_revisions: string[] };
type ClientRequest = { id: string; body: string; created_at: string };

export function ClientPublicationPanel({ projectId, scope }: { projectId: string; scope: string }) {
  const qc = useQueryClient();
  const [summary, setSummary] = useState("");
  const [selected, setSelected] = useState<Record<string, boolean>>({});
  const [preview, setPreview] = useState<Record<string, unknown> | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const base = `/api/admin/pm/projects/${projectId}`;
  const status = useQuery({ queryKey: ["client-pm-status"], queryFn: () => api<{ enabled: boolean }>("/api/admin/pm/client-portal/status"), enabled: scope === "client" });
  const portalEnabled = Boolean(status.data?.enabled);
  const current = useQuery({ queryKey: ["client-publication", projectId], queryFn: () => api<Record<string, unknown> | null>(`${base}/client-publication`), enabled: scope === "client" && portalEnabled });
  const requests = useQuery({ queryKey: ["client-pm-requests", projectId], queryFn: () => api<ClientRequest[]>(`${base}/client-requests`), enabled: scope === "client" && portalEnabled });
  const picks = useQuery({ queryKey: ["client-publication-options", projectId], queryFn: async () => {
    const [milestones, tasks, notes, artifacts] = await Promise.all([
      api<Row[]>(`${base}/milestones`), api<{ items: Row[] }>(`${base}/tasks?limit=200`), api<{ items: Row[] }>(`${base}/notes?limit=200`), api<Row[]>(`${base}/artifacts`),
    ]);
    return { milestones, tasks, notes, artifacts } as PickData;
  }, enabled: scope === "client" && portalEnabled });
  const entries = useMemo(() => {
    const p = picks.data;
    if (!p) return [] as { key: string; id: string; title: string; type: keyof Selection }[];
    return [
      ...p.milestones.map((r) => ({ key: `milestone:${r.id}`, id: r.id, title: r.title, type: "milestone_ids" as const })),
      ...p.tasks.items.map((r) => ({ key: `task:${r.id}`, id: r.id, title: r.title, type: "task_ids" as const })),
      ...p.notes.items.filter((r) => r.current_revision_id).map((r) => ({ key: `note:${r.id}`, id: r.current_revision_id!, title: `${r.title} · revision ${r.revision_number}`, type: "note_revision_ids" as const })),
      ...p.artifacts.filter((r) => r.current_revision_id || r.current_revision?.id).map((r) => ({ key: `artifact:${r.id}`, id: (r.current_revision_id || r.current_revision?.id)!, title: r.title, type: "artifact_revisions" as const })),
    ];
  }, [picks.data]);
  if (scope !== "client") return null;
  const payload = (): Selection => ({
    summary,
    milestone_ids: entries.filter((e) => e.type === "milestone_ids" && selected[e.key]).map((e) => e.id),
    task_ids: entries.filter((e) => e.type === "task_ids" && selected[e.key]).map((e) => e.id),
    note_revision_ids: entries.filter((e) => e.type === "note_revision_ids" && selected[e.key]).map((e) => e.id),
    artifact_revisions: entries.filter((e) => e.type === "artifact_revisions" && selected[e.key]).map((e) => e.id),
  });
  const act = async (fn: () => Promise<unknown>) => { setBusy(true); setError(""); try { await fn(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } };

  return <Card title="Client project sharing" description="Publish a reviewed snapshot of selected project records. Later edits stay private until you publish another revision.">
    {error && <Banner tone="error">{error}</Banner>}
    {!status.isLoading && !portalEnabled && <Banner tone="info">Client project sharing is disabled. Admin project management remains available.</Banner>}
    {portalEnabled && <div className="space-y-4">
      <div><label className={label} htmlFor="client-summary">Client progress summary</label><textarea id="client-summary" className={textarea} rows={3} maxLength={3000} value={summary} onChange={(e) => setSummary(e.target.value)} placeholder="Write and review the client-facing summary." /></div>
      {picks.isLoading ? <p className="text-sm text-ink-500">Loading shareable project records…</p> : <fieldset><legend className="mb-2 text-sm font-semibold text-ink-800">Select content to include</legend><div className="max-h-64 space-y-1 overflow-auto rounded-lg border border-ink-200 p-2">
        {entries.map((e) => <label key={e.key} className="flex items-center gap-2 rounded px-2 py-1.5 text-sm hover:bg-ink-50"><input type="checkbox" checked={!!selected[e.key]} onChange={(ev) => setSelected((s) => ({ ...s, [e.key]: ev.target.checked }))} /><span className="w-20 shrink-0 text-xs capitalize text-ink-500">{e.type.replace("_ids", "").replace("artifact_revisions", "artifact")}</span><span className="truncate">{e.title}</span></label>)}
        {!entries.length && <p className="p-2 text-sm text-ink-500">No milestones, tasks, notes, or artifacts to select yet.</p>}
      </div></fieldset>}
      <p className="text-xs text-amber-800">Published content can be downloaded or copied by clients. Unsharing revokes future portal access but cannot recall copies.</p>
      <div className="flex flex-wrap gap-2">
        <Button disabled={busy || !summary.trim()} onClick={() => act(async () => setPreview(await post<Record<string, unknown>>(`${base}/client-preview`, payload())))}>Preview as client</Button>
        <Button variant="primary" disabled={busy || !summary.trim()} onClick={() => act(async () => { await api(`${base}/client-publication`, { method: "PUT", body: JSON.stringify(payload()) }); setPreview(null); await qc.invalidateQueries({ queryKey: ["client-publication", projectId] }); })}>{current.data ? "Publish new revision" : "Publish to client"}</Button>
        {current.data && <Button variant="danger" disabled={busy} onClick={() => act(async () => { await del(`${base}/client-publication`); await qc.invalidateQueries({ queryKey: ["client-publication", projectId] }); })}>Unshare</Button>}
      </div>
      {current.data && <p className="text-xs text-ink-500">Latest publication revision {(current.data.revision as number)}{current.data.revoked_at ? " (revoked)" : " (currently shared)"}.</p>}
      {!!requests.data?.length && <section className="border-t border-ink-100 pt-4"><h3 className="mb-2 text-sm font-semibold">Client requests</h3><ul className="space-y-2">{requests.data.map((r) => <li key={r.id} className="rounded-lg bg-ink-50 p-3"><p className="whitespace-pre-wrap text-sm">{r.body}</p><time className="mt-1 block text-xs text-ink-400">{new Date(r.created_at).toLocaleString()}</time></li>)}</ul></section>}
      {preview && <section aria-label="Preview as client" className="rounded-lg border border-brand-200 bg-brand-50/50 p-4"><h3 className="mb-2 font-semibold">Preview as client</h3><pre className="max-h-80 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(preview, null, 2)}</pre></section>}
    </div>}
  </Card>;
}
