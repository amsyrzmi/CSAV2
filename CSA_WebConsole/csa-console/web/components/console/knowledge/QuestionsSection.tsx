"use client";

import { useState } from "react";
import { post } from "@/lib/api";
import type { Agent, KbGap } from "@/lib/identity-contracts";
import { addedText } from "@/lib/knowledge-view";
import { Notice, secondary } from "../ui";
import LockedNotice from "./LockedNotice";
import SectionError from "./SectionError";
import { lockedSlot, useKbSlot } from "./useKbSlot";

/** Unanswered questions: what customers asked that the assistant could not answer, with Mark resolved. */
export default function QuestionsSection({ query, canWrite }: { query: string; canWrite: boolean }) {
  const agents = useKbSlot<Agent[]>("agents", query);
  const gaps = useKbSlot<KbGap[]>("kb/gaps", query);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const path = (resource: string) => `/console/v1/${resource}?${query}`;
  const agent = agents.slot.data?.find((row) => row.name === "kb_agent");
  const writable = canWrite && agent?.capability?.active === true;
  const locked = lockedSlot(agents.slot, gaps.slot);
  const loadGaps = gaps.load;
  const loadAgents = agents.load;

  async function resolve(id: string) {
    setBusy(true); setError("");
    try {
      await post(path(`kb/gaps/${id}/resolve`));
      void loadGaps();
    } catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  }

  if (locked) return <LockedNotice slot={locked} onRefresh={() => { void loadGaps(); void loadAgents(); }} />;
  return <section data-testid="kb-gaps" className="space-y-3">
    <p className="text-sm text-[color:var(--fs-text-secondary)]">
      Customers asked these and your assistant had no answer. Add a file that covers them, then mark each one resolved.
    </p>
    {error && <Notice error>{error}</Notice>}
    {gaps.slot.error && <SectionError name="gaps" what="the unanswered questions" message={gaps.slot.error}
      stale={gaps.slot.data !== null} onRetry={() => void loadGaps()} />}
    {gaps.slot.data === null && !gaps.slot.error && <p className="text-sm">Loading...</p>}
    {gaps.slot.data && !gaps.slot.data.length && <p className="text-sm">No recorded unresolved gaps.</p>}
    {gaps.slot.data && gaps.slot.data.length > 0 && <ul className="divide-y divide-[color:var(--fs-border)] rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)]">
      {gaps.slot.data.map((gap) => <li key={gap.id} data-testid={`kb-gap-${gap.id}`} className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 p-3 text-sm">
        <div className="flex min-w-0 flex-1 basis-56 items-start gap-3">
          <svg aria-hidden="true" viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" className="mt-0.5 shrink-0 text-[color:var(--fs-text-secondary)]"><circle cx="12" cy="12" r="9" /></svg>
          <div className="min-w-0 [overflow-wrap:anywhere]">
            <p className="font-medium">{gap.query}</p>
            <p className="text-[color:var(--fs-text-secondary)]">{gap.hits} hits. Last seen: {addedText(gap.last_seen)}</p>
          </div>
        </div>
        {canWrite && <button className={secondary} disabled={busy || !writable} onClick={() => resolve(gap.id)}>Mark resolved</button>}
      </li>)}
    </ul>}
  </section>;
}
