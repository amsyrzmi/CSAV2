"use client";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { captureUtterance, installedVoice, playLocalReply } from "@/lib/voice-session";
import type { Page, SandboxTurn } from "@/lib/sandbox-chat";
import { Notice, secondary } from "./ui";

type Job = { status: string; transcript: string | null; error_code: string | null };
type Pending = { key: string; blob: Blob; body?: string };
function pause(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    function abort() { clearTimeout(timer); reject(signal.reason); }
    const timer = setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, 1000);
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) abort();
  });
}

export function VoiceConversation({ conversation, query, disabled, onBusy, onDraft }: {
  conversation: string; query: string; disabled: boolean;
  onBusy: (value: boolean) => void; onDraft: (value: string) => void;
}) {
  const [enabled, setEnabled] = useState(false);
  const [consent, setConsent] = useState(false);
  const [language, setLanguage] = useState("en");
  const [state, setState] = useState("Stopped");
  const [error, setError] = useState("");
  const [recoverable, setRecoverable] = useState(false);
  const controller = useRef<AbortController | null>(null);
  const pending = useRef<Pending | null>(null);
  useEffect(() => {
    const request = new AbortController();
    api<{ enabled: boolean }>("/console/v1/chat/voice", { signal: request.signal })
      .then(value => setEnabled(value.enabled)).catch(cause => { if (!request.signal.aborted) setError((cause as Error).message); });
    return () => { request.abort(); controller.current?.abort(); window.speechSynthesis?.cancel(); };
  }, []);
  function stop() {
    controller.current?.abort(); window.speechSynthesis?.cancel();
    setState("Stopped"); onBusy(false); setRecoverable(pending.current !== null);
  }
  const base = `/console/v1/chat/conversations/${conversation}`;
  async function complete(recording: Pending, signal: AbortSignal): Promise<string> {
    if (!recording.body) {
      setState("Transcribing");
      let job: Job;
      try { job = await api<Job>(`${base}/voice/${recording.key}?${query}`, { signal }); }
      catch (cause) {
        if (!(cause instanceof ApiError) || cause.status !== 404) throw cause;
        job = await api<Job>(`${base}/voice/${recording.key}?${query}`, {
          method: "POST", body: recording.blob, signal: AbortSignal.any([signal, AbortSignal.timeout(45_000)]),
        });
      }
      const deadline = Date.now() + 95_000;
      while (job.status === "processing" && Date.now() < deadline) {
        await pause(signal);
        job = await api<Job>(`${base}/voice/${recording.key}?${query}`, { signal });
      }
      if (job.status !== "completed" || !job.transcript) throw new Error(job.error_code ?? "Transcription did not complete. Check status or use text input.");
      recording.body = job.transcript; onDraft(recording.body);
    }
    setState("Thinking");
    // Exact key and body survive uncertainty within this mounted conversation.
    await api(`${base}/turns?${query}`, { method: "POST", body: JSON.stringify({ request_key: recording.key, body: recording.body }), signal });
    const deadline = Date.now() + 100_000;
    while (Date.now() < deadline) {
      const page = await api<Page<SandboxTurn>>(`${base}/turns?${query}`, { signal });
      const turn = page.items.find(value => value.request_key === recording.key);
      if (turn?.status === "available" && turn.replies.length) return turn.replies.map(value => value.body).join("\n");
      if (turn && ["held", "failed", "no_reply"].includes(turn.status)) throw new Error(`Conversation ${turn.status.replaceAll("_", " ")}. Check the recorded status in history.`);
      await pause(signal);
    }
    throw new Error("Reply is taking longer than expected. Check history or resume the same request.");
  }
  async function start() {
    if (controller.current && !controller.current.signal.aborted) return;
    const current = new AbortController(); controller.current = current;
    const signal = AbortSignal.any([current.signal, AbortSignal.timeout(15 * 60_000)]);
    setError(""); setRecoverable(false); onBusy(true);
    try {
      setState("Checking installed voice");
      await installedVoice(language, signal);
      while (!signal.aborted) {
        if (!pending.current) {
          setState("Listening");
          const blob = await captureUtterance(signal);
          signal.throwIfAborted(); pending.current = { key: crypto.randomUUID(), blob };
        }
        const reply = await complete(pending.current, signal);
        signal.throwIfAborted(); pending.current = null; onDraft("");
        setState("Speaking"); await playLocalReply(reply, language, signal);
      }
    } catch (cause) {
      if (!current.signal.aborted) setError(`${(cause as Error).message ?? "Voice chat stopped."} Check history before starting another request.`);
    } finally {
      if (controller.current === current) {
        current.abort(); setState("Stopped"); onBusy(false); setRecoverable(pending.current !== null);
      }
    }
  }
  const running = state !== "Stopped";
  return <section className="my-4 space-y-2 rounded-lg border border-ink-200 p-3" aria-label="Hands-free voice chat">
    <p role="status">Voice chat: {state}</p>
    {error && <Notice error>{error}</Notice>}
    <label className="block text-sm">Playback language <select value={language} disabled={running} onChange={event => setLanguage(event.target.value)}>
      <option value="en">English</option><option value="ms">Malay</option></select></label>
    <label className="block text-sm"><input type="checkbox" checked={consent} disabled={running} onChange={event => setConsent(event.target.checked)} /> Automatically send my transcribed speech to this conversation and read replies aloud.</label>
    <p className="text-xs">Speech pauses submit your question. The microphone is off while the assistant thinks or speaks. Stop does not cancel an already accepted message. Check history after a reload.</p>
    {enabled ? <>
      <button type="button" className={secondary} disabled={running || disabled || !consent} onClick={() => void start()}>{recoverable ? "Resume same request" : "Start voice chat"}</button>
      <button type="button" className={secondary} disabled={!running} onClick={stop}>Stop voice chat / mute</button>
      {recoverable && !running && <button type="button" className={secondary} onClick={() => { pending.current = null; setRecoverable(false); setError("Local recovery discarded. Previously accepted messages remain in history."); }}>Discard local recovery</button>}
    </> : <p>Transcription is not configured.</p>}
  </section>;
}
