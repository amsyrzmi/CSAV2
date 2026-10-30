"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Archive, History, RefreshCw } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import type { Client, Me } from "@/lib/identity-contracts";
import type { Page, PendingSandboxTurn, SandboxThread, SandboxTurn, ScrollSnapshot } from "@/lib/sandbox-chat";
import { chipsFor, disclaimerMode, scrollPlan } from "@/lib/sandbox-chat";
import { VoiceConversation } from "./VoiceConversation";
import { RecordedVoice } from "./RecordedVoice";
import { localDictation, speakLocal } from "@/lib/hr-voice";
import SlideOver from "./fs/SlideOver";
import { ChatComposer } from "./chat/ChatComposer";
import { CompactLimits, SandboxLimits } from "./chat/ChatDisclaimer";
import { AssistantTile, ChatEmptyState } from "./chat/ChatEmptyState";
import { ChatHistory, threadLabel } from "./chat/ChatHistory";
import { ChatMessages } from "./chat/ChatMessages";
import { field, Heading, Notice, secondary } from "./ui";

/**
 * The private sandbox assistant in a ChatGPT-style layout. By default it brings its own page
 * chrome (heading, and the tenant select for platform administrators). `chromeless` renders the
 * chat alone, filling its container's height, for a caller that owns the page header and passes
 * the tenant (Overview).
 */
export function SandboxWorkspace({ me, chromeless = false, tenant: chosenTenant }: {
  me: Me;
  chromeless?: boolean;
  /** Chromeless only: the tenant the caller selected (platform administrators). */
  tenant?: string;
}) {
  const search = useSearchParams();
  const platform = me.role === "platform_admin";
  const [selected, setSelected] = useState(search.get("client_id") ?? "");
  const [clients, setClients] = useState<Client[]>([]);
  const [error, setError] = useState("");
  const loadClients = platform && !chromeless;
  useEffect(() => {
    if (!loadClients) return;
    const controller = new AbortController();
    api<Client[]>("/console/v1/admin/clients", { signal: controller.signal })
      .then(setClients).catch((cause: Error) => {
        if (!controller.signal.aborted) setError(cause.message);
      });
    return () => controller.abort();
  }, [loadClients]);
  const tenant = chromeless
    ? (chosenTenant ?? (platform ? "" : me.active_client_id))
    : (platform ? selected : me.active_client_id);
  const sandbox = tenant ? <Sandbox key={tenant} query={platform ? `client_id=${encodeURIComponent(tenant)}` : ""}
    canWrite={me.role !== null && me.role !== "read_only"} role={me.role}
    dockClearance={me.workspace_assistant === true} /> : null;
  if (chromeless) return sandbox ?? <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
    <AssistantTile />
    <p className="text-[color:var(--fs-text-secondary)]">{platform ? "Choose a tenant to start chatting" : "Select a tenant to continue."}</p>
  </div>;
  return <>
    <Heading title="Sandbox assistant" description="Private to your account and selected tenant. Knowledge and product-information questions only." />
    {error && <Notice error>{error}</Notice>}
    {platform && <div className="my-5">
      <label htmlFor="sandbox-tenant" className="block text-sm">Tenant</label>
      <select id="sandbox-tenant" className={field} value={selected} onChange={(event) => setSelected(event.target.value)}>
        <option value="">Select a tenant</option>
        {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
      </select>
    </div>}
    {sandbox ? <div className="card h-[calc(100dvh-14rem)] min-h-[28rem] overflow-hidden">{sandbox}</div>
      : <Notice>Select a tenant to continue.</Notice>}
  </>;
}

const headerIcon =
  "inline-grid size-10 shrink-0 place-items-center rounded-[var(--fs-radius-round)] text-[color:var(--fs-text-secondary)] transition-colors duration-150 hover:bg-[var(--fs-surface-blue)] hover:text-[color:var(--fs-text)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] disabled:cursor-not-allowed disabled:text-[color:var(--fs-disabled-text)] disabled:hover:bg-transparent pointer-coarse:size-11";

function Sandbox({ query, canWrite, role, dockClearance }: {
  query: string; canWrite: boolean; role: Me["role"];
  /** The floating assistant dock is on: keep the composer clear of its button at every width. */
  dockClearance: boolean;
}) {
  const [threads, setThreads] = useState<SandboxThread[]>([]);
  const [threadsLoaded, setThreadsLoaded] = useState(false);
  const [thread, setThread] = useState("");
  const [threadCursor, setThreadCursor] = useState<number | null>(null);
  const [turns, setTurns] = useState<SandboxTurn[]>([]);
  const [loadedThread, setLoadedThread] = useState("");
  const [turnCursor, setTurnCursor] = useState<number | null>(null);
  const [historyPages, setHistoryPages] = useState(1);
  const [text, setText] = useState("");
  const [pending, setPending] = useState<PendingSandboxTurn | null>(null);
  const creationKey = useRef<string | null>(null);
  const observed = useRef(new Set<string>());
  const [creating, setCreating] = useState(false);
  // A creation failed: its request key is retained and the next try (New chat or Send) reuses it.
  const [createFailed, setCreateFailed] = useState(false);
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
  const [voiceOpen, setVoiceOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const stopDictation = useRef<(() => void) | null>(null);
  const alive = useRef(true);
  const composer = useRef<HTMLTextAreaElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const historyButton = useRef<HTMLButtonElement>(null);
  const composerArea = useRef<HTMLDivElement>(null);
  // Focus was in the composer when a send started: give it back once the composer is usable.
  const refocus = useRef(false);
  const nearBottom = useRef(true);
  const ownKeys = useRef(new Set<string>());
  const scrolled = useRef<ScrollSnapshot & { height: number }>({ thread: "", firstId: null, lastKey: null, height: 0 });
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
      .then((page) => { setThreads(page.items); setThreadCursor(page.next_cursor); setThreadsLoaded(true); })
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
        setLoadedThread(thread);
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

  // Follow the end only for a conversation's first load, the reader's own question, or a reader
  // already at the end; keep the view still when older turns arrive above (lib scrollPlan).
  useLayoutEffect(() => {
    const element = scroller.current;
    if (!element) return;
    const next: ScrollSnapshot = { thread, firstId: turns[0]?.id ?? null, lastKey: turns.at(-1)?.request_key ?? null };
    const plan = scrollPlan(scrolled.current, next, nearBottom.current, ownKeys.current);
    if (plan === "bottom") element.scrollTop = element.scrollHeight;
    else if (plan === "keep-offset") element.scrollTop += element.scrollHeight - scrolled.current.height;
    scrolled.current = { ...next, height: element.scrollHeight };
  }, [thread, turns]);
  function onScroll() {
    const element = scroller.current;
    if (!element) return;
    nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
    scrolled.current.height = element.scrollHeight;
  }

  /**
   * Creates a conversation, selects it and returns its id; null when creation failed. A failure
   * leaves the composer usable and the draft in place; the retained key makes the next try (New
   * chat or Send) the same request. Opened from no conversation, the draft belongs to the new one.
   */
  async function create(): Promise<string | null> {
    creationKey.current ??= crypto.randomUUID();
    const keepDraft = !thread;
    setCreating(true); setBusy(true); setError("");
    try {
      const result = await mutate<SandboxThread>("", { request_key: creationKey.current });
      if (!alive.current) return null;
      creationKey.current = null; setCreateFailed(false);
      select(result.id, keepDraft); setThreadRevision((value) => value + 1);
      return result.id;
    } catch (cause) {
      if (alive.current) {
        setCreateFailed(true);
        setError(`${(cause as Error).message}. Retry creation keeps the same request key.`);
      }
      return null;
    } finally { if (alive.current) { setCreating(false); setBusy(false); } }
  }
  function select(id: string, keepDraft = false) {
    stopVoice();
    window.speechSynthesis?.cancel();
    observed.current.clear();
    setThread(id); setTurns([]); setLoadedThread(""); setHistoryPages(1); setTurnCursor(null);
    setPollError(""); setConfirmArchive(false);
    if (!keepDraft) setText("");
  }
  async function send() {
    if (pending?.source === "voice") return;
    const draft = pending ?? { source: "text" as const, request_key: crypto.randomUUID(), body: text };
    const active = document.activeElement;
    refocus.current = active !== null && composerArea.current?.contains(active) === true;
    // With no conversation open, the first question opens one (as "New chat" does), then sends.
    const target = thread || await create();
    if (!target || !alive.current) return;
    setPending(draft); setText(""); setBusy(true); setError("");
    ownKeys.current.add(draft.request_key);
    try {
      await mutate(`/${target}/turns`, { request_key: draft.request_key, body: draft.body });
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
  function fillFromChip(chip: string) {
    setText(chip);
    composer.current?.focus();
  }
  const locked = busy || pending !== null || creating || listening || voiceBusy || conversationVoiceBusy;
  const textDisabled = !canWrite || locked;
  useEffect(() => {
    if (textDisabled || !refocus.current) return;
    refocus.current = false;
    // Only if focus did not move elsewhere meanwhile (disabling the text box drops it to body).
    const active = document.activeElement;
    if (!active || active === document.body || composerArea.current?.contains(active)) composer.current?.focus();
  }, [textDisabled]);
  const voiceRunning = voiceBusy || conversationVoiceBusy;
  const current = threads.find((item) => item.id === thread);
  const loaded = !thread || loadedThread === thread;
  const mode = disclaimerMode(turns.length);
  const history = (inPanel: boolean) => <ChatHistory threads={threads} loaded={threadsLoaded} selected={thread}
    showLabel={!inPanel} hasOlder={threadCursor !== null} onOlder={() => void olderThreads()}
    newLabel={createFailed ? "Retry conversation creation" : "New chat"}
    newDisabled={!canWrite || busy || pending !== null || voiceBusy || conversationVoiceBusy || listening}
    selectDisabled={locked}
    onNew={() => { setHistoryOpen(false); void create(); }}
    onSelect={(id) => { setHistoryOpen(false); select(id); }} />;

  return <div className="grid h-full min-h-0 grid-rows-[minmax(0,1fr)] bg-[var(--fs-surface)] md:grid-cols-[16rem_minmax(0,1fr)]">
    <aside className="hidden min-h-0 flex-col border-r border-[color:var(--fs-border)] p-3 md:flex">{history(false)}</aside>
    <SlideOver open={historyOpen} onClose={() => setHistoryOpen(false)} title="Your private conversations"
      returnFocusTo={historyButton}>{history(true)}</SlideOver>
    <section aria-label="Sandbox chat" className="flex min-h-0 min-w-0 flex-col">
      <header className="flex min-h-14 shrink-0 items-center gap-2 border-b border-[color:var(--fs-border)] px-3 sm:px-4">
        <button ref={historyButton} type="button" className={`${secondary} shrink-0 px-3 md:hidden`} onClick={() => setHistoryOpen(true)}>
          <History size={16} aria-hidden />History</button>
        <p data-testid="sandbox-chat-title" className="min-w-0 flex-1 truncate font-medium text-[color:var(--fs-text)]">
          {current ? threadLabel(current) : thread ? "Conversation" : "New chat"}</p>
        {thread && <>
          <button type="button" className={headerIcon} onClick={() => setRevision((value) => value + 1)}
            aria-label="Reconnect / check status" title="Reconnect / check status"><RefreshCw size={16} aria-hidden /></button>
          <button type="button" className={headerIcon} disabled={!canWrite || locked} onClick={() => setConfirmArchive(true)}
            aria-label="Archive conversation" title="Archive conversation"><Archive size={16} aria-hidden /></button>
        </>}
      </header>
      {confirmArchive && <div className="shrink-0 space-y-3 border-b border-[color:var(--fs-border)] px-3 py-3 sm:px-4">
        <Notice>Hide this conversation from your list? History and queued work are retained. This does not delete messages or cancel an accepted turn.</Notice>
        <div className="flex flex-wrap gap-3">
          <button className={secondary} disabled={locked} onClick={() => void archive()}>Confirm archive</button>
          <button className={secondary} onClick={() => setConfirmArchive(false)}>Cancel</button>
        </div>
      </div>}
      {/* `relative`: the sr-only "You:" prefixes are absolutely positioned; without it they
          escape the scroller's clip and stretch the page (Overview must not scroll). */}
      <div ref={scroller} data-testid="sandbox-scroll" onScroll={onScroll} className="relative min-h-0 flex-1 overflow-y-auto overscroll-contain">
        <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col gap-5 px-4 py-6 sm:px-6">
          {pollError && <Notice error>Polling disconnected: {pollError}. History may be stale; reconnect does not resend messages.</Notice>}
          {turnCursor && <div className="text-center">
            <button className={secondary} onClick={() => {
              if (scroller.current) scrolled.current.height = scroller.current.scrollHeight;
              setHistoryPages((value) => value + 1);
            }}>Older messages</button></div>}
          {!loaded ? (!pollError && <p role="status" className="text-center text-sm text-[color:var(--fs-text-secondary)]">Loading messages</p>)
            : turns.length ? <ChatMessages turns={turns} now={now} onReadAloud={readAloud} readDisabled={conversationVoiceBusy} />
              : <ChatEmptyState chips={chipsFor(role)} disabled={!canWrite || locked} onChip={fillFromChip} />}
        </div>
      </div>
      <div className={`shrink-0 px-3 pt-2 sm:px-6 ${dockClearance ? "pb-[4.5rem]" : "pb-3"}`}>
        <div ref={composerArea} className="mx-auto w-full max-w-3xl space-y-2">
          {error && <Notice error>{error}</Notice>}
          {!canWrite && <Notice>Read-only membership: sending, creating and archiving conversations are disabled.</Notice>}
          {pending && <Notice>{pending.source === "voice"
            ? "Voice request retained. Resume the same request or explicitly discard local recovery. Check history for any accepted message."
            : "Uncertain send retained. Retry uses the original text and request key. Polling can confirm acceptance without resending."}</Notice>}
          <ChatComposer value={pending?.body ?? text} onChange={setText} textareaRef={composer}
            disabled={textDisabled} onSend={() => void send()} retry={pending?.source === "text"}
            sendDisabled={!canWrite || busy || creating || listening || voiceBusy || conversationVoiceBusy || pending?.source === "voice" || (!pending && !text.trim())}
            listening={listening} onDictate={listening ? stopVoice : dictate}
            dictateDisabled={!canWrite || busy || pending !== null || creating || voiceBusy || conversationVoiceBusy}
            onStopSpeech={() => window.speechSynthesis?.cancel()}
            voiceOpen={voiceOpen || voiceRunning} voiceToggleDisabled={voiceRunning} onVoiceToggle={() => setVoiceOpen((value) => !value)}
            voice={<div className="space-y-3">
              <div>
                <label htmlFor="sandbox-voice-language" className="block text-sm">Dictation language</label>
                <select id="sandbox-voice-language" className={field} value={voiceLanguage} disabled={!canWrite || locked}
                  onChange={event => setVoiceLanguage(event.target.value)}>
                  <option value="en-US">English (US)</option>
                  <option value="en-GB">English (UK)</option>
                  <option value="en-MY">English (Malaysia)</option>
                </select>
              </div>
              <p className="text-xs text-[color:var(--fs-text-secondary)]">Review dictated text before sending. Local dictation requires a secure browser with local speech support and an installed language pack matching your selection. Audio stays in your browser.</p>
              {thread ? <>
                <VoiceConversation key={`voice-${thread}`} conversation={thread} query={query}
                  disabled={!canWrite || busy || pending?.source === "text" || creating || listening || voiceBusy}
                  onBusy={setConversationVoiceBusy} onPending={setPending} />
                <RecordedVoice key={thread} conversation={thread} query={query}
                  disabled={!canWrite || busy || pending !== null || creating || listening || conversationVoiceBusy}
                  onText={setText} onBusy={setVoiceBusy} />
              </> : <p className="text-xs text-[color:var(--fs-text-secondary)]">Hands-free voice chat and recorded voice messages open with a conversation: choose one from history or start a new chat.</p>}
            </div>} />
          {/* Whenever the composer shows, so do the limits: in full until the conversation is known
              to have a message (the empty state holds its own full copy), then one line. */}
          {!loaded ? <SandboxLimits /> : mode === "compact" ? <CompactLimits /> : null}
        </div>
      </div>
    </section>
  </div>;
}
