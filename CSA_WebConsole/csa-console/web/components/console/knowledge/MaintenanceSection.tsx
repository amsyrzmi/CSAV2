"use client";

import { useState } from "react";
import { post } from "@/lib/api";
import type { Agent, KbFile } from "@/lib/identity-contracts";
import { Notice } from "../ui";
import { Maintenance, Settings } from "./Disclosures";
import LockedNotice from "./LockedNotice";
import SectionError from "./SectionError";
import { agentStateOf, lockedSlot, useKbSlot } from "./useKbSlot";

/**
 * Maintenance and settings: both open on arrival. Reconcile removes orphaned stored files and
 * then reads the files list again; the settings say whether the knowledge assistant is on.
 */
export default function MaintenanceSection({
  query,
  canWrite,
  servicesHref,
}: {
  query: string;
  canWrite: boolean;
  servicesHref: string;
}) {
  const agents = useKbSlot<Agent[]>("agents", query);
  const files = useKbSlot<KbFile[]>("kb/files", query);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const agent = agents.slot.data?.find((row) => row.name === "kb_agent");
  const agentState = agentStateOf(agents.slot, agent);
  const writable = canWrite && agent?.capability?.active === true;
  const locked = lockedSlot(agents.slot, files.slot);
  const loadFiles = files.load;
  const loadAgents = agents.load;

  async function reconcile() {
    setBusy(true); setError("");
    try {
      const result = await post<{ removed_files: number }>(`/console/v1/kb/storage/reconcile?${query}`);
      setMessage(`Storage reconciled: ${result.removed_files} orphaned/deleted/partial files removed. Active sources are retained.`);
      void loadFiles();
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }

  if (locked) return <LockedNotice slot={locked} onRefresh={() => { void loadFiles(); void loadAgents(); }} />;
  return <div className="space-y-6">
    <div className="space-y-3 empty:hidden">
      {error && <Notice error>{error}</Notice>}
      {message && <Notice>{message}</Notice>}
      {files.slot.error && <SectionError name="files" what="the original files" message={files.slot.error}
        stale={files.slot.data !== null} onRetry={() => void loadFiles()} />}
    </div>
    <Maintenance agent={agent ?? null} agentState={agentState} canWrite={canWrite} writable={writable} busy={busy}
      onReconcile={reconcile} onRetryAgent={() => void loadAgents()} />
    <Settings agent={agent ?? null} agentState={agentState} servicesHref={servicesHref} onRetryAgent={() => void loadAgents()} />
  </div>;
}
