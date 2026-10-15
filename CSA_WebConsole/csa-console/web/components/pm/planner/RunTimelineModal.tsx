"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  Cpu,
  FileCheck2,
  Loader2,
  StopCircle,
  X,
} from "lucide-react";
import { api, post } from "@/lib/api";
import { Banner, Button, Pill } from "@/components/ui/kit";
import { RunDetail, RunEvent } from "../types";

type Props = {
  projectId: string;
  runId: string;
  isOpen: boolean;
  onClose: () => void;
  onOpenProposal: (proposalId: string) => void;
};

export function RunTimelineModal({
  projectId,
  runId,
  isOpen,
  onClose,
  onOpenProposal,
}: Props) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [isCancelling, setIsCancelling] = useState(false);
  const [cancelError, setCancelError] = useState("");

  // Query run detail
  const { data: run, refetch } = useQuery<RunDetail>({
    queryKey: ["pm-run", projectId, runId],
    queryFn: () => api<RunDetail>(`/api/admin/pm/projects/${projectId}/runs/${runId}`),
    enabled: isOpen && !!runId,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status && ["succeeded", "failed", "cancelled", "waiting_review", "waiting_input"].includes(status)) {
        return false;
      }
      return 1500;
    },
  });

  // Subscribe to SSE stream
  useEffect(() => {
    if (!isOpen || !runId) return;

    let es: EventSource | null = null;
    try {
      es = new EventSource(`/api/admin/pm/projects/${projectId}/runs/${runId}/events`);

      es.onmessage = (e) => {
        try {
          const parsed = JSON.parse(e.data);
          if (parsed && parsed.sequence) {
            setEvents((prev) => {
              if (prev.some((p) => p.sequence === parsed.sequence)) return prev;
              return [...prev, parsed].sort((a, b) => a.sequence - b.sequence);
            });
          }
        } catch {
          // heartbeat ping or stream end
        }
      };

      es.addEventListener("run.stream_end", () => {
        refetch();
        es?.close();
      });

      es.addEventListener("proposal.created", (e) => {
        try {
          const parsed = JSON.parse(e.data);
          setEvents((prev) => [...prev, parsed]);
          refetch();
        } catch {
          // ignore
        }
      });

      es.onerror = () => {
        es?.close();
      };
    } catch {
      // SSE connection error fallback
    }

    return () => {
      es?.close();
    };
  }, [isOpen, projectId, runId, refetch]);

  if (!isOpen) return null;

  const status = run?.status || "queued";
  const proposalId = run?.latest_proposal_id;

  const handleCancel = async () => {
    setIsCancelling(true);
    setCancelError("");
    try {
      await post(`/api/admin/pm/projects/${projectId}/runs/${runId}/cancel`, {});
      refetch();
    } catch (err: unknown) {
      const e = err as { detail?: string; message?: string };
      setCancelError(e.detail || e.message || "Failed to cancel run");
    } finally {
      setIsCancelling(false);
    }
  };

  // Determine stage progress
  const hasContext = events.some((e) => e.event_type.startsWith("stage.context_resolved") || e.event_type.startsWith("run.started"));
  const hasAssessed = events.some((e) => e.event_type.startsWith("stage.plan_drafted") || e.event_type.startsWith("stage.waiting_input"));
  const hasDrafted = events.some((e) => e.event_type.startsWith("stage.plan_drafted"));
  const hasRepairs = events.some((e) => e.event_type === "stage.repair_attempted");
  const hasProposal = events.some((e) => e.event_type === "proposal.created") || !!proposalId;

  const STAGES = [
    {
      id: "context",
      title: "1. Evidence Gathering",
      desc: "Resolves project brief, goals, and scoped notes",
      done: hasContext,
      active: status === "running" && !hasAssessed,
    },
    {
      id: "assess",
      title: "2. Information Assessment",
      desc: "Verifies completeness without fabricating requirements",
      done: hasAssessed,
      active: status === "running" && hasContext && !hasDrafted,
    },
    {
      id: "draft",
      title: "3. Plan Generation",
      desc: "Synthesizes milestones, tasks, and acceptance criteria",
      done: hasDrafted,
      active: status === "running" && hasDrafted && !hasProposal,
    },
    {
      id: "validate",
      title: "4. DAG & Acyclicity Validation",
      desc: hasRepairs ? "Self-repair cycle triggered" : "Verifies DAG dependency topology",
      done: hasProposal,
      active: status === "running" && hasDrafted && !hasProposal,
      isRepair: hasRepairs,
    },
    {
      id: "proposal",
      title: "5. Reviewed Proposal Published",
      desc: "Structured proposal ready for human acceptance",
      done: hasProposal,
      active: status === "waiting_review",
    },
  ];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="relative w-full max-w-3xl bg-white rounded-2xl shadow-2xl border border-slate-100 flex flex-col max-h-[90vh] overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between bg-gradient-to-r from-blue-50/40 to-white">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-blue-600/10 flex items-center justify-center text-blue-600">
              <Cpu className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-slate-900 flex items-center gap-2">
                AI Planner Run Execution
                <StatusBadge status={status} />
              </h2>
              <p className="text-xs text-slate-500 font-mono">
                Run ID: {runId.slice(0, 13)}...
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {cancelError && (
            <Banner tone="error" onClose={() => setCancelError("")}>
              <div>
                <strong>Cancellation Error:</strong> {cancelError}
              </div>
            </Banner>
          )}

          {/* Missing info prompt */}
          {status === "waiting_input" && (
            <Banner tone="warn">
              <div>
                <strong>Grounded Context Required:</strong> The planner identified missing requirements or ambiguous directives and paused execution rather than fabricating false specifications.
              </div>
            </Banner>
          )}

          {/* Failure banner */}
          {status === "failed" && (
            <Banner tone="error">
              <div>
                <strong>Planner Run Failed:</strong> {run?.error_message || "Execution encountered an error during planning stages."}
              </div>
            </Banner>
          )}

          {/* Stages Progression Grid */}
          <div className="space-y-3">
            <h3 className="text-xs font-semibold text-slate-500 tracking-wider uppercase">
              Execution Stages &amp; Pipeline
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-5 gap-2">
              {STAGES.map((st) => (
                <div
                  key={st.id}
                  className={`p-3 rounded-xl border flex flex-col justify-between transition-all ${
                    st.done
                      ? "bg-emerald-50/60 border-emerald-200"
                      : st.active
                      ? "bg-blue-50/70 border-blue-300 ring-2 ring-blue-500/10"
                      : "bg-slate-50/50 border-slate-200 opacity-60"
                  }`}
                >
                  <div className="flex items-center justify-between mb-1">
                    {st.done ? (
                      <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                    ) : st.active ? (
                      <Loader2 className="w-4 h-4 text-blue-600 animate-spin" />
                    ) : (
                      <div className="w-4 h-4 rounded-full border-2 border-slate-300" />
                    )}
                    {st.isRepair && (
                      <span className="text-[10px] font-mono text-amber-600 bg-amber-100 px-1 rounded">
                        Repair
                      </span>
                    )}
                  </div>
                  <div>
                    <h4 className="text-xs font-semibold text-slate-900 leading-tight">
                      {st.title}
                    </h4>
                    <p className="text-[10px] text-slate-500 line-clamp-2 mt-1">
                      {st.desc}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Live Events Feed */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h3 className="text-xs font-semibold text-slate-500 tracking-wider uppercase">
                Chronological Event Stream ({events.length})
              </h3>
              <span className="text-[11px] text-slate-400 font-mono">
                Last sequence: {events[events.length - 1]?.sequence || 0}
              </span>
            </div>
            <div className="border border-slate-200 rounded-xl bg-slate-900 text-slate-200 p-3 font-mono text-xs max-h-56 overflow-y-auto space-y-1.5 shadow-inner">
              {events.length === 0 ? (
                <div className="text-slate-500 italic py-2">
                  Waiting for initial events...
                </div>
              ) : (
                events.map((evt) => (
                  <div key={evt.id || evt.sequence} className="flex items-start gap-2 text-[11px]">
                    <span className="text-slate-500 shrink-0">
                      [{String(evt.sequence).padStart(2, "0")}]
                    </span>
                    <span className="text-blue-400 shrink-0">{evt.event_type}</span>
                    <span className="text-slate-400 truncate">
                      {JSON.stringify(evt.public_payload || {})}
                    </span>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        {/* Footer Actions */}
        <div className="px-6 py-4 border-t border-slate-100 flex items-center justify-between bg-slate-50/50">
          <div>
            {["queued", "running"].includes(status) && (
              <Button
                variant="danger"
                onClick={handleCancel}
                disabled={isCancelling}
              >
                {isCancelling ? (
                  <Loader2 className="w-4 h-4 animate-spin mr-1" />
                ) : (
                  <StopCircle className="w-4 h-4 mr-1" />
                )}
                Cancel Run
              </Button>
            )}
          </div>

          <div className="flex items-center gap-3">
            <Button variant="secondary" onClick={onClose}>
              Close
            </Button>
            {proposalId && (
              <Button
                variant="primary"
                onClick={() => {
                  onClose();
                  onOpenProposal(proposalId);
                }}
              >
                <FileCheck2 className="w-4 h-4 mr-1.5" />
                Review &amp; Accept Proposal
              </Button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  switch (status) {
    case "running":
      return <Pill tone="brand">Running</Pill>;
    case "queued":
      return <Pill tone="neutral">Queued</Pill>;
    case "waiting_review":
    case "waiting_approval":
      return <Pill tone="good">Proposal Ready</Pill>;
    case "waiting_input":
      return <Pill tone="warn">Waiting Input</Pill>;
    case "succeeded":
      return <Pill tone="good">Succeeded</Pill>;
    case "failed":
      return <Pill tone="bad">Failed</Pill>;
    case "cancelled":
      return <Pill tone="neutral">Cancelled</Pill>;
    default:
      return <Pill tone="neutral">{status}</Pill>;
  }
}
