"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { hrClient, type AgentResult } from "@/lib/hr-api";
import { localDictation, speakLocal } from "@/lib/hr-voice";
import { action, field, secondary, Notice, Panel } from "./ui";

function status(result: AgentResult): string {
  if ("operation" in result) return `${result.status.replaceAll("_", " ")} · reference ${result.resource_id}`;
  if ("clarification" in result) return ({ request_details: "Please provide the request details, dates or reference needed to continue.",
    manual_decision_required: "This action needs a human decision. Open the request in Training or HR requests.",
    unsupported_request: "Use the relevant HR form for this request." })[result.clarification] ?? "More details are required.";
  if ("kind" in result && result.kind === "persisted_training") return `Training ${result.request.content.title}: ${result.request.status}.`;
  if ("kind" in result && result.kind === "confirmation_required") return "A human confirmation is required in the training workflow.";
  if ("values" in result) return Object.values(result.values).filter(value => typeof value === "string").join(" · ") || "Authorized directory fields loaded.";
  if ("request_id" in result) return `Training ${result.content.title}: ${result.status}.`;
  if ("domain" in result) return `${result.domain} request: ${result.status}. This is a local HR record.`;
  return "Request processed.";
}
export function HrAssistant({ tenant, readonly }: { tenant: string; readonly: boolean }) {
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const [message, setMessage] = useState("");
  const [source, setSource] = useState<"text" | "voice">("text");
  const [result, setResult] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const stop = useRef<(() => void) | null>(null);
  const inFlight = useRef(false);
  useEffect(() => () => { stop.current?.(); window.speechSynthesis?.cancel(); }, []);
  function dictate() {
    setError("");
    try {
      stop.current?.();
      stop.current = localDictation(value => { setMessage(value); setSource("voice"); },
        () => setListening(false), problem => { setError(problem); setListening(false); });
      setListening(true);
    } catch (cause) { setError((cause as Error).message); }
  }
  async function send(event: React.FormEvent) {
    event.preventDefault(); if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    try { setResult(status(await client.chat(message, source))); }
    catch (cause) { setError(`${(cause as Error).message}. Check saved requests before retrying; an identical retry keeps its key.`); }
    finally { inFlight.current = false; setBusy(false); }
  }
  return <Panel title="Staff HR assistant"><div className="space-y-4">
    <p className="text-sm text-ink-600">Submitted messages use the configured model provider after the client enables this assistant. Reads and request preparation use your current HR permissions. Decisions, private CV access and payroll actions use the manual forms.</p>
    <form onSubmit={send} className="space-y-3"><label className="block text-sm">Your request<textarea className={`${field} mt-1 h-28 py-3`} value={message}
      onChange={event => { setMessage(event.target.value.replace(/[\r\n]+/g, " ")); setSource("text"); }} maxLength={2000} required disabled={busy || readonly} /></label>
      <div className="flex flex-wrap gap-2"><button className={action} disabled={busy || readonly || listening}>Send request</button>
        <button type="button" className={secondary} disabled={busy || readonly} onClick={listening ? () => { stop.current?.(); setListening(false); } : dictate}>{listening ? "Stop microphone" : "Dictate locally"}</button></div>
      <p className="text-xs text-ink-500">Review dictated text before sending. Local dictation needs browser support and an installed language pack.</p>
    </form>
    {error && <Notice error>{error}</Notice>}
    {result && <><Notice>{result}</Notice><button className={secondary} onClick={() => { try { speakLocal(result); } catch (cause) { setError((cause as Error).message); } }}>Read status aloud locally</button></>}
  </div></Panel>;
}
