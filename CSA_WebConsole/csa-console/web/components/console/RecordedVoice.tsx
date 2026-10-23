"use client";
import { useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api";
import { Notice, secondary } from "./ui";

type Job = { status: "processing" | "completed" | "failed" | "timeout" | "expired"; transcript: string | null; error_code: string | null };
type Recording = { key: string; blob: Blob };

export function RecordedVoice({ conversation, query, disabled, onText, onBusy }: {
  conversation: string; query: string; disabled: boolean;
  onText: (text: string) => void; onBusy: (busy: boolean) => void;
}) {
  const [enabled, setEnabled] = useState(false);
  const [consent, setConsent] = useState(false);
  const [state, setState] = useState("idle");
  const [error, setError] = useState("");
  const recorder = useRef<MediaRecorder | null>(null);
  const retained = useRef<Recording | null>(null);
  const active = useRef(false);
  const generation = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const request = useRef<AbortController | null>(null);
  const limit = 8 * 1024 * 1024;
  useEffect(() => {
    active.current = true;
    const pendingGeneration = generation;
    const controller = new AbortController();
    api<{ enabled: boolean }>("/console/v1/chat/voice", { signal: controller.signal })
      .then(value => { if (active.current) setEnabled(value.enabled); })
      .catch(cause => { if (!controller.signal.aborted) setError((cause as Error).message); });
    return () => {
      active.current = false; pendingGeneration.current++;
      controller.abort(); request.current?.abort();
      if (timer.current) clearTimeout(timer.current);
      if (recorder.current) {
        recorder.current.onstop = null;
        recorder.current.ondataavailable = null; recorder.current.onerror = null;
        if (recorder.current.state !== "inactive") recorder.current.stop();
        recorder.current.stream.getTracks().forEach(track => track.stop());
      }
      retained.current = null;
    };
  }, []);
  function settle(job: Job) {
    if (!active.current) return;
    if (job.status === "completed" && job.transcript) {
      onText(job.transcript); retained.current = null; setState("idle"); onBusy(false);
    } else if (job.status === "processing") {
      setState("uncertain"); setError("Transcription is processing. Check status before recording again.");
    } else {
      setState("uncertain"); setError(job.error_code ?? "Transcription failed. Discard this recording to try again.");
    }
  }
  async function upload(value: Recording) {
    request.current = new AbortController();
    const signal = AbortSignal.any([request.current.signal, AbortSignal.timeout(45_000)]);
    try {
      settle(await api<Job>(`/console/v1/chat/conversations/${conversation}/voice/${value.key}?${query}`, {
        method: "POST", body: value.blob, signal,
      }));
    } catch (cause) {
      if (active.current) { setState("uncertain"); setError(`${(cause as Error).message}. Check status or retry the same recording.`); }
    }
  }
  async function check() {
    const value = retained.current;
    if (!value) return;
    setError(""); setState("uploading");
    try {
      settle(await api<Job>(`/console/v1/chat/conversations/${conversation}/voice/${value.key}?${query}`, { signal: AbortSignal.timeout(15_000) }));
    } catch (cause) {
      if (!active.current) return;
      if (cause instanceof ApiError && cause.status === 404) await upload(value);
      else { setState("uncertain"); setError((cause as Error).message); }
    }
  }
  function cancel() {
    generation.current++;
    if (timer.current) clearTimeout(timer.current);
    if (recorder.current) {
      recorder.current.onstop = null;
      recorder.current.ondataavailable = null; recorder.current.onerror = null;
      if (recorder.current.state !== "inactive") recorder.current.stop();
      recorder.current.stream.getTracks().forEach(track => track.stop());
    }
    retained.current = null; setState("idle"); setError(""); onBusy(false);
  }
  async function start() {
    setError(""); setState("permission"); onBusy(true);
    const version = ++generation.current;
    let stream: MediaStream | null = null;
    try {
      if (!window.isSecureContext || !navigator.mediaDevices || typeof MediaRecorder === "undefined") throw new Error("Recording requires a secure browser with microphone support.");
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      if (!active.current || version !== generation.current) { stream.getTracks().forEach(track => track.stop()); return; }
      const mime = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"].find(type => MediaRecorder.isTypeSupported(type));
      if (!mime) throw new Error("This browser has no supported recording format.");
      const media = new MediaRecorder(stream, { mimeType: mime });
      recorder.current = media;
      const chunks: Blob[] = []; let size = 0;
      media.ondataavailable = event => {
        size += event.data.size;
        if (size > limit) { cancel(); setError("Recording exceeds 8 MB. Record a shorter message."); }
        else if (event.data.size) chunks.push(event.data);
      };
      media.onerror = () => { cancel(); setError("Recording failed. Check your microphone and try again."); };
      media.onstop = () => {
        if (timer.current) clearTimeout(timer.current);
        media.stream.getTracks().forEach(track => track.stop());
        if (!active.current || version !== generation.current) return;
        const value = { key: crypto.randomUUID(), blob: new Blob(chunks, { type: mime }) };
        retained.current = value; setState("uploading"); void upload(value);
      };
      media.start(1000); setState("recording");
      timer.current = setTimeout(() => { if (media.state === "recording") media.stop(); }, 60_000);
    } catch (cause) {
      stream?.getTracks().forEach(track => track.stop());
      if (active.current && version === generation.current) { cancel(); setError((cause as Error).message); }
    }
  }
  return <div className="my-4 space-y-2">
    {error && <Notice error>{error}</Notice>}
    {!enabled ? <p className="text-sm">Recorded voice is unavailable until transcription is configured.</p> : <>
      <label className="block text-sm"><input type="checkbox" checked={consent} disabled={state !== "idle"}
        onChange={event => setConsent(event.target.checked)} /> Send this recording to the configured transcription service. Review the transcript before sending it to chat.</label>
      {state === "idle" && <button type="button" className={secondary} disabled={disabled || !consent} onClick={() => void start()}>Record voice message</button>}
      {state === "permission" && <button type="button" className={secondary} onClick={cancel}>Cancel microphone request</button>}
      {state === "recording" && <><button type="button" className={secondary} onClick={() => recorder.current?.stop()}>Stop and transcribe</button>
        <button type="button" className={secondary} onClick={cancel}>Cancel recording</button></>}
      {state === "uploading" && <p role="status">Transcribing recording…</p>}
      {state === "uncertain" && <><button type="button" className={secondary} onClick={() => void check()}>Check status / retry same recording</button>
        <button type="button" className={secondary} onClick={cancel}>Discard recording</button></>}
      <p className="text-xs">Maximum 60 seconds / 8 MB. Server audio is held only in memory during transcription. Saved transcripts expire from API access after one hour.</p>
    </>}
  </div>;
}
