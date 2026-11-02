"use client";

import { useEffect, useState } from "react";
import { avatarForAgent } from "@/lib/agent-avatars";
import { outcomeOf, plainSummary } from "@/lib/activity-view";
import { api } from "@/lib/api";
import type { AgentRun, RuntimeEvent } from "@/lib/identity-contracts";
import AgentAvatar from "../fs/AgentAvatar";
import { DetailsRow } from "../kit";
import { Notice } from "../ui";
import OutcomeBadge from "./OutcomeBadge";

/**
 * One run: a plain sentence and how it ended, with the technical record behind the arrow. The
 * original lines (run number, capability, status code, error code, time, milliseconds, trace id
 * and the trace events) keep their exact text inside the opened block. The parent owns `open`
 * (by run id), so the 5-second list refresh never closes a row. The detail read starts when the
 * row opens and stops when it closes.
 */
export default function RunRow({
  run,
  query,
  now,
  open,
  onOpenChange,
}: {
  run: AgentRun;
  query: string;
  now: Date;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const avatar = avatarForAgent(run.agent);
  return (
    <DetailsRow
      testId={`activity-run-${run.id}`}
      toggleLabel={`run ${run.id}`}
      open={open}
      onOpenChange={onOpenChange}
      summary={
        <div className="flex items-center gap-3">
          {avatar && <AgentAvatar agentId={avatar} size={32} label="" state="rest" />}
          <div className="min-w-0">
            <p className="text-sm font-medium">{plainSummary(run, now)}</p>
            <div className="mt-1">
              <OutcomeBadge outcome={outcomeOf(run)} testId={`activity-run-${run.id}-outcome`} />
            </div>
          </div>
        </div>
      }
    >
      <p>
        #{run.id} {run.agent} / {run.status} / {run.error_code ?? (run.ok ? "Succeeded" : "Failed")}
      </p>
      <p className="mt-1 text-[color:var(--fs-text-secondary)]">
        {new Date(run.created_at).toLocaleString()} / {run.latency_ms ?? "Unknown"} ms
      </p>
      {open && <RunDetail run={run} query={query} />}
    </DetailsRow>
  );
}

function RunDetail({ run, query }: { run: AgentRun; query: string }) {
  const [detail, setDetail] = useState<AgentRun | null>(null);
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let cursor = 0;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const params = new URLSearchParams(query);
        params.set("trace_id", run.trace_id);
        params.set("after_id", String(cursor));
        const [current, next] = await Promise.all([
          api<AgentRun>(`/console/v1/runs/${run.id}?${query}`, { signal: controller.signal }),
          api<RuntimeEvent[]>(`/console/v1/events?${params}`, { signal: controller.signal }),
        ]);
        if (!controller.signal.aborted) {
          setDetail(current);
          setEvents((previous) => [...previous, ...next]);
          if (next.length) cursor = next[next.length - 1].id;
          setError("");
        }
      } catch (cause) {
        if (!controller.signal.aborted) setError((cause as Error).message);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 5000);
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [run.id, run.trace_id, query]);
  return (
    <div className="mt-3 border-t border-[var(--fs-border)] pt-3">
      <h3 className="mb-2 text-sm font-semibold">Run #{run.id}</h3>
      {error && <Notice error>{error} (previously loaded detail may be stale)</Notice>}
      {detail && <p className="mb-3 break-all">{detail.agent}: {detail.status}. Trace: {detail.trace_id}</p>}
      <h4 className="mb-2 text-sm font-semibold">Trace events</h4>
      {events.length === 0 ? <p>{detail ? "No recorded events yet." : "Loading detail..."}</p> :
        <ol className="space-y-2">{events.map((event) =>
          <li key={event.id}>#{event.id} {event.node} / {event.agent ?? "Coordinator"} / {event.status} / {event.duration_ms} ms</li>)}</ol>}
    </div>
  );
}
