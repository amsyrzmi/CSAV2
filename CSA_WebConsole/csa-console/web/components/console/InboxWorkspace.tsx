"use client";

import { useEffect, useState } from "react";
import { api, ApiError } from "@/lib/api";
import type { Client, Me } from "@/lib/identity-contracts";
import { action, field, Heading, Notice, Panel, secondary } from "./ui";

type Conversation = {
  id: string; channel: "telegram"; status: string; owned_by: string | null;
  ai_muted_until: string | null; human_muted: boolean; inbox_version: number; opened_at: string;
};
type Message = {
  id: string; direction: string; author: string; body: string;
  created_at: string; delivery_status: string | null;
};
type Approval = {
  id: number; conversation_id: string; draft: string; reason: string | null;
  status: string; expires_at: string;
};
type Page<T> = { items: T[]; next_cursor: string | number | null };
type Result = {
  outcome: "applied" | "already_handled" | "expired" | "not_found";
  queued: boolean; warnings: string[]; status: string | null;
};
type Pending = { path: string; payload: Record<string, unknown>; onSuccess?: () => void };

function usePages<T>(path: string, pages: number, revision: number) {
  const [data, setData] = useState<Page<T>>({ items: [], next_cursor: null });
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const items: T[] = [];
        let cursor: string | number | null = null;
        for (let index = 0; index < pages; index++) {
          const page: Page<T> = await api(`${path}${cursor ? `&before=${cursor}` : ""}`, {
            signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]),
          });
          items.push(...page.items); cursor = page.next_cursor;
          if (cursor === null) break;
        }
        if (!controller.signal.aborted) { setData({ items, next_cursor: cursor }); setError(""); }
      } catch (cause) {
        if (!controller.signal.aborted) setError((cause as Error).message);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 3000);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [path, pages, revision]);
  return { ...data, error };
}

export function InboxWorkspace({ me }: { me: Me }) {
  const [selected, setSelected] = useState("");
  const [clients, setClients] = useState<Client[]>([]);
  const [error, setError] = useState("");
  const platform = me.role === "platform_admin";
  const permitted = platform || me.role === "tenant_admin" || me.role === "tenant_staff";
  useEffect(() => {
    if (!platform) return;
    const controller = new AbortController();
    api<Client[]>("/console/v1/admin/clients", { signal: controller.signal }).then(setClients)
      .catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [platform]);
  if (!permitted) return <Notice error>Staff inbox access requires an administrator or staff role.</Notice>;
  const tenant = platform ? selected : me.active_client_id;
  return <>
    <Heading title="Customer inbox" description="Telegram customer conversations and reply approvals for your team." />
    <Notice>Take over before replying. AI stays paused until release. Takeover waits for a send already in progress;
      queued automatic replies are suppressed. Queued replies still need delivery. Common personal details are redacted.</Notice>
    {error && <Notice error>{error}</Notice>}
    {platform && <label className="my-5 block">Tenant
      <select className={field} value={selected} onChange={(event) => setSelected(event.target.value)}>
        <option value="">Select a tenant</option>
        {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
      </select>
    </label>}
    {tenant ? <Inbox key={tenant} query={platform ? `client_id=${encodeURIComponent(tenant)}` : ""} />
      : <Notice>Select a tenant to open its inbox.</Notice>}
  </>;
}

function Inbox({ query }: { query: string }) {
  const [selected, setSelected] = useState("");
  const [pages, setPages] = useState(1);
  const [approvalPages, setApprovalPages] = useState(1);
  const [includeHandled, setIncludeHandled] = useState(false);
  const [revision, setRevision] = useState(0);
  const [pending, setPending] = useState<Pending | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const conversations = usePages<Conversation>(`/console/v1/inbox/conversations?${query}`, pages, revision);
  const approvals = usePages<Approval>(`/console/v1/inbox/approvals?${query}&include_handled=${includeHandled}`, approvalPages, revision);

  async function submit(request: Pending) {
    setPending(request); setBusy(true); setError(""); setNotice("");
    try {
      const result = await api<Result>(request.path, {
        method: "POST", body: JSON.stringify(request.payload), signal: AbortSignal.timeout(15_000),
      });
      setPending(null);
      request.onSuccess?.();
      setNotice(result.outcome === "already_handled" ? "Already handled. Refreshed the current state."
        : result.outcome === "expired" ? `Approval expired.${result.queued ? " Follow-up reply queued." : " No reply queued."}`
        : result.queued ? "Reply queued. Delivery status appears in the conversation."
        : result.status ? `Reply ${result.status}. No new delivery queued.` : "Conversation updated.");
      if (result.warnings.length) setNotice((value) => `${value} Edit warning: ${result.warnings.join(", ")}`);
      setRevision((value) => value + 1);
    } catch (cause) {
      const definitive = cause instanceof ApiError && cause.status >= 400 && cause.status < 500;
      if (definitive) setPending(null);
      setError(`${(cause as Error).message}${definitive ? ". Refreshed; review before acting again." : ". Outcome uncertain. Retry keeps the same request key. After reload, inspect history before sending again."}`);
      setRevision((value) => value + 1);
    } finally { setBusy(false); }
  }
  const blocked = busy || pending !== null;
  return <div className="mt-6 space-y-5">
    {(error || conversations.error || approvals.error) && <Notice error>{error || conversations.error || approvals.error}</Notice>}
    {notice && <Notice>{notice}</Notice>}
    {pending && !busy && <button className={action} onClick={() => void submit(pending)}>Retry the same action</button>}
    <div className="grid gap-5 lg:grid-cols-[18rem_1fr]">
      <Panel title="Conversations">
        {!conversations.items.length && <p className="text-sm">No customer conversations found.</p>}
        {conversations.items.map((item) => <button key={item.id} disabled={blocked}
          className={`${secondary} mb-2 w-full flex-col items-start break-all text-left`}
          onClick={() => setSelected(item.id)} aria-pressed={selected === item.id}>
          <span>{item.id.slice(0, 8)} · Telegram</span>
          <span className="text-xs">{item.human_muted ? "Human support · AI paused" : "AI"} · {item.status}</span>
        </button>)}
        {conversations.next_cursor && <button className={secondary} onClick={() => setPages((value) => value + 1)}>Older conversations</button>}
      </Panel>
      {selected ? <ConversationDetail key={selected} id={selected} query={query} revision={revision}
        blocked={blocked} submit={submit} /> : <Notice>Select a conversation or an approval source.</Notice>}
    </div>
    <Panel title="Reply approvals">
      <label className="mb-4 flex items-center gap-2 text-sm">
        <input type="checkbox" checked={includeHandled} onChange={(event) => setIncludeHandled(event.target.checked)} />
        Include handled approvals
      </label>
      {!approvals.items.length && <p className="text-sm">No customer reply approvals found.</p>}
      <div className="space-y-4">{approvals.items.map((approval) => <ApprovalCard key={approval.id} approval={approval}
        blocked={blocked} source={() => setSelected(approval.conversation_id)}
        decide={(decision, text) => submit({ path: `/console/v1/inbox/approvals/${approval.id}/decide?${query}`,
          payload: { request_key: crypto.randomUUID(), decision, ...(decision === "edit" ? { text } : {}) } })} />)}</div>
      {approvals.next_cursor && <button className={`${secondary} mt-4`} onClick={() => setApprovalPages((value) => value + 1)}>Older approvals</button>}
    </Panel>
  </div>;
}

function ConversationDetail({ id, query, revision, blocked, submit }: {
  id: string; query: string; revision: number; blocked: boolean; submit: (request: Pending) => Promise<void>;
}) {
  const [current, setCurrent] = useState<Conversation | null>(null);
  const [error, setError] = useState("");
  const [text, setText] = useState("");
  const [pages, setPages] = useState(1);
  const base = `/console/v1/inbox/conversations/${id}`;
  const messages = usePages<Message>(`${base}/messages?${query}`, pages, revision);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const value = await api<Conversation>(`${base}?${query}`, {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]),
        });
        if (!controller.signal.aborted) { setCurrent(value); setError(""); }
      } catch (cause) { if (!controller.signal.aborted) setError((cause as Error).message); }
      finally { if (!controller.signal.aborted) timer = setTimeout(refresh, 3000); }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [base, query, revision]);
  function act(operation: string) {
    if (!current) return;
    return submit({ path: `${base}/${operation}?${query}`,
      onSuccess: operation === "reply" ? () => setText("") : undefined, payload: {
      request_key: crypto.randomUUID(), expected_version: current.inbox_version,
      ...(operation === "reply" ? { body: text } : {}),
    } });
  }
  return <Panel title="Conversation">
    {(error || messages.error) && <Notice error>{error || messages.error}</Notice>}
    <p className="mb-4 break-all text-xs text-ink-500">{id}</p>
    {current && <>
      <p className="mb-3 text-sm">{current.human_muted ? "Human support owns this conversation. AI paused." : "AI owns this conversation."}</p>
      {current.human_muted && current.ai_muted_until && <>
        <p className="mb-3 text-sm">Temporary pause until {new Date(current.ai_muted_until).toLocaleString()}.
          Take over indefinitely to keep AI paused until deliberate release.</p>
        <button className={`${secondary} mr-2`} disabled={blocked || !!error}
          onClick={() => void act("takeover")}>Take over indefinitely</button>
      </>}
      <button className={secondary} disabled={blocked || !!error} onClick={() => {
        if (!current.human_muted || window.confirm("Release this conversation to AI? Older paused turns stay suppressed."))
          void act(current.human_muted ? "release" : "takeover");
      }}>{current.human_muted ? "Release to AI" : "Take over"}</button>
    </>}
    <ol className="my-5 space-y-3">{[...messages.items].reverse().map((message) => <li key={message.id} className="rounded-md border border-ink-200 p-3">
      <p className="text-xs text-ink-500">{message.direction === "inbound" ? "Customer" : message.author === "human_support" ? "Staff" : "Assistant"}
        {message.delivery_status ? ` · ${message.delivery_status === "pending" ? "queued" : message.delivery_status}` : ""}</p>
      <p className="whitespace-pre-wrap break-words text-sm">{message.body}</p>
    </li>)}</ol>
    {messages.next_cursor && <button className={secondary} onClick={() => setPages((value) => value + 1)}>Older messages</button>}
    {current?.human_muted && <form className="mt-5 space-y-3" onSubmit={(event) => { event.preventDefault(); void act("reply"); }}>
      <label className="block text-sm">Human reply
        <textarea className={`${field} h-28 py-2`} value={text} maxLength={2000} disabled={blocked}
          onChange={(event) => setText(event.target.value)} required />
      </label>
      <button className={action} disabled={blocked || !!error || !text.trim()}>Queue reply</button>
    </form>}
  </Panel>;
}

function ApprovalCard({ approval, blocked, source, decide }: {
  approval: Approval; blocked: boolean; source: () => void;
  decide: (decision: "approve" | "reject" | "edit", text?: string) => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const pending = approval.status === "pending";
  return <article className="rounded-md border border-ink-200 p-4">
    <p className="text-xs text-ink-500">Reply #{approval.id} · {approval.status} · Expires {new Date(approval.expires_at).toLocaleString()}</p>
    <p className="my-3 whitespace-pre-wrap break-words text-sm">{approval.draft}</p>
    <p className="mb-3 text-xs">{approval.reason}</p>
    <div className="flex flex-wrap gap-2">
      <button className={secondary} disabled={blocked} onClick={source}>View conversation</button>
      {pending && <>
        <button className={action} disabled={blocked} onClick={() => { if (window.confirm("Approve this draft for Telegram delivery?")) void decide("approve"); }}>Approve</button>
        <button className={secondary} disabled={blocked} onClick={() => { if (window.confirm("Reject this draft? The normal follow-up reply is queued unless AI is paused.")) void decide("reject"); }}>Reject</button>
        <button className={secondary} disabled={blocked} onClick={() => { setEditing(!editing); setText(approval.draft); }}>Edit</button>
      </>}
    </div>
    {editing && pending && <form className="mt-4 space-y-3" onSubmit={(event) => { event.preventDefault(); if (window.confirm("Queue this edited reply for Telegram delivery?")) void decide("edit", text); }}>
      <label className="block text-sm">Replacement reply
        <textarea className={`${field} h-28 py-2`} value={text} maxLength={2000} disabled={blocked}
          onChange={(event) => setText(event.target.value)} required />
      </label>
      <button className={action} disabled={blocked || !text.trim()}>Queue edited reply</button>
    </form>}
  </article>;
}
