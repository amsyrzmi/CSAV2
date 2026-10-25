"use client";

import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { api, ApiError } from "@/lib/api";
import type { Client, Me } from "@/lib/identity-contracts";
import type { Page, PendingSandboxTurn, SandboxThread, SandboxTurn } from "@/lib/sandbox-chat";
import { sandboxDelayed, sandboxStatus } from "@/lib/sandbox-chat";
import { VoiceConversation } from "./VoiceConversation";
import { RecordedVoice } from "./RecordedVoice";
import { localDictation, speakLocal } from "@/lib/hr-voice";
import { action, field, Heading, Notice, Panel, secondary } from "./ui";

export function SandboxWorkspace({ me }: { me: Me }) {
  const search = useSearchParams();
  const platform = me.role === "platform_admin";
  const [selected, setSelected] = useState(search.get("client_id") ?? "");
  const [clients, setClients] = useState<Client[]>([]);
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
  const tenant = platform ? selected : me.active_client_id;
  return <>
    <Heading title="Sandbox assistant" description="Private to your account and selected tenant. Knowledge and product-information questions only." />
    <Notice>No customer records, orders, bookings, tickets, invoices, payments, email or staff escalation.
      No external customer sends. Business confirmations and attachments are unavailable.
      Browser dictation fills a draft for review; read aloud uses a local browser voice.
      Use Knowledge base for document uploads. Archive hides history; it does not delete it.</Notice>
    {error && <Notice error>{error}</Notice>}
    {platform && <div className="my-5">
      <label htmlFor="sandbox-tenant" className="block text-sm">Tenant</label>
      <select id="sandbox-tenant" className={field} value={selected} onChange={(event) => setSelected(event.target.value)}>
        <option value="">Select a tenant</option>
        {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
      </select>
    </div>}
    {tenant ? <Sandbox key={tenant} query={platform ? `client_id=${encodeURIComponent(tenant)}` : ""}
      canWrite={me.role !== null && me.role !== "read_only"} /> : <Notice>Select a tenant to continue.</Notice>}
  </>;
}

function Sandbox({ query, canWrite }: { query: string; canWrite: boolean }) {
  const [threads, setThreads] = useState<SandboxThread[]>([]);
  const [thread, setThread] = useState("");
  const [threadCursor, setThreadCursor] = useState<number | null>(null);
  const [turns, setTurns] = useState<SandboxTurn[]>([]);
  const [turnCursor, setTurnCursor] = useState<number | null>(null);
  const [historyPages, setHistoryPages] = useState(1);
  const [text, setText] = useState("");
  const [pending, setPending] = useState<PendingSandboxTurn | null>(null);
  const creationKey = useRef<string | null>(null);
  const observed = useRef(new Set<string>());
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [revision, setRevision] = useState(0);
  const [threadRevision, setThreadRevision] = useState(0);
  const [now, setNow] = useState(Date.now);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const [listening, setListening] = useState(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [conversationVoiceBusy, setConversationVoiceBusy] = useState(false);
  const [voiceLanguage, setVoiceLanguage] = useState("en-US");
  const stopDictation = useRef<(() => void) | null>(null);
  const alive = useRef(true);
  useEffect(() => () => {
    stopDictation.current?.();
    window.speechSynthesis?.cancel();
  }, []);
  function stopVoice() {
    stopDictation.current?.();
    stopDictation.current = null;
    setListening(false);
  }
  function dictate() {
    setError("");
    stopVoice();
    try {
      stopDictation.current = localDictation(
        value => setText(value),
        () => setListening(false),
        problem => { setError(problem); setListening(false); },
        voiceLanguage,
      );
      setListening(true);
    } catch (cause) { setError((cause as Error).message); }
  }
  function readAloud(body: string) {
    setError("");
    try { speakLocal(body); }
    catch (cause) { setError((cause as Error).message); }
  }
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);
  const path = (suffix: string, cursor?: number) =>
    `/console/v1/chat/conversations${suffix}?${query}${cursor ? `&before=${cursor}` : ""}`;
  const mutate = <T,>(suffix: string, body: unknown = {}) => api<T>(path(suffix), {
    method: "POST", body: JSON.stringify(body), signal: AbortSignal.timeout(15_000),
  });

  useEffect(() => {
    const controller = new AbortController();
    api<Page<SandboxThread>>(`/console/v1/chat/conversations?${query}`, { signal: controller.signal })
      .then((page) => { setThreads(page.items); setThreadCursor(page.next_cursor); })
      .catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [query, threadRevision]);

  useEffect(() => {
    if (!thread) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const items: SandboxTurn[] = [];
        let cursor: number | null = null;
        for (let index = 0; index < historyPages; index++) {
          const page: Page<SandboxTurn> = await api(`/console/v1/chat/conversations/${thread}/turns?${query}${cursor ? `&before=${cursor}` : ""}`,
            { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]) });
          items.push(...page.items);
          cursor = page.next_cursor;
          if (cursor === null) break;
        }
        if (controller.signal.aborted) return;
        setTurns(items.sort((a, b) => a.id - b.id));
        items.forEach((turn) => observed.current.add(turn.request_key));
        setTurnCursor(cursor);
        setPending((value) => value?.source === "text" && items.some((turn) => turn.request_key === value.request_key) ? null : value);
        setPollError("");
        setNow(Date.now());
      } catch (cause) {
        if (!controller.signal.aborted) setPollError((cause as Error).message);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 2500);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [thread, query, revision, historyPages]);

  async function create() {
    creationKey.current ??= crypto.randomUUID();
    setCreating(true); setBusy(true); setError("");
    try {
      const result = await mutate<SandboxThread>("", { request_key: creationKey.current });
      if (!alive.current) return;
      creationKey.current = null; setCreating(false);
      select(result.id); setThreadRevision((value) => value + 1);
    } catch (cause) {
      if (alive.current) setError(`${(cause as Error).message}. Retry creation keeps the same request key.`);
    } finally { if (alive.current) setBusy(false); }
  }
  function select(id: string) {
    stopVoice();
    window.speechSynthesis?.cancel();
    observed.current.clear();
    setThread(id); setTurns([]); setHistoryPages(1); setTurnCursor(null);
    setPollError(""); setConfirmArchive(false); setText("");
  }
  async function send() {
    if (pending?.source === "voice") return;
    const draft = pending ?? { source: "text" as const, request_key: crypto.randomUUID(), body: text };
    setPending(draft); setText(""); setBusy(true); setError("");
    try {
      await mutate(`/${thread}/turns`, { request_key: draft.request_key, body: draft.body });
      if (!alive.current) return;
      setPending(null); setText(""); setRevision((value) => value + 1);
    } catch (cause) {
      if (!alive.current) return;
      if (observed.current.has(draft.request_key)) {
        setPending(null);
        setError("This request is already confirmed in history. Follow its recorded status; do not resend.");
        return;
      }
      if (cause instanceof ApiError && cause.status < 500 && cause.status !== 408) {
        setPending(null); setText(draft.body);
        setError(`Request rejected (${cause.status}): ${cause.message}. Check history and correct the error before sending again.`);
      } else {
        setError(`${(cause as Error).message}. Result may be uncertain: check history or retry this exact request, not a new message.`);
      }
    } finally { if (alive.current) setBusy(false); }
  }
  async function archive() {
    setBusy(true); setError("");
    try {
      await mutate(`/${thread}/archive`);
      if (!alive.current) return;
      select(""); setThreadRevision((value) => value + 1);
    } catch (cause) { if (alive.current) setError((cause as Error).message); }
    finally { if (alive.current) setBusy(false); }
  }
  async function olderThreads() {
    if (!threadCursor) return;
    setBusy(true);
    try {
      const page = await api<Page<SandboxThread>>(path("", threadCursor));
      if (!alive.current) return;
      setThreads((items) => [...items, ...page.items.filter((item) => !items.some((old) => old.id === item.id))]);
      setThreadCursor(page.next_cursor);
    } catch (cause) { if (alive.current) setError((cause as Error).message); }
    finally { if (alive.current) setBusy(false); }
  }
  const locked = busy || pending !== null || creating || listening || voiceBusy || conversationVoiceBusy;
  return <div className="mt-5 space-y-5">
    {error && <Notice error>{error}</Notice>}
    {!canWrite && <Notice>Read-only membership: sending, creating and archiving conversations are disabled.</Notice>}
    <Panel title="Your private conversations">
      <div className="flex flex-wrap gap-3">
        <button className={action} disabled={!canWrite || busy || pending !== null || voiceBusy || conversationVoiceBusy || listening} onClick={() => void create()}>
          {creating ? "Retry conversation creation" : "New sandbox conversation"}</button>
        <label className="sr-only" htmlFor="sandbox-conversation">Conversation</label>
        <select id="sandbox-conversation" className={field} value={thread} disabled={locked}
          onChange={(event) => select(event.target.value)}>
          <option value="">Select your conversation</option>
          {threads.map((item) => <option key={item.id} value={item.id}>
            {new Date(item.created_at).toLocaleString()} - {item.id.slice(0, 8)}</option>)}
        </select>
        {threadCursor && <button className={secondary} disabled={locked} onClick={() => void olderThreads()}>Older conversations</button>}
      </div>
    </Panel>
    {thread && <Panel title="Sandbox history">
      {pollError && <Notice error>Polling disconnected: {pollError}. History may be stale; reconnect does not resend messages.</Notice>}
      <button className={secondary} onClick={() => setRevision((value) => value + 1)}>Reconnect / check status</button>
      {turnCursor && <button className={`${secondary} ml-3`} onClick={() => setHistoryPages((value) => value + 1)}>Older messages</button>}
      <div className="my-5 space-y-5" role="log" aria-live="polite" aria-label="Sandbox messages">
        {!turns.length && <p>No messages loaded.</p>}
        {turns.map((turn) => <article key={turn.id} className="rounded-lg border border-ink-200 p-4">
          <p className="whitespace-pre-wrap break-words"><strong>You:</strong> {turn.body}</p>
          <p className="my-2 text-sm text-ink-600">{sandboxStatus[turn.status]}</p>
          {sandboxDelayed(turn, now) && <Notice>Taking longer than the 90-second turn deadline. This is not a delivery confirmation or permission to resend. Check status; worker retries may still be pending.</Notice>}
          {turn.replies.map((reply) => <p key={reply.id} className="mt-3 whitespace-pre-wrap break-words"><strong>Sandbox assistant:</strong> {reply.body}
            <button type="button" className={`${secondary} ml-3`} disabled={conversationVoiceBusy} onClick={() => readAloud(reply.body)}>Read reply aloud locally</button>
          </p>)}
        </article>)}
      </div>
      {pending && <Notice>{pending.source === "voice"
        ? "Voice request retained. Resume the same request or explicitly discard local recovery. Check history for any accepted message."
        : "Uncertain send retained. Retry uses the original text and request key. Polling can confirm acceptance without resending."}</Notice>}
      <VoiceConversation key={`voice-${thread}`} conversation={thread} query={query}
        disabled={!canWrite || busy || pending?.source === "text" || creating || listening || voiceBusy}
        onBusy={setConversationVoiceBusy} onPending={setPending} />
      <RecordedVoice key={thread} conversation={thread} query={query}
        disabled={!canWrite || busy || pending !== null || creating || listening || conversationVoiceBusy}
        onText={setText} onBusy={setVoiceBusy} />
      <label htmlFor="sandbox-text" className="block text-sm">Text question (maximum 4000 characters)</label>
      <textarea id="sandbox-text" className={field} maxLength={4000} value={pending?.body ?? text}
        disabled={!canWrite || locked} onChange={(event) => setText(event.target.value)} />
      <label htmlFor="sandbox-voice-language" className="mt-3 block text-sm">Dictation language</label>
      <select id="sandbox-voice-language" className={field} value={voiceLanguage} disabled={!canWrite || locked}
        onChange={event => setVoiceLanguage(event.target.value)}>
        <option value="en-US">English (US)</option>
        <option value="en-GB">English (UK)</option>
        <option value="en-MY">English (Malaysia)</option>
      </select>
      <div className="mt-3 flex flex-wrap gap-3">
        <button className={action} disabled={!canWrite || busy || creating || listening || voiceBusy || conversationVoiceBusy || pending?.source === "voice" || (!pending && !text.trim())} onClick={() => void send()}>
          {pending?.source === "text" ? "Retry exact request" : "Send to sandbox"}</button>
        <button type="button" className={secondary} disabled={!canWrite || busy || pending !== null || creating || voiceBusy || conversationVoiceBusy}
          onClick={listening ? stopVoice : dictate}>{listening ? "Stop microphone" : "Dictate locally"}</button>
        <button type="button" className={secondary} onClick={() => window.speechSynthesis?.cancel()}>Stop speech</button>
        <button className={secondary} disabled={!canWrite || locked} onClick={() => setConfirmArchive(true)}>Archive conversation</button>
      </div>
      <p className="text-xs text-ink-500">Review dictated text before sending. Local dictation requires a secure browser with local speech support and an installed language pack matching your selection. Audio stays in your browser.</p>
      {confirmArchive && <div className="mt-4">
        <Notice>Hide this conversation from your list? History and queued work are retained. This does not delete messages or cancel an accepted turn.</Notice>
        <button className={secondary} disabled={locked} onClick={() => void archive()}>Confirm archive</button>
        <button className={`${secondary} ml-3`} onClick={() => setConfirmArchive(false)}>Cancel</button>
      </div>}
    </Panel>}
  </div>;
}
