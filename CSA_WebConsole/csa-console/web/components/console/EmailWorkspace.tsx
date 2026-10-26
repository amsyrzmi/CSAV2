"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { Me } from "@/lib/identity-contracts";
import { emailClient, type Draft, type EmailContext, type Thread, type ThreadDetail } from "@/lib/email-api";
import { usePublishContext } from "@/lib/page-context";
import { createProposal, type ProposalOut } from "@/lib/workspace-api";
import { ProposalCard } from "./ProposalCard";
import { action, field, secondary, Heading, Notice, Panel } from "./ui";

const categoryLabel: Record<string, string> = {
  customer_request: "Customer request", order_or_delivery: "Order / delivery", complaint: "Complaint",
  billing: "Billing", vendor_or_partner: "Vendor / partner", internal: "Internal",
  newsletter_or_promotion: "Newsletter / promotion", spam_or_phishing: "Spam / phishing", other: "Other",
};
const errorText: Record<string, string> = {
  connected: "Mailbox connected.",
  oauth_state_invalid: "The sign-in link expired or was already used. Start the connection again.",
  provider_denied: "Google reported that access was not granted.",
  scopes_not_granted: "Google did not grant read and compose access. Tick both permissions and try again.",
  assistant_disabled: "The email assistant is switched off for this tenant.",
  mailbox_not_connected: "Connect a mailbox first.",
  mailbox_reauthorization_required: "Google access expired. Disconnect and connect the mailbox again.",
  model_not_configured: "No model is configured for analysis.",
  provider_not_configured: "Google OAuth is not configured on this deployment.",
  draft_rejected: "The draft was rejected: check the recipient and remove line breaks from the subject.",
  draft_already_exported: "This draft was already saved to Gmail and can no longer be edited here.",
  thread_body_unavailable: "This thread has no readable text to analyse.",
  idempotency_conflict: "A different request already used this key. Reload and try again.",
};

/** Each tenant selection creates fresh state and transport; late responses cannot cross tenants. */
export function EmailWorkspace({ me, initialTenant = "", notice = "" }: {
  me: Me; initialTenant?: string; notice?: string;
}) {
  const [selection, setSelection] = useState(initialTenant || me.active_client_id || "");
  const [tenant, setTenant] = useState(me.role === "platform_admin" ? initialTenant : me.active_client_id ?? "");
  return <div className="space-y-6">
    <Heading title="Email assistant" description="Triage, summarise and draft replies for your own connected Gmail mailbox. Nothing is sent from here." />
    {me.role === "platform_admin" && <form className="flex flex-wrap items-end gap-3" onSubmit={event => {
      event.preventDefault(); setTenant(selection);
    }}><label className="text-sm">Tenant membership<select className={`${field} mt-1`} value={selection}
      onChange={e => setSelection(e.target.value)} required><option value="">Choose a tenant</option>
      {me.memberships.map(m => <option key={m.client_id} value={m.client_id}>{m.client_id}</option>)}
    </select></label><button className={secondary}>Open email</button></form>}
    {tenant ? <TenantEmail key={tenant} me={me} tenant={tenant} notice={notice} /> : <Notice>Select a tenant to continue.</Notice>}
  </div>;
}

function TenantEmail({ me, tenant, notice }: { me: Me; tenant: string; notice: string }) {
  const scope = me.role === "platform_admin" ? tenant : "";
  const client = useMemo(() => emailClient(scope), [scope]);
  const [context, setContext] = useState<EmailContext | null>(null);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [lastSync, setLastSync] = useState<string | null>(null);
  const [selected, setSelected] = useState<ThreadDetail | null>(null);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [tab, setTab] = useState<"inbox" | "drafts">("inbox");
  const [error, setError] = useState(errorText[notice] && notice !== "connected" ? errorText[notice] : "");
  const [message, setMessage] = useState(notice === "connected" ? errorText.connected : "");
  const [busy, setBusy] = useState(true);
  const inFlight = useRef(true);
  const writable = me.role !== "read_only";
  const [exporting, setExporting] = useState<{ draftId: string; proposal: ProposalOut } | null>(null);
  usePublishContext({ tab: "email", record: exporting ? { kind: "draft", id: exporting.draftId } : null });

  async function run<T>(work: () => Promise<T>, done?: (value: T) => void) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    try { const value = await work(); done?.(value); }
    catch (reason) {
      const code = reason instanceof Error ? reason.message : String(reason);
      setError(errorText[code] ?? `Request failed: ${code}`);
    } finally { inFlight.current = false; setBusy(false); }
  }
  async function reloadLists(ctx: EmailContext) {
    if (!ctx.enabled || !ctx.mailbox) { setThreads([]); setDrafts([]); return; }
    const [page, draftPage] = await Promise.all([client.threads(), client.drafts()]);
    setThreads(page.items); setLastSync(page.last_sync_at); setDrafts(draftPage.items);
  }
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const ctx = await client.context();
        if (!active) return;
        setContext(ctx);
        await reloadLists(ctx);
      } catch (reason) {
        if (active) setError(reason instanceof Error ? (errorText[reason.message] ?? reason.message) : "Unavailable");
      } finally { if (active) { inFlight.current = false; setBusy(false); } }
    })();
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client]);

  if (!context) return <Notice>{error || "Loading…"}</Notice>;
  const mailbox = context.mailbox;
  const connected = mailbox?.status === "connected";

  return <div className="space-y-6">
    {error && <Notice error>{error}</Notice>}
    {message && <Notice>{message}</Notice>}
    <Panel title="Mailbox">
      <p className="text-sm">
        {!context.enabled && "The email assistant is switched off for this tenant."}
        {context.enabled && !mailbox && "No mailbox connected for your account."}
        {context.enabled && mailbox && `Mailbox: ${mailbox.address ?? "(pending)"} · ${mailbox.status}` +
          (mailbox.last_sync_at ? ` · last synced ${new Date(mailbox.last_sync_at).toLocaleString()}` : "")}
      </p>
      <p className="mt-2 text-xs text-neutral-500">{context.disclosure}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {context.can_manage && <button className={secondary} disabled={busy} onClick={() => run(
          () => client.setEnabled(!context.enabled),
          async () => { const ctx = await client.context(); setContext(ctx); await reloadLists(ctx); setMessage(ctx.enabled ? "Assistant enabled." : "Assistant disabled."); },
        )}>{context.enabled ? "Switch off for tenant" : "Switch on for tenant"}</button>}
        {context.can_connect && !connected && <button className={action} disabled={busy} onClick={() => run(
          () => client.connect(), result => { window.location.assign(result.authorization_url); },
        )}>Connect Gmail</button>}
        {context.enabled && connected && writable && <>
          <button className={action} disabled={busy} onClick={() => run(
            () => client.sync(), async r => { await reloadLists(context); setMessage(`Synced ${String((r.detail as { threads?: number }).threads ?? 0)} threads.`); },
          )}>Sync inbox</button>
          <button className={secondary} disabled={busy} onClick={() => run(
            () => client.disconnect(), async () => { const ctx = await client.context(); setContext(ctx); setSelected(null); setMessage("Mailbox disconnected; cached threads remain readable."); },
          )}>Disconnect</button>
        </>}
      </div>
      {!context.provider_configured && <p className="mt-2 text-xs text-amber-700">Google OAuth is not configured on this deployment.</p>}
    </Panel>

    {context.enabled && mailbox && <>
      <div className="flex gap-2 text-sm">
        {(["inbox", "drafts"] as const).map(key => <button key={key} className={tab === key ? action : secondary}
          onClick={() => setTab(key)}>{key === "inbox" ? `Inbox (${threads.length})` : `Drafts (${drafts.length})`}</button>)}
      </div>

      {tab === "inbox" && <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <Panel title={`Threads${lastSync ? ` · synced ${new Date(lastSync).toLocaleString()}` : ""}`}>
          {threads.length === 0 && <p className="text-sm">No threads cached yet. Sync the inbox.</p>}
          <ul className="divide-y">
            {threads.map(thread => <li key={thread.thread_id}>
              <button className="w-full py-2 text-left text-sm hover:bg-neutral-50" onClick={() => run(
                () => client.thread(thread.thread_id), setSelected,
              )}>
                <div className="flex items-center justify-between gap-2">
                  <span className={thread.unread ? "font-semibold" : ""}>{thread.subject || "(no subject)"}</span>
                  <span className="shrink-0 text-xs">{thread.priority === "high" && <span className="rounded bg-red-100 px-1 text-red-800">high</span>} {thread.category && categoryLabel[thread.category]}</span>
                </div>
                <div className="text-xs text-neutral-500">{thread.sender}</div>
                <div className="truncate text-xs text-neutral-600">{thread.summary ?? thread.snippet}</div>
              </button>
            </li>)}
          </ul>
        </Panel>
        <Panel title={selected ? selected.subject || "(no subject)" : "Select a thread"}>
          {selected && <ThreadView detail={selected} busy={busy} writable={writable} modelConfigured={context.model_configured}
            onAnalyse={() => run(() => client.analyse(selected.thread_id), async d => { setSelected(d); setThreads(await client.threads().then(p => p.items)); })}
            onDraft={(instructions, language) => run(() => client.draft(selected.thread_id, instructions, language), async d => {
              setDrafts(await client.drafts().then(p => p.items)); setTab("drafts"); setMessage(`Draft created for ${d.recipient}.`);
            })} />}
        </Panel>
      </div>}

      {tab === "drafts" && <Panel title="Drafts">
        {exporting && <ProposalCard key={exporting.proposal.id} proposal={exporting.proposal} tenantId={scope}
          onSettled={next => { if (next.status === "executed") void run(() => client.drafts(), page => setDrafts(page.items)); }} />}
        {drafts.length === 0 && <p className="text-sm">No drafts yet. Analyse a thread and ask for a reply draft.</p>}
        <div className="space-y-4">
          {drafts.map(draft => <DraftEditor key={`${draft.id}:${draft.updated_at}`} draft={draft} busy={busy} writable={writable}
            onSave={body => run(() => client.updateDraft(draft.id, body), async () => { setDrafts(await client.drafts().then(p => p.items)); setMessage("Draft saved."); })}
            onExport={() => run(() => createProposal("email_export", { action: "email_export", draft_id: draft.id }, undefined, undefined, scope),
              proposal => setExporting({ draftId: draft.id, proposal }))}
            onDelete={() => run(() => client.deleteDraft(draft.id), async () => { setDrafts(await client.drafts().then(p => p.items)); })} />)}
        </div>
      </Panel>}
    </>}
  </div>;
}

function ThreadView({ detail, busy, writable, modelConfigured, onAnalyse, onDraft }: {
  detail: ThreadDetail; busy: boolean; writable: boolean; modelConfigured: boolean;
  onAnalyse: () => void; onDraft: (instructions: string, language: "auto" | "en" | "ms") => void;
}) {
  const [instructions, setInstructions] = useState("");
  const [language, setLanguage] = useState<"auto" | "en" | "ms">("auto");
  return <div className="space-y-3 text-sm">
    <div className="text-xs text-neutral-500">From {detail.sender}{detail.received_at ? ` · ${new Date(detail.received_at).toLocaleString()}` : ""}</div>
    {detail.summary ? <div className="rounded border border-neutral-200 bg-neutral-50 p-3">
      <div className="text-xs uppercase text-neutral-500">{detail.category && categoryLabel[detail.category]} · priority {detail.priority}</div>
      <p className="mt-1">{detail.summary}</p>
      {detail.action_items.length > 0 && <ul className="mt-2 list-disc pl-5">{detail.action_items.map(item => <li key={item}>{item}</li>)}</ul>}
    </div> : <p className="text-xs text-neutral-500">Not analysed yet.</p>}
    <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded border border-neutral-200 p-3 text-xs">{detail.body ?? detail.snippet}</pre>
    {writable && <div className="space-y-2">
      <button className={secondary} disabled={busy || !modelConfigured || !detail.has_body} onClick={onAnalyse}>{detail.summary ? "Re-analyse" : "Analyse"}</button>
      <form className="space-y-2" onSubmit={event => { event.preventDefault(); onDraft(instructions, language); }}>
        <label className="block text-sm">Instructions for the reply (optional)
          <textarea className={`${field} mt-1`} maxLength={1000} rows={2} value={instructions} onChange={e => setInstructions(e.target.value)}
            placeholder="e.g. apologise for the delay and say it ships on Monday" /></label>
        <label className="block text-sm">Language<select className={`${field} mt-1`} value={language} onChange={e => setLanguage(e.target.value as "auto" | "en" | "ms")}>
          <option value="auto">Match the customer</option><option value="en">English</option><option value="ms">Bahasa Melayu</option></select></label>
        <button className={action} disabled={busy || !modelConfigured || !detail.has_body}>Draft a reply</button>
      </form>
    </div>}
  </div>;
}

function DraftEditor({ draft, busy, writable, onSave, onExport, onDelete }: {
  draft: Draft; busy: boolean; writable: boolean;
  onSave: (body: { recipient: string; subject: string; body: string }) => void; onExport: () => void; onDelete: () => void;
}) {
  const [recipient, setRecipient] = useState(draft.recipient);
  const [subject, setSubject] = useState(draft.subject);
  const [body, setBody] = useState(draft.body);
  const locked = draft.status === "exported" || !writable;
  return <form className="space-y-2 rounded border border-neutral-200 p-3 text-sm" onSubmit={event => { event.preventDefault(); onSave({ recipient, subject, body }); }}>
    <div className="flex items-center justify-between text-xs text-neutral-500">
      <span>{draft.status === "exported" ? `Saved to Gmail drafts (${draft.gmail_draft_id})` : `${draft.source === "model" ? "Model draft" : "Edited draft"} · not sent`}</span>
      <span>{new Date(draft.updated_at).toLocaleString()}</span>
    </div>
    <label className="block">To<input className={`${field} mt-1`} value={recipient} maxLength={254} disabled={locked} onChange={e => setRecipient(e.target.value)} /></label>
    <label className="block">Subject<input className={`${field} mt-1`} value={subject} maxLength={200} disabled={locked} onChange={e => setSubject(e.target.value)} /></label>
    <label className="block">Body<textarea className={`${field} mt-1`} rows={8} maxLength={20000} disabled={locked} value={body} onChange={e => setBody(e.target.value)} /></label>
    {!locked && <div className="flex flex-wrap gap-2">
      <button className={secondary} disabled={busy}>Save edits</button>
      <button type="button" className={action} disabled={busy} onClick={onExport}>Save to my Gmail drafts</button>
      <button type="button" className={secondary} disabled={busy} onClick={onDelete}>Delete</button>
    </div>}
  </form>;
}
