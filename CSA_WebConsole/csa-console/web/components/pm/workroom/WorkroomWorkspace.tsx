"use client";

import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Sparkles,
  ShieldCheck,
  GitMerge,
  Play,
  CheckCircle2,
  Plus,
} from "lucide-react";
import { api, post } from "@/lib/api";
import { Button, Card, Modal, Pill } from "@/components/ui/kit";
import { WorkroomRoster } from "./WorkroomRoster";
import { HandoffTimeline } from "./HandoffTimeline";
import { CollaborationFeed } from "./CollaborationFeed";
import { BudgetPanel } from "./BudgetPanel";
import { InterventionBar } from "./InterventionBar";
import { ConflictReviewModal } from "./ConflictReviewModal";
import { WorkroomRun, WorkroomBudgetStatus } from "../types";

interface WorkroomWorkspaceProps {
  projectId: string;
  projectName: string;
}

export function WorkroomWorkspace({ projectId, projectName }: WorkroomWorkspaceProps) {
  const qc = useQueryClient();
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [launchModalOpen, setLaunchModalOpen] = useState(false);
  const [conflictModalOpen, setConflictModalOpen] = useState(false);

  // Form state for launch modal
  const [workflowTitle, setWorkflowTitle] = useState("Bug Fix: Zero Division Arithmetic Error");
  const [workflowType, setWorkflowType] = useState<"bug_fixture" | "feature_implementation" | "architecture_design">("bug_fixture");
  const [symptom, setSymptom] = useState("ZeroDivisionError: division by zero in calculator service when denominator is 0.");
  const [simulateConflict, setSimulateConflict] = useState(false);
  const [simulateBudgetExhaustion, setSimulateBudgetExhaustion] = useState(false);

  // 1. Fetch runs for this project
  const { data: runs = [] } = useQuery({
    queryKey: ["pm-workroom-runs", projectId],
    queryFn: () => api<WorkroomRun[]>(`/api/admin/pm/projects/${projectId}/workroom/runs`),
    refetchInterval: 4000,
  });

  // Determine active run
  const activeRunId = selectedRunId || (runs.length > 0 ? runs[0].id : null);

  // 2. Fetch active run detail
  const { data: currentRun } = useQuery({
    queryKey: ["pm-workroom-run", projectId, activeRunId],
    queryFn: () => api<WorkroomRun>(`/api/admin/pm/projects/${projectId}/workroom/runs/${activeRunId}`),
    enabled: !!activeRunId,
    refetchInterval: 3000,
  });

  // 3. Fetch budget status
  const { data: budgetStatus } = useQuery({
    queryKey: ["pm-workroom-budget", projectId],
    queryFn: () => api<WorkroomBudgetStatus>(`/api/admin/pm/projects/${projectId}/workroom/budget`),
    refetchInterval: 5000,
  });

  // Launch workflow mutation
  const launchMutation = useMutation({
    mutationFn: (payload: {
      title: string;
      workflow_type: string;
      symptom: string;
      simulate_conflict: boolean;
      simulate_budget_exhaustion: boolean;
    }) => post<WorkroomRun>(`/api/admin/pm/projects/${projectId}/workroom/runs`, payload),
    onSuccess: (newRun) => {
      qc.invalidateQueries({ queryKey: ["pm-workroom-runs", projectId] });
      setSelectedRunId(newRun.id);
      setLaunchModalOpen(false);
    },
  });

  const handleLaunchSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    launchMutation.mutate({
      title: workflowTitle.trim(),
      workflow_type: workflowType,
      symptom: symptom.trim(),
      simulate_conflict: simulateConflict,
      simulate_budget_exhaustion: simulateBudgetExhaustion,
    });
  };

  return (
    <div className="space-y-4">
      {/* Workroom Command Header */}
      <div className="flex flex-wrap items-center justify-between gap-3 p-4 rounded-2xl border border-brand-200/80 bg-gradient-to-r from-brand-50/70 via-white to-sky-50/50 shadow-xs">
        <div>
          <div className="flex items-center gap-2">
            <span className="p-1.5 rounded-lg bg-brand-600 text-white shadow-xs">
              <Sparkles size={16} />
            </span>
            <h2 className="text-base font-bold text-ink-900">
              {projectName} • Multi-Agent AI Workroom
            </h2>
            <Pill tone="good">Phase 08 Active</Pill>
          </div>
          <p className="text-xs text-ink-500 mt-1">
            Bounded coordinated workflows across specialized roles with strict aggregate budgets, conflict detection, and human verification gates.
          </p>
        </div>

        <div className="flex items-center gap-2">
          {runs.length > 0 && (
            <select
              value={activeRunId || ""}
              onChange={(e) => setSelectedRunId(e.target.value)}
              className="text-xs font-medium px-3 py-1.5 rounded-lg border border-ink-200 bg-white text-ink-800 focus:outline-none focus:border-brand-500 shadow-2xs max-w-[200px] truncate"
            >
              {runs.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.title} ({r.current_stage})
                </option>
              ))}
            </select>
          )}

          <Button
            size="sm"
            variant="secondary"
            onClick={() => setConflictModalOpen(true)}
            className="text-xs"
          >
            <GitMerge size={13} className="mr-1 text-ink-600" />
            Conflict Checker
          </Button>

          <Button
            size="sm"
            variant="primary"
            onClick={() => setLaunchModalOpen(true)}
            className="text-xs"
          >
            <Plus size={13} className="mr-1" />
            Launch Multi-Agent Workflow
          </Button>
        </div>
      </div>

      {/* Real-time Budget & Quota Telemetry */}
      <BudgetPanel run={currentRun} budgetStatus={budgetStatus} />

      {/* Handoff Progression Timeline */}
      {currentRun && <HandoffTimeline run={currentRun} />}

      {/* Operator Intervention Control Toolbar */}
      {currentRun && <InterventionBar projectId={projectId} run={currentRun} />}

      {/* Main Workroom Workspace Layout: Roster & Review Bundle (Left) + Collaboration Stream (Right) */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        {/* Left Column: Roster & Review Bundle */}
        <div className="lg:col-span-6 space-y-4">
          <WorkroomRoster projectId={projectId} />

          {/* Review Bundle Inspector (when workflow reaches review_ready / waiting_review) */}
          {currentRun?.review_bundle && Object.keys(currentRun.review_bundle).length > 0 && (
            <Card className="p-4 border border-emerald-200 bg-emerald-50/30 shadow-xs space-y-3">
              <div className="flex items-center justify-between border-b border-emerald-200/60 pb-2">
                <div className="flex items-center gap-2">
                  <ShieldCheck size={16} className="text-emerald-700" />
                  <h4 className="text-xs font-bold text-emerald-900">
                    QA Verified Review Bundle (Human Gate)
                  </h4>
                </div>
                <span className="text-[10.5px] font-mono px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800 font-semibold">
                  Verdict: {String(currentRun.review_bundle.verdict || "Verified")}
                </span>
              </div>

              <div className="space-y-2 text-xs">
                <div className="p-2.5 rounded-lg bg-white border border-emerald-100 space-y-1">
                  <div className="flex items-center justify-between text-ink-600">
                    <span>Sandbox Test Command:</span>
                    <span className="font-mono text-ink-900 font-semibold">
                      {String(currentRun.review_bundle.test_command || "pytest")}
                    </span>
                  </div>
                  <div className="flex items-center justify-between text-ink-600">
                    <span>Test Suite Exit Code:</span>
                    <span className="font-mono text-emerald-700 font-bold">
                      {String(currentRun.review_bundle.exit_code ?? 0)} (Passed)
                    </span>
                  </div>
                  <p className="text-[11px] text-emerald-800 font-mono mt-1 pt-1 border-t border-ink-100">
                    {String(currentRun.review_bundle.sandbox_output || "All regression tests passed cleanly.")}
                  </p>
                </div>

                {Array.isArray(currentRun.review_bundle.acceptance_criteria_verified) && (
                  <div className="space-y-1">
                    <span className="text-[10.5px] font-semibold text-emerald-950 uppercase">
                      Acceptance Criteria Checklist
                    </span>
                    <div className="space-y-1">
                      {currentRun.review_bundle.acceptance_criteria_verified.map((ac: { criterion?: string } | string, i: number) => (
                        <div
                          key={i}
                          className="flex items-center gap-1.5 text-[11px] text-emerald-900 bg-white/80 p-1.5 rounded border border-emerald-100"
                        >
                          <CheckCircle2 size={13} className="text-emerald-600 shrink-0" />
                          <span>{typeof ac === "object" && ac !== null ? ac.criterion || JSON.stringify(ac) : String(ac)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                <div className="p-2.5 rounded-lg bg-white/90 border border-emerald-200 text-ink-800">
                  <span className="font-semibold text-emerald-900">Recommended Action:</span>
                  <p className="text-[11.5px] mt-0.5 text-ink-700">
                    {String(currentRun.review_bundle.recommended_action || "Operator review ready.")}
                  </p>
                </div>
              </div>
            </Card>
          )}
        </div>

        {/* Right Column: Typed Collaboration Feed */}
        <div className="lg:col-span-6">
          <CollaborationFeed projectId={projectId} runId={activeRunId} />
        </div>
      </div>

      {/* Launch Multi-Agent Workflow Modal */}
      {launchModalOpen && (
        <Modal
          isOpen={launchModalOpen}
          onClose={() => setLaunchModalOpen(false)}
          title="Launch Multi-Agent Coordinated Workflow"
        >
          <form onSubmit={handleLaunchSubmit} className="space-y-3.5">
            <div>
              <label className="text-xs font-semibold text-ink-800 block mb-1">Workflow Title</label>
              <input
                type="text"
                value={workflowTitle}
                onChange={(e) => setWorkflowTitle(e.target.value)}
                required
                className="w-full text-xs px-2.5 py-2 rounded-lg border border-ink-200 focus:border-brand-500"
              />
            </div>

            <div>
              <label className="text-xs font-semibold text-ink-800 block mb-1">Workflow Type</label>
              <select
                value={workflowType}
                onChange={(e) => setWorkflowType(e.target.value as "bug_fixture" | "feature_implementation" | "architecture_design")}
                className="w-full text-xs px-2.5 py-2 rounded-lg border border-ink-200 focus:border-brand-500 bg-white"
              >
                <option value="bug_fixture">Bug Fixture (Investigator ➔ Implementer ➔ Reviewer ➔ QA)</option>
                <option value="feature_implementation">Feature Implementation (Architect ➔ Implementer ➔ Reviewer)</option>
                <option value="architecture_design">Architecture Design (Architect ➔ Planner)</option>
              </select>
            </div>

            <div>
              <label className="text-xs font-semibold text-ink-800 block mb-1">Observed Symptom / Instruction</label>
              <textarea
                value={symptom}
                onChange={(e) => setSymptom(e.target.value)}
                rows={3}
                required
                className="w-full text-xs p-2.5 rounded-lg border border-ink-200 focus:border-brand-500"
              />
            </div>

            <div className="p-3 rounded-xl bg-ink-50 border border-ink-200 space-y-2">
              <span className="text-[10px] font-semibold uppercase text-ink-500 tracking-wider">
                Acceptance & Verification Simulation Flags
              </span>
              <div className="flex items-center gap-4 text-xs">
                <label className="flex items-center gap-1.5 cursor-pointer text-ink-700">
                  <input
                    type="checkbox"
                    checked={simulateConflict}
                    onChange={(e) => setSimulateConflict(e.target.checked)}
                    className="rounded text-brand-600"
                  />
                  Simulate Concurrent Merge Conflict
                </label>
                <label className="flex items-center gap-1.5 cursor-pointer text-ink-700">
                  <input
                    type="checkbox"
                    checked={simulateBudgetExhaustion}
                    onChange={(e) => setSimulateBudgetExhaustion(e.target.checked)}
                    className="rounded text-brand-600"
                  />
                  Simulate Budget Ceiling Breach
                </label>
              </div>
            </div>

            <div className="flex justify-end gap-2 pt-2 border-t border-ink-100">
              <Button variant="secondary" size="sm" onClick={() => setLaunchModalOpen(false)}>
                Cancel
              </Button>
              <Button
                type="submit"
                variant="primary"
                size="sm"
                disabled={launchMutation.isPending}
              >
                <Play size={12} className="mr-1 fill-current" />
                {launchMutation.isPending ? "Starting Run…" : "Start Workflow"}
              </Button>
            </div>
          </form>
        </Modal>
      )}

      {/* Conflict Review Modal */}
      <ConflictReviewModal
        projectId={projectId}
        isOpen={conflictModalOpen}
        onClose={() => setConflictModalOpen(false)}
      />
    </div>
  );
}
