"use client";

import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { api } from "@/lib/api";
import type { Agent, KbJob } from "@/lib/identity-contracts";
import { agentStateOf, lockedSlot, useKbSlot, type Slot } from "./useKbSlot";
import AddKnowledge from "./AddKnowledge";
import LockedNotice from "./LockedNotice";
import SectionError from "./SectionError";
import { Notice } from "../ui";

/**
 * Add knowledge: the upload control and its own message and error. It reads the assistant row
 * (is adding switched on) and the reading jobs; the upload, its limits and its wording are as
 * they always were, including the same idempotency key on a retry.
 */
export default function AddSection({ query, canWrite }: { query: string; canWrite: boolean }) {
  const agents = useKbSlot<Agent[]>("agents", query);
  const jobs = useKbSlot<KbJob[]>("kb/ingest-jobs", query);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [selection, setSelection] = useState<{ files: File[]; key: string } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const feedback = useRef<HTMLDivElement>(null);
  const path = (resource: string) => `/console/v1/${resource}?${query}`;

  const agent = agents.slot.data?.find((row) => row.name === "kb_agent");
  const agentState = agentStateOf(agents.slot, agent);
  const writable = canWrite && agent?.capability?.active === true;
  const locked = lockedSlot(agents.slot, jobs.slot);
  const loadJobs = jobs.load;
  const loadAgents = agents.load;

  useEffect(() => {
    if (error || message) feedback.current?.scrollIntoView({ block: "nearest" });
  }, [error, message]);

  function choose(event: ChangeEvent<HTMLInputElement>) {
    const chosen = Array.from(event.target.files ?? []);
    setError(""); setSelection(null);
    if (!chosen.length) return;
    if (chosen.length > 3 || chosen.some((file) => !/\.(pdf|docx|csv)$/i.test(file.name) || file.size === 0 || file.size > 10 * 1024 * 1024)) {
      setError("Select one to three non-empty PDF, DOCX or CSV files, at most 10 MB each.");
      event.target.value = ""; return;
    }
    setSelection({ files: chosen, key: crypto.randomUUID() });
  }
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
      void loadJobs();
    } catch (cause) {
      setError(`${(cause as Error).message}. Retry keeps the same idempotency key; do not reselect files for an uncertain result.`);
    } finally { setBusy(false); }
  }

  if (locked) return <LockedNotice slot={locked} onRefresh={() => { void loadJobs(); void loadAgents(); }} />;
  const retry = (slot: Slot<unknown>) => slot.error && (
    <SectionError name="jobs" what="the files being read" message={slot.error} stale={slot.data !== null} onRetry={() => void loadJobs()} />
  );

  return <div className="space-y-6">
    {canWrite
      ? <AddKnowledge inputRef={input} agent={agentState} writable={writable} busy={busy}
          selection={selection?.files ?? null} onChange={choose} onUpload={upload} onRetryAgent={() => void loadAgents()} />
      : <Notice>Your role can read the business knowledge but not add to it.</Notice>}
    <div ref={feedback} className="space-y-3 empty:hidden">
      {error && <Notice error>{error}</Notice>}
      {message && <Notice>{message}</Notice>}
      {retry(jobs.slot)}
    </div>
  </div>;
}
