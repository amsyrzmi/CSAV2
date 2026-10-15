"use client";

import React from "react";
import {
  Search,
  Code2,
  ShieldCheck,
  CheckCheck,
  UserCheck,
  ArrowRight,
  RotateCcw,
  AlertCircle,
  GitPullRequest,
  CheckCircle,
} from "lucide-react";
import { WorkroomRun, TaskCompletionStatus } from "../types";

const STAGES: Array<{
  key: string;
  role: string;
  label: string;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  color: string;
}> = [
  { key: "investigating", role: "investigator", label: "Investigation", icon: Search, color: "text-purple-600 border-purple-200 bg-purple-50" },
  { key: "implementing", role: "implementer", label: "Implementation", icon: Code2, color: "text-amber-600 border-amber-200 bg-amber-50" },
  { key: "reviewing", role: "reviewer", label: "Independent Review", icon: ShieldCheck, color: "text-rose-600 border-rose-200 bg-rose-50" },
  { key: "qa_testing", role: "qa", label: "QA Sandbox", icon: CheckCheck, color: "text-emerald-600 border-emerald-200 bg-emerald-50" },
  { key: "review_ready", role: "human_operator", label: "Review Bundle", icon: UserCheck, color: "text-brand-600 border-brand-200 bg-brand-50" },
];

const COMPLETION_STATUS_MAP: Record<TaskCompletionStatus, { label: string; badge: string }> = {
  pending: { label: "Pending Execution", badge: "bg-ink-100 text-ink-700 border-ink-200" },
  patch_proposed: { label: "Patch Proposed", badge: "bg-amber-50 text-amber-700 border-amber-200" },
  tests_passed_sandbox: { label: "Tests Passed (Sandbox)", badge: "bg-emerald-50 text-emerald-700 border-emerald-200" },
  published_repository: { label: "Published to Repository", badge: "bg-purple-50 text-purple-700 border-purple-200" },
  deployed: { label: "Deployed", badge: "bg-brand-50 text-brand-700 border-brand-200" },
};

interface HandoffTimelineProps {
  run?: WorkroomRun | null;
}

export function HandoffTimeline({ run }: HandoffTimelineProps) {
  if (!run) {
    return null;
  }

  const stageOrder = ["queued", "investigating", "implementing", "reviewing", "repairing", "qa_testing", "review_ready", "approved"];
  const currentIdx = stageOrder.indexOf(run.current_stage);
  const completionBadge = COMPLETION_STATUS_MAP[run.task_completion_status] || {
    label: run.task_completion_status,
    badge: "bg-ink-100 text-ink-700 border-ink-200",
  };

  return (
    <div className="rounded-xl border border-ink-200 bg-white p-4 shadow-sm space-y-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-ink-100 pb-3">
        <div className="flex items-center gap-2">
          <GitPullRequest size={16} className="text-brand-600" />
          <span className="text-xs font-semibold text-ink-900">
            {run.title}
          </span>
          <span className="text-[11px] font-mono text-ink-400">
            ({run.workflow_type})
          </span>
        </div>

        <div className="flex items-center gap-2">
          {run.repair_cycles > 0 && (
            <span className="inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">
              <RotateCcw size={11} />
              Repair Cycle {run.repair_cycles}/{run.max_repair_cycles}
            </span>
          )}

          <span
            className={`text-[11px] font-medium px-2.5 py-0.5 rounded-full border ${completionBadge.badge}`}
          >
            {completionBadge.label}
          </span>
        </div>
      </div>

      {/* Sequential Handoff Flow */}
      <div className="flex items-center justify-between gap-1 overflow-x-auto py-1">
        {STAGES.map((s, idx) => {
          const Icon = s.icon;
          const stageIndex = stageOrder.indexOf(s.key);
          const isDone = currentIdx > stageIndex || run.status === "waiting_review";
          const isActive = run.current_stage === s.key || (s.key === "implementing" && run.current_stage === "repairing");

          return (
            <React.Fragment key={s.key}>
              <div
                className={`flex flex-col items-center p-2 rounded-xl border text-center transition-all min-w-[100px] flex-1 ${
                  isActive
                    ? `${s.color} ring-2 ring-brand-500/20 shadow-sm font-semibold`
                    : isDone
                    ? "bg-ink-50/80 border-ink-200 text-ink-700"
                    : "bg-ink-50/40 border-ink-100 text-ink-400 opacity-60"
                }`}
              >
                <div className="p-1.5 rounded-lg bg-white/80 shadow-xs mb-1">
                  {isDone && !isActive ? (
                    <CheckCircle size={16} className="text-emerald-600" />
                  ) : (
                    <Icon size={16} />
                  )}
                </div>
                <span className="text-[11px] whitespace-nowrap capitalize">
                  {s.label}
                </span>
                <span className="text-[9.5px] font-mono text-ink-400 capitalize mt-0.5">
                  {s.role.replace("_", " ")}
                </span>
              </div>

              {idx < STAGES.length - 1 && (
                <div className="flex items-center justify-center text-ink-300 shrink-0 px-0.5">
                  <ArrowRight size={14} />
                </div>
              )}
            </React.Fragment>
          );
        })}
      </div>

      {/* Status Warning or Error Notice if any */}
      {run.status === "budget_exceeded" && (
        <div className="p-2.5 rounded-lg bg-rose-50 border border-rose-200 text-xs text-rose-800 flex items-center gap-2">
          <AlertCircle size={14} className="shrink-0 text-rose-600" />
          <span>
            <strong>Budget Limit Reached:</strong> {run.error_message || "Token, cost, or wall-time ceiling hit. Branches stopped."}
          </span>
        </div>
      )}

      {run.status === "conflict_detected" && (
        <div className="p-2.5 rounded-lg bg-amber-50 border border-amber-200 text-xs text-amber-800 flex items-center gap-2">
          <AlertCircle size={14} className="shrink-0 text-amber-600" />
          <span>
            <strong>Concurrent Edit Collision:</strong> Merge conflict detected across task branches. Reviewable conflict patch generated.
          </span>
        </div>
      )}
    </div>
  );
}
