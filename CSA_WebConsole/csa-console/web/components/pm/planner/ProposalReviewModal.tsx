"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronRight,
  Clock,
  FileCheck,
  Layers,
  ListTodo,
  Loader2,
  Milestone as MilestoneIcon,
  ShieldAlert,
  Sparkles,
  Target,
  X,
} from "lucide-react";
import { api, post } from "@/lib/api";
import { Banner, Button, Pill } from "@/components/ui/kit";
import {
  PlanTaskDraft,
  ProposalDetail,
  TaskPriority,
} from "../types";

type Props = {
  projectId: string;
  proposalId: string;
  isOpen: boolean;
  onClose: () => void;
  onAccepted: () => void;
};

export function ProposalReviewModal({
  projectId,
  proposalId,
  isOpen,
  onClose,
  onAccepted,
}: Props) {
  const qc = useQueryClient();
  const [selectedTaskIds, setSelectedTaskIds] = useState<string[]>([]);
  const [initializedSelection, setInitializedSelection] = useState(false);
  const [isAccepting, setIsAccepting] = useState(false);
  const [acceptError, setAcceptError] = useState("");
  const [activeTab, setActiveTab] = useState<"tasks" | "context">("tasks");

  // Fetch proposal detail
  const { data: proposal, isLoading, isError } = useQuery<ProposalDetail>({
    queryKey: ["pm-proposal", projectId, proposalId],
    queryFn: () => api<ProposalDetail>(`/api/admin/pm/projects/${projectId}/proposals/${proposalId}`),
    enabled: isOpen && !!proposalId,
  });

  // Initialize all tasks as selected by default
  const tasks = useMemo(() => proposal?.structured_body?.tasks || [], [proposal]);
  const milestones = useMemo(() => proposal?.structured_body?.milestones || [], [proposal]);
  const assumptions = useMemo(() => proposal?.structured_body?.assumptions || [], [proposal]);
  const risks = useMemo(() => proposal?.structured_body?.risks || [], [proposal]);
  const goals = useMemo(() => proposal?.structured_body?.goals || [], [proposal]);

  if (tasks.length > 0 && !initializedSelection && selectedTaskIds.length === 0) {
    setSelectedTaskIds(tasks.map((t) => t.id));
    setInitializedSelection(true);
  }

  // Validate dependency consistency dynamically
  const dependencyConflicts = useMemo(() => {
    const selectedSet = new Set(selectedTaskIds);
    const conflicts: { task: PlanTaskDraft; missingDep: string }[] = [];

    for (const t of tasks) {
      if (selectedSet.has(t.id)) {
        for (const dep of t.depends_on || []) {
          if (!selectedSet.has(dep)) {
            conflicts.push({ task: t, missingDep: dep });
          }
        }
      }
    }
    return conflicts;
  }, [tasks, selectedTaskIds]);

  if (!isOpen) return null;

  const toggleTask = (id: string) => {
    setSelectedTaskIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
  };

  const selectAll = () => {
    setSelectedTaskIds(tasks.map((t) => t.id));
  };

  const deselectAll = () => {
    setSelectedTaskIds([]);
  };

  const handleAcceptProposal = async () => {
    if (dependencyConflicts.length > 0) return;
    setIsAccepting(true);
    setAcceptError("");

    try {
      await post(`/api/admin/pm/projects/${projectId}/proposals/${proposalId}/accept`, {
        selected_task_ids: selectedTaskIds,
        idempotency_key: `accept-${proposalId}-${selectedTaskIds.sort().join(",")}`,
      });

      // Invalidate relevant caches
      qc.invalidateQueries({ queryKey: ["pm-tasks", projectId] });
      qc.invalidateQueries({ queryKey: ["pm-milestones", projectId] });
      qc.invalidateQueries({ queryKey: ["pm-activity", projectId] });
      qc.invalidateQueries({ queryKey: ["pm-proposal", projectId, proposalId] });

      onAccepted();
    } catch (err: unknown) {
      const e = err as { detail?: string; message?: string };
      setAcceptError(e.detail || e.message || "Failed to accept proposal");
    } finally {
      setIsAccepting(false);
    }
  };

  // Group tasks by milestone
  const milestoneMap = new Map<string, PlanTaskDraft[]>();
  const unassignedTasks: PlanTaskDraft[] = [];

  for (const t of tasks) {
    if (t.milestone_id) {
      if (!milestoneMap.has(t.milestone_id)) {
        milestoneMap.set(t.milestone_id, []);
      }
      milestoneMap.get(t.milestone_id)!.push(t);
    } else {
      unassignedTasks.push(t);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="relative w-full max-w-4xl bg-white rounded-2xl shadow-2xl border border-slate-100 flex flex-col max-h-[92vh] overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between bg-gradient-to-r from-blue-50/50 via-indigo-50/20 to-white">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-blue-600/10 flex items-center justify-center text-blue-600 ring-1 ring-blue-500/20">
              <FileCheck className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h2 className="text-base font-semibold text-slate-900">
                  Review &amp; Accept Proposed Plan
                </h2>
                <Pill tone={proposal?.status === "accepted" ? "good" : "brand"}>
                  {proposal?.status === "accepted" ? "Accepted" : `Rev ${proposal?.revision_number || 1}`}
                </Pill>
              </div>
              <p className="text-xs text-slate-500 font-mono">
                Hash: {proposal?.proposal_hash?.slice(0, 16)}...
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

        {/* View Switcher Tabs */}
        <div className="px-6 pt-3 border-b border-slate-100 flex items-center gap-6 text-xs font-medium">
          <button
            onClick={() => setActiveTab("tasks")}
            className={`pb-2.5 flex items-center gap-1.5 transition-colors border-b-2 ${
              activeTab === "tasks"
                ? "border-blue-600 text-blue-600 font-semibold"
                : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            <ListTodo className="w-4 h-4" />
            Proposed Tasks &amp; Milestones ({tasks.length})
          </button>
          <button
            onClick={() => setActiveTab("context")}
            className={`pb-2.5 flex items-center gap-1.5 transition-colors border-b-2 ${
              activeTab === "context"
                ? "border-blue-600 text-blue-600 font-semibold"
                : "border-transparent text-slate-500 hover:text-slate-800"
            }`}
          >
            <Target className="w-4 h-4" />
            Goals, Assumptions &amp; Risks
          </button>
        </div>

        {/* Content Body */}
        <div className="flex-1 overflow-y-auto p-6 space-y-6">
          {acceptError && (
            <Banner tone="error" onClose={() => setAcceptError("")}>
              <div>
                <strong>Acceptance Conflict:</strong> {acceptError}
              </div>
            </Banner>
          )}

          {/* Dependency conflict warnings */}
          {dependencyConflicts.length > 0 && (
            <div className="p-3.5 rounded-xl border border-amber-200 bg-amber-50/70 text-amber-900 text-xs flex items-start gap-3">
              <AlertCircle className="w-4 h-4 text-amber-600 shrink-0 mt-0.5" />
              <div className="space-y-1">
                <span className="font-semibold">Unresolved Dependency Conflict:</span>
                {dependencyConflicts.map((c, i) => (
                  <p key={i} className="text-amber-800">
                    Task <strong>&quot;{c.task.title}&quot;</strong> ({c.task.id}) depends on predecessor task <strong>&quot;{c.missingDep}&quot;</strong>, which is currently unselected. You must select <strong>&quot;{c.missingDep}&quot;</strong> or uncheck <strong>&quot;{c.task.title}&quot;</strong> before accepting.
                  </p>
                ))}
              </div>
            </div>
          )}

          {isLoading ? (
            <div className="p-8 flex items-center justify-center gap-2 text-sm text-slate-400">
              <Loader2 className="w-5 h-5 animate-spin text-blue-500" />
              Loading structured proposal...
            </div>
          ) : isError || !proposal ? (
            <Banner tone="error">Unable to load proposal detail.</Banner>
          ) : activeTab === "tasks" ? (
            <div className="space-y-6">
              {/* Batch Actions Bar */}
              <div className="flex items-center justify-between text-xs text-slate-500 bg-slate-50/80 px-4 py-2 rounded-xl border border-slate-100">
                <div className="flex items-center gap-3">
                  <span className="font-medium text-slate-700">
                    {selectedTaskIds.length} of {tasks.length} tasks selected
                  </span>
                  <span className="text-slate-300">|</span>
                  <button
                    type="button"
                    onClick={selectAll}
                    className="text-blue-600 hover:underline"
                  >
                    Select All
                  </button>
                  <button
                    type="button"
                    onClick={deselectAll}
                    className="text-slate-500 hover:underline"
                  >
                    Deselect All
                  </button>
                </div>
                <span className="text-[11px] text-slate-400">
                  Acceptance creates milestones &amp; tasks atomically
                </span>
              </div>

              {/* Milestones and Tasks Tree */}
              <div className="space-y-4">
                {milestones.map((m) => {
                  const mTasks = milestoneMap.get(m.id) || [];
                  const selectedCount = mTasks.filter((t) => selectedTaskIds.includes(t.id)).length;

                  return (
                    <div
                      key={m.id}
                      className="border border-slate-200/80 rounded-xl overflow-hidden bg-white shadow-xs"
                    >
                      {/* Milestone Header */}
                      <div className="px-4 py-3 bg-gradient-to-r from-slate-50 to-slate-100/50 border-b border-slate-200/60 flex items-center justify-between">
                        <div className="flex items-center gap-2.5">
                          <MilestoneIcon className="w-4 h-4 text-blue-600" />
                          <span className="text-xs font-semibold text-slate-900">
                            {m.title}
                          </span>
                          <span className="text-[10px] font-mono text-slate-400">
                            ({m.id})
                          </span>
                        </div>
                        <span className="text-[11px] text-slate-500 font-medium">
                          {selectedCount}/{mTasks.length} tasks accepted
                        </span>
                      </div>

                      {/* Milestone Tasks */}
                      <div className="divide-y divide-slate-100">
                        {mTasks.map((t) => (
                          <TaskProposalRow
                            key={t.id}
                            task={t}
                            selected={selectedTaskIds.includes(t.id)}
                            onToggle={() => toggleTask(t.id)}
                          />
                        ))}
                      </div>
                    </div>
                  );
                })}

                {/* Tasks without Milestones */}
                {unassignedTasks.length > 0 && (
                  <div className="border border-slate-200/80 rounded-xl overflow-hidden bg-white shadow-xs">
                    <div className="px-4 py-2.5 bg-slate-50 border-b border-slate-200/60 text-xs font-semibold text-slate-700">
                      Unassigned Tasks
                    </div>
                    <div className="divide-y divide-slate-100">
                      {unassignedTasks.map((t) => (
                        <TaskProposalRow
                          key={t.id}
                          task={t}
                          selected={selectedTaskIds.includes(t.id)}
                          onToggle={() => toggleTask(t.id)}
                        />
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          ) : (
            /* Context tab: Goals, Assumptions, Risks */
            <div className="space-y-6 text-xs">
              {/* Goals */}
              <div className="border border-slate-200 rounded-xl p-4 space-y-2 bg-slate-50/30">
                <h4 className="font-semibold text-slate-900 flex items-center gap-2">
                  <Target className="w-4 h-4 text-blue-600" />
                  Target Project Goals
                </h4>
                <ul className="list-disc list-inside space-y-1 text-slate-700">
                  {goals.map((g, i) => (
                    <li key={i}>{g}</li>
                  ))}
                </ul>
              </div>

              {/* Assumptions */}
              <div className="border border-slate-200 rounded-xl p-4 space-y-2 bg-slate-50/30">
                <h4 className="font-semibold text-slate-900 flex items-center gap-2">
                  <Layers className="w-4 h-4 text-indigo-600" />
                  Planning Assumptions
                </h4>
                <ul className="list-disc list-inside space-y-1 text-slate-700">
                  {assumptions.map((a, i) => (
                    <li key={i}>{a}</li>
                  ))}
                </ul>
              </div>

              {/* Risks & Mitigations */}
              <div className="border border-slate-200 rounded-xl p-4 space-y-2 bg-slate-50/30">
                <h4 className="font-semibold text-slate-900 flex items-center gap-2">
                  <ShieldAlert className="w-4 h-4 text-amber-600" />
                  Identified Risks &amp; Mitigations
                </h4>
                <div className="space-y-2">
                  {risks.map((r, i) => (
                    <div key={i} className="p-2.5 rounded-lg border border-slate-200 bg-white space-y-1">
                      <div className="font-medium text-slate-900">⚠️ {r.risk}</div>
                      <div className="text-slate-600 text-[11px]">Mitigation: {r.mitigation}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Footer Actions */}
        <div className="px-6 py-4 border-t border-slate-100 flex items-center justify-between bg-slate-50/60">
          <div className="text-xs text-slate-600 font-medium">
            {selectedTaskIds.length === 0 ? (
              <span className="text-amber-600">No tasks selected</span>
            ) : (
              <span>
                Accepting <strong>{selectedTaskIds.length}</strong> tasks into the live board
              </span>
            )}
          </div>

          <div className="flex items-center gap-3">
            <Button variant="secondary" onClick={onClose} disabled={isAccepting}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={handleAcceptProposal}
              disabled={
                isAccepting ||
                selectedTaskIds.length === 0 ||
                dependencyConflicts.length > 0 ||
                proposal?.status === "accepted"
              }
            >
              {isAccepting ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin mr-2" />
                  Accepting Plan...
                </>
              ) : proposal?.status === "accepted" ? (
                <>
                  <Check className="w-4 h-4 mr-1.5" />
                  Already Accepted
                </>
              ) : (
                <>
                  <Sparkles className="w-4 h-4 mr-1.5" />
                  Accept Selected into Board
                </>
              )}
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
}

function TaskProposalRow({
  task,
  selected,
  onToggle,
}: {
  task: PlanTaskDraft;
  selected: boolean;
  onToggle: () => void;
}) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      className={`px-4 py-3 transition-colors ${
        selected ? "bg-white" : "bg-slate-50/50 opacity-60"
      }`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3 min-w-0">
          <input
            type="checkbox"
            checked={selected}
            onChange={onToggle}
            className="mt-1 rounded border-slate-300 text-blue-600 focus:ring-blue-500"
          />
          <div className="space-y-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs font-semibold text-slate-900 truncate">
                {task.title}
              </span>
              <PriorityPill priority={task.priority} />
              <span className="text-[10px] font-mono text-slate-400 bg-slate-100 px-1 rounded">
                {task.id}
              </span>
              {task.estimated_effort && (
                <span className="text-[11px] text-slate-500 flex items-center gap-1 font-mono">
                  <Clock className="w-3 h-3 text-slate-400" />
                  {task.estimated_effort}
                </span>
              )}
            </div>

            <p className="text-xs text-slate-600 line-clamp-1">
              {task.description}
            </p>

            {/* Predecessors chips */}
            {task.depends_on && task.depends_on.length > 0 && (
              <div className="flex items-center gap-1.5 text-[11px] text-slate-500 pt-0.5">
                <span className="text-slate-400">Depends on:</span>
                {task.depends_on.map((dep) => (
                  <span
                    key={dep}
                    className="font-mono text-[10px] bg-slate-100 text-slate-700 px-1.5 py-0.5 rounded border border-slate-200"
                  >
                    {dep}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>

        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="text-xs text-slate-400 hover:text-slate-700 mt-1 shrink-0"
        >
          {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
        </button>
      </div>

      {/* Expanded Details: Acceptance Criteria */}
      {expanded && (
        <div className="mt-3 pl-7 pt-3 border-t border-slate-100 space-y-2">
          <div className="text-[11px] font-semibold text-slate-700 uppercase tracking-wider">
            Acceptance Criteria Checklist
          </div>
          <div className="space-y-1">
            {task.acceptance_criteria.map((ac, idx) => (
              <div key={idx} className="flex items-center gap-2 text-xs text-slate-600">
                <div className="w-3.5 h-3.5 rounded border border-slate-300 flex items-center justify-center text-slate-400">
                  ✓
                </div>
                <span>{ac.item}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function PriorityPill({ priority }: { priority: TaskPriority }) {
  switch (priority) {
    case "urgent":
      return <Pill tone="bad">Urgent</Pill>;
    case "high":
      return <Pill tone="warn">High</Pill>;
    case "medium":
      return <Pill tone="brand">Medium</Pill>;
    case "low":
    default:
      return <Pill tone="neutral">Low</Pill>;
  }
}
