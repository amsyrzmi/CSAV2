"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import type { Me } from "@/lib/identity-contracts";
import {
  emailClient, type AttentionPage, type Draft, type DraftEdit, type EmailContext, type Thread, type ThreadDetail,
} from "@/lib/email-api";
import {
  addressesLookValid, attentionEmpty, attentionNote, attentionProblem, emailErrorSentence, formatAddresses,
  formatWhen, isReply, needsSync, parseAddresses, rereadThread,
} from "@/lib/email-recipients";
import { usePublishContext } from "@/lib/page-context";
import { createProposal, type ProposalOut } from "@/lib/workspace-api";
import { EmailPreview } from "./EmailPreview";
import { ProposalCard } from "./ProposalCard";
import { action, field, secondary, Heading, Notice, Panel } from "./ui";

const categoryLabel: Record<string, string> = {
  customer_request: "Customer request", order_or_delivery: "Order / delivery", complaint: "Complaint",
  billing: "Billing", vendor_or_partner: "Vendor / partner", internal: "Internal",
  newsletter_or_promotion: "Newsletter / promotion", spam_or_phishing: "Spam / phishing", other: "Other",
};
const CONNECTED = "Mailbox connected.";

/** The plain sentence for a failed request; codes without one keep the generic form. */
function errorFor(reason: unknown): string {
  const sentence = emailErrorSentence(reason);
  if (sentence) return sentence;
  const code = reason instanceof ApiError ? reason.code : reason instanceof Error ? reason.message : String(reason);
  return `Request failed: ${code}`;
}

type AttentionState =
  | { state: "off" } | { state: "loading" } | { state: "ready"; page: AttentionPage } | { state: "error"; text: string };

/** Each tenant selection creates fresh state and transport; late responses cannot cross tenants. */
export function EmailWorkspace({ me, initialTenant = "", notice = "" }: {
  me: Me; initialTenant?: string; notice?: string;
}) {
  const [selection, setSelection] = useState(initialTenant || me.active_client_id || "");
  const [tenant, setTenant] = useState(me.role === "platform_admin" ? initialTenant : me.active_client_id ?? "");
  return <div className="space-y-6">
    <Heading title="Email assistant" description="Triage, summarise, draft and send email from your own connected Gmail mailbox. Nothing is sent until you confirm it." />
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
  // The open thread for code that runs after a sync, where `selected` may be a stale closure.
  const openThread = useRef<string | null>(null);
  useEffect(() => { openThread.current = selected?.thread_id ?? null; }, [selected]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [tab, setTab] = useState<"inbox" | "drafts">("inbox");
  const [error, setError] = useState(notice && notice !== "connected" ? emailErrorSentence({ code: notice }) ?? "" : "");
  const [message, setMessage] = useState(notice === "connected" ? CONNECTED : "");
  const [more, setMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [attention, setAttention] = useState<AttentionState>({ state: "off" });
  const [confirming, setConfirming] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(true);
  const inFlight = useRef(true);
  const writable = me.role !== "read_only";
  const [exporting, setExporting] = useState<{ draftId: string; proposal: ProposalOut } | null>(null);
  usePublishContext({ tab: "email", record: exporting ? { kind: "draft", id: exporting.draftId } : null });

  async function run<T>(work: () => Promise<T>, done?: (value: T) => void | Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    try { const value = await work(); await done?.(value); }
    catch (reason) { setError(errorFor(reason)); }
    finally { inFlight.current = false; setBusy(false); }
  }
  async function reloadDrafts() { setDrafts((await client.drafts()).items); }
  /** Context and lists after a sync: the sync time and whether older email remains both move. */
  async function refreshAll() {
    const ctx = await client.context();
    setContext(ctx); setMore(ctx.more_available); await reloadLists(ctx);
    await refreshOpen();
    return ctx;
  }
  /**
   * Re-read the open thread so its body is current; a thread the sync removed is closed. Any
   * other failure keeps the detail already shown, so the sync's own result still reports.
   */
  async function refreshOpen() {
    const id = openThread.current;
    let detail: ThreadDetail | null;
    try { detail = await rereadThread(id, threadId => client.thread(threadId)); }
    catch { return; }
    // Only replace what is still open: the user may have picked another thread meanwhile.
    setSelected(current => (current?.thread_id === id ? detail : current));
  }
  function loadAttention(ctx: EmailContext) {
    // The summary is not offered to read-only members; it needs a mailbox to read from.
    if (!ctx.enabled || !ctx.mailbox || !writable) { setAttention({ state: "off" }); return; }
    setAttention({ state: "loading" });
    client.attention().then(
      page => setAttention({ state: "ready", page }),
      (reason: unknown) => {
        const problem = attentionProblem(reason);
        setAttention(problem === "hide" ? { state: "off" } : { state: "error", text: problem ?? errorFor(reason) });
      },
    );
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
        let ctx = await client.context();
        if (!active) return;
        setContext(ctx); setMore(ctx.more_available);
        await reloadLists(ctx);
        // Sync on open when the last sync is older than five minutes.
        if (active && ctx.enabled && ctx.mailbox?.status === "connected" && writable &&
          needsSync(ctx.last_synced_at ?? null, Date.now())) {
          try { await client.sync(); if (active) ctx = await refreshAll(); }
          catch (reason) { if (active) setError(errorFor(reason)); }
        }
        if (active) loadAttention(ctx);
      } catch (reason) {
        if (active) setError(reason instanceof ApiError ? (emailErrorSentence(reason) ?? reason.message) : reason instanceof Error ? reason.message : "Unavailable");
      } finally { if (active) { inFlight.current = false; setBusy(false); } }
    })();
    return () => { active = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client]);

  if (!context) return <Notice>{error || "Loading…"}</Notice>;
  const mailbox = context.mailbox;
  const connected = mailbox?.status === "connected";

  async function loadMore() {
    setLoadingMore(true);
    await run(() => client.syncMore(), async receipt => {
      const done = receipt.detail.done === true;
      setThreads((await client.threads()).items);
      await refreshOpen();
      setMore(!done);
      const count = Number(receipt.detail.threads ?? 0);
      setMessage(count ? `Loaded ${count} older ${count === 1 ? "thread" : "threads"}.` : "");
    });
    setLoadingMore(false);
  }
  function checkAddresses(edit: DraftEdit): boolean {
    if (addressesLookValid([...edit.to, ...edit.cc, ...edit.bcc])) return true;
    setError(emailErrorSentence({ code: "recipients_invalid" }) ?? ""); setMessage("");
    return false;
  }
  /** Save unsaved edits first, so the dialog shows the stored draft that `/send` will use. */
  function startSend(draft: Draft, edit: DraftEdit, dirty: boolean) {
    if (!checkAddresses(edit)) return;
    void run(async () => (dirty ? client.updateDraft(draft.id, edit) : draft), saved => {
      if (dirty) setDrafts(list => list.map(item => (item.id === saved.id ? saved : item)));
      if (saved.needs_staff_input) { setError(emailErrorSentence({ code: "draft_needs_staff_input" }) ?? ""); return; }
      setConfirming(saved);
    });
  }
  function confirmSend(draft: Draft) {
    void run(async () => {
      try { return await client.send(draft.id, draft.version); }
      catch (reason) {
        // An unknown outcome leaves the draft `sending`: show its release button.
        setConfirming(null); await reloadDrafts().catch(() => undefined);
        throw reason;
      }
    }, async result => {
      setConfirming(null); await reloadDrafts();
      setMessage(result.sent_at ? `Email sent from your Gmail at ${formatWhen(result.sent_at)}.` : "Email sent from your Gmail.");
    });
  }

  return <div className="space-y-6">
    {error && <Notice error>{error}</Notice>}
    {message && <Notice>{message}</Notice>}
    {attention.state !== "off" && <AttentionPanel attention={attention} busy={busy}
      onOpen={id => run(() => client.thread(id), detail => { setSelected(detail); setTab("inbox"); })} />}
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
            () => client.sync(), async r => { const ctx = await refreshAll(); setMessage(`Synced ${String((r.detail as { threads?: number }).threads ?? 0)} threads.`); loadAttention(ctx); },
          )}>Sync inbox</button>
          <button className={secondary} disabled={busy} onClick={() => run(
            () => client.disconnect(), async () => { const ctx = await client.context(); setContext(ctx); setSelected(null); setAttention({ state: "off" }); setMessage("Mailbox disconnected; cached threads remain readable."); },
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
          {connected && writable && (more
            ? <button type="button" data-testid="email-load-more" className={`${secondary} mt-3`} disabled={busy}
              onClick={() => void loadMore()}>{loadingMore ? "Loading…" : "Load older email"}</button>
            : <p data-testid="email-load-more" className="mt-3 text-xs text-ink-500">No older email in the last 90 days</p>)}
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
            onSave={body => { if (checkAddresses(body)) void run(() => client.updateDraft(draft.id, body), async () => { await reloadDrafts(); setMessage("Draft saved."); }); }}
            onSend={(body, dirty) => startSend(draft, body, dirty)}
            onRelease={() => run(() => client.release(draft.id), async () => { await reloadDrafts(); setMessage("You can edit and send this email again."); })}
            onExport={() => run(() => createProposal("email_export", { action: "email_export", draft_id: draft.id }, undefined, undefined, scope),
              proposal => setExporting({ draftId: draft.id, proposal }))}
            onDelete={() => run(() => client.deleteDraft(draft.id), async () => { setDrafts(await client.drafts().then(p => p.items)); })} />)}
        </div>
      </Panel>}
    </>}
    {confirming && <SendDialog draft={confirming} busy={busy} from={mailbox?.address ?? null}
      onConfirm={() => confirmSend(confirming)} onClose={() => setConfirming(null)} />}
  </div>;
}

function AttentionPanel({ attention, busy, onOpen }: {
  attention: Exclude<AttentionState, { state: "off" }>; busy: boolean; onOpen: (threadId: string) => void;
}) {
  const page = attention.state === "ready" ? attention.page : null;
  const note = page ? attentionNote(page) : null;
  return <div data-testid="email-attention">
    <Panel title="What needs my attention">
      {page?.as_of && <p className="-mt-3 mb-3 text-xs text-ink-500">as of {formatWhen(page.as_of)}</p>}
      {attention.state === "loading" && <p className="text-sm">Loading…</p>}
      {attention.state === "error" && <p className="text-sm">{attention.text}</p>}
      {page && page.items.length === 0 && <p className="text-sm">{attentionEmpty(page)}</p>}
      {page && page.items.length > 0 && <ul className="divide-y">
        {page.items.map(item => <li key={item.thread_id} className="py-2 text-sm">
          <button type="button" className="w-full text-left hover:bg-neutral-50" disabled={busy} onClick={() => onOpen(item.thread_id)}>
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium">{item.subject || "(no subject)"}</span>
              {item.priority === "high" && <span className="shrink-0 rounded bg-red-100 px-1 text-xs text-red-800">high</span>}
            </div>
            <div className="text-xs text-neutral-500">{item.sender}</div>
            <p className="mt-1">{item.summary}</p>
          </button>
          {item.next_steps.length > 0 && <ul className="mt-1 list-disc pl-5 text-xs">{item.next_steps.map(step => <li key={step}>{step}</li>)}</ul>}
        </li>)}
      </ul>}
      {note && <p className="mt-2 text-xs text-ink-500">{note}</p>}
    </Panel>
  </div>;
}

/** Shows exactly the stored draft that will go out; sending uses the version the server gave it. */
function SendDialog({ draft, busy, from, onConfirm, onClose }: {
  draft: Draft; busy: boolean; from: string | null; onConfirm: () => void; onClose: () => void;
}) {
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
    onKeyDown={event => { if (event.key === "Escape" && !busy) onClose(); }}>
    <div role="dialog" aria-modal="true" aria-labelledby="email-send-title"
      className="card max-h-[85vh] w-full max-w-lg space-y-4 overflow-y-auto bg-white p-5 sm:p-6">
      <h2 id="email-send-title" className="text-base font-semibold">Send this email?</h2>
      <EmailPreview draft={draft} />
      <p className="text-sm">It goes from your Gmail{from ? ` (${from})` : ""} straight away. You can&apos;t take it back.</p>
      <div className="flex flex-wrap gap-2">
        <button type="button" data-testid="email-send-confirm" className={action} disabled={busy} onClick={onConfirm}>
          {busy ? "Sending…" : "Send now"}</button>
        <button type="button" className={secondary} disabled={busy} autoFocus onClick={onClose}>Keep editing</button>
      </div>
    </div>
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

function DraftEditor({ draft, busy, writable, onSave, onSend, onExport, onDelete, onRelease }: {
  draft: Draft; busy: boolean; writable: boolean;
  onSave: (body: DraftEdit) => void; onSend: (body: DraftEdit, dirty: boolean) => void;
  onExport: () => void; onDelete: () => void; onRelease: () => void;
}) {
  const [to, setTo] = useState(formatAddresses(draft.to));
  const [cc, setCc] = useState(formatAddresses(draft.cc));
  const [bcc, setBcc] = useState(formatAddresses(draft.bcc));
  const [subject, setSubject] = useState(draft.subject);
  const [body, setBody] = useState(draft.body);
  const locked = draft.status !== "draft" || !writable;
  const edit: DraftEdit = { to: parseAddresses(to), cc: parseAddresses(cc), bcc: parseAddresses(bcc), subject, body };
  const same = (a: string[], b: string[]) => a.length === b.length && a.every((item, i) => item === b[i]);
  const dirty = !same(edit.to, draft.to) || !same(edit.cc, draft.cc) || !same(edit.bcc, draft.bcc) ||
    subject !== draft.subject || body !== draft.body;
  const kind = isReply(draft) ? "Reply" : "New email";
  const state = draft.status === "exported" ? `Saved to Gmail drafts (${draft.gmail_draft_id})`
    : draft.status === "sent" ? "Sent" : draft.status === "sending" ? "Sending not confirmed"
    : `${draft.source === "model" ? "Model draft" : "Edited draft"} · not sent`;
  return <form className="space-y-2 rounded border border-neutral-200 p-3 text-sm" onSubmit={event => { event.preventDefault(); onSave(edit); }}>
    <div className="flex items-center justify-between text-xs text-neutral-500">
      <span>{kind} · {state}</span>
      <span>{formatWhen(draft.updated_at)}</span>
    </div>
    {draft.status === "sent" && draft.sent_at && <p data-testid="email-sent-at" className="font-medium">Sent at {formatWhen(draft.sent_at)}</p>}
    {draft.status === "sending" && <div className="space-y-2">
      <Notice error>{emailErrorSentence({ code: "send_outcome_unknown" })}</Notice>
      {writable && <button type="button" data-testid="email-release" className={secondary} disabled={busy} onClick={onRelease}>
        I checked — it wasn&apos;t sent</button>}
    </div>}
    <label className="block">To<input data-testid="email-to" className={`${field} mt-1`} value={to} disabled={locked} onChange={e => setTo(e.target.value)} /></label>
    <label className="block">CC<input data-testid="email-cc" className={`${field} mt-1`} value={cc} disabled={locked} onChange={e => setCc(e.target.value)} placeholder="Optional" /></label>
    <label className="block">BCC<input data-testid="email-bcc" className={`${field} mt-1`} value={bcc} disabled={locked} onChange={e => setBcc(e.target.value)} placeholder="Optional" /></label>
    <label className="block">Subject<input className={`${field} mt-1`} value={subject} maxLength={200} disabled={locked} onChange={e => setSubject(e.target.value)} /></label>
    <label className="block">Body<textarea className={`${field} mt-1 h-auto`} rows={8} maxLength={20000} disabled={locked} value={body} onChange={e => setBody(e.target.value)} /></label>
    {!locked && draft.needs_staff_input && !dirty && <p className="text-xs text-amber-700">{emailErrorSentence({ code: "draft_needs_staff_input" })}</p>}
    {!locked && <div className="flex flex-wrap gap-2">
      <button type="button" data-testid="email-send" className={action} disabled={busy} onClick={() => onSend(edit, dirty)}>Send</button>
      <button className={secondary} disabled={busy || !dirty}>Save edits</button>
      <button type="button" className={secondary} disabled={busy || dirty} onClick={onExport}>Save to my Gmail drafts</button>
      <button type="button" className={secondary} disabled={busy} onClick={onDelete}>Delete</button>
    </div>}
  </form>;
}
