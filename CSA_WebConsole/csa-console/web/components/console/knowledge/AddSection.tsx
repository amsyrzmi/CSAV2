"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import type { Agent, KbJob } from "@/lib/identity-contracts";
import { progressWord, validateSelection, type ProgressWord } from "@/lib/knowledge-view";
import { agentStateOf, lockedSlot, useKbSlot, type Slot } from "./useKbSlot";
import AddKnowledge from "./AddKnowledge";
import LockedNotice from "./LockedNotice";
import SectionError from "./SectionError";
import StatusLabel, { type StatusTone } from "../fs/StatusLabel";
import { TechnicalDetails } from "../kit";
import { Notice } from "../ui";

const TONES: Record<ProgressWord, StatusTone> = {
  Waiting: "info",
  Reading: "info",
  Ready: "success",
  "Partly read": "attention",
  "Could not be read": "attention",
  Removed: "info",
};

type Upload = { jobId: string; names: string[]; message: string };

/**
 * Add knowledge: the upload control and its own message and error. It reads the assistant row
 * (is adding switched on) and the reading jobs; the upload, its limits and its wording are as
 * they always were, including the same idempotency key on a retry.
 */
export default function AddSection({ query, canWrite }: { query: string; canWrite: boolean }) {
  const agents = useKbSlot<Agent[]>("agents", query);
  const jobs = useKbSlot<KbJob[]>("kb/ingest-jobs", query);
  const [error, setError] = useState("");
  const [uploads, setUploads] = useState<Upload[]>([]);
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

  const jobRows = jobs.slot.data;
  // A removed job leaves the list (cancelled jobs are dismissed), so a job seen once and then missing is shown as removed.
  const [seen, setSeen] = useState<ReadonlySet<string>>(new Set());
  const fresh = (jobRows ?? []).filter((row) => !seen.has(row.id));
  if (fresh.length) setSeen(new Set([...seen, ...fresh.map((row) => row.id)]));
  const wordOf = (upload: Upload): ProgressWord => {
    const row = jobRows?.find((entry) => entry.id === upload.jobId);
    if (!row && jobRows && seen.has(upload.jobId)) return progressWord("cancelled");
    return progressWord(row?.status ?? null);
  };
  const waiting = uploads.some((upload) => ["Waiting", "Reading"].includes(wordOf(upload)));
  useEffect(() => {
    if (error) feedback.current?.scrollIntoView({ block: "nearest" });
  }, [error]);
  // The list is read again every 5 seconds only while a file of this visit is waiting or being read.
  useEffect(() => {
    if (!waiting) return;
    const timer = setInterval(() => void loadJobs(), 5000);
    return () => clearInterval(timer);
  }, [waiting, loadJobs]);

  // The picker and a drop both come here, so both are held to the same rules.
  function choose(chosen: File[]) {
    setError(""); setSelection(null);
    if (!chosen.length) return;
    const problem = validateSelection(chosen.map((file) => ({ name: file.name, size: file.size })));
    if (problem) {
      setError(problem);
      if (input.current) input.current.value = "";
      return;
    }
    setSelection({ files: chosen, key: crypto.randomUUID() });
  }
  async function upload() {
    if (!selection) return;
    setBusy(true); setError("");
    const form = new FormData();
    for (const file of selection.files) form.append("files", file);
    try {
      const result = await api<{ job_id: string; duplicate_request: boolean }>(path("kb/uploads"), {
        method: "POST", headers: { "Idempotency-Key": selection.key }, body: form,
      });
      const message = `${result.duplicate_request ? "Existing" : "Queued"} job ${result.job_id}. Queued is not yet searchable.`;
      const entry = { jobId: result.job_id, names: selection.files.map((file) => file.name), message };
      setUploads((current) => [entry, ...current.filter((row) => row.jobId !== result.job_id)]);
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
          selection={selection?.files ?? null} onFiles={choose} onUpload={upload} onRetryAgent={() => void loadAgents()} />
      : <Notice>Your role can read the business knowledge but not add to it.</Notice>}
    <div ref={feedback} className="space-y-3 empty:hidden">
      {error && <Notice error>{error}</Notice>}
      {retry(jobs.slot)}
    </div>
    {uploads.length > 0 && <ul data-testid="kb-progress" className="space-y-3">
      {uploads.map((upload) => {
        const word = wordOf(upload);
        const id = `kb-progress-${upload.jobId}`;
        return <li key={upload.jobId} data-testid={id}
          className="rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
            <p className="min-w-0 font-medium [overflow-wrap:anywhere]">{upload.names.join(", ")}</p>
            <span data-testid={`${id}-word`}><StatusLabel tone={TONES[word]}>{word}</StatusLabel></span>
          </div>
          <TechnicalDetails testId={`${id}-tech`}><p>{upload.message}</p></TechnicalDetails>
        </li>;
      })}
    </ul>}
  </div>;
}
