"use client";

import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Users,
  Cpu,
  Pin,
  Sliders,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { api, post } from "@/lib/api";
import { Card, Button, Modal } from "@/components/ui/kit";
import { WorkroomAgent } from "../types";

const ROLE_BADGES: Record<string, { label: string; color: string; border: string }> = {
  planner: { label: "Planner", color: "bg-sky-50 text-sky-700", border: "border-sky-200" },
  architect: { label: "Architect", color: "bg-indigo-50 text-indigo-700", border: "border-indigo-200" },
  investigator: { label: "Investigator", color: "bg-purple-50 text-purple-700", border: "border-purple-200" },
  implementer: { label: "Implementer", color: "bg-amber-50 text-amber-700", border: "border-amber-200" },
  reviewer: { label: "Reviewer", color: "bg-rose-50 text-rose-700", border: "border-rose-200" },
  qa: { label: "QA Specialist", color: "bg-emerald-50 text-emerald-700", border: "border-emerald-200" },
};

interface WorkroomRosterProps {
  projectId: string;
}

export function WorkroomRoster({ projectId }: WorkroomRosterProps) {
  const qc = useQueryClient();
  const [selectedAgent, setSelectedAgent] = useState<WorkroomAgent | null>(null);
  const [overrideJson, setOverrideJson] = useState("");
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data: agents = [], isLoading } = useQuery({
    queryKey: ["pm-workroom-agents", projectId],
    queryFn: () => api<WorkroomAgent[]>(`/api/admin/pm/projects/${projectId}/workroom/agents`),
  });

  const assignMutation = useMutation({
    mutationFn: (payload: { definition_id: string; version_id?: string | null; policy_override?: Record<string, unknown> }) =>
      post(`/api/admin/pm/projects/${projectId}/workroom/agents/assign`, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pm-workroom-agents", projectId] });
      setSelectedAgent(null);
    },
  });

  const handleOpenAssign = (agent: WorkroomAgent) => {
    setSelectedAgent(agent);
    setOverrideJson(JSON.stringify(agent.policy_override || {}, null, 2));
  };

  const handleSaveAssignment = () => {
    if (!selectedAgent) return;
    let policy: Record<string, unknown> = {};
    try {
      policy = JSON.parse(overrideJson || "{}");
    } catch {
      alert("Invalid JSON policy override");
      return;
    }
    assignMutation.mutate({
      definition_id: selectedAgent.definition_id,
      version_id: selectedAgent.current_version_id,
      policy_override: policy,
    });
  };

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {[1, 2, 3, 4, 5, 6].map((i) => (
          <div key={i} className="h-40 rounded-xl bg-ink-50 animate-pulse border border-ink-100" />
        ))}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-ink-900 flex items-center gap-2">
            <Users size={16} className="text-brand-600" />
            Project Agent Roster
          </h3>
          <p className="text-xs text-ink-500 mt-0.5">
            Immutable, bounded role definitions pinned to this project workspace.
          </p>
        </div>
        <span className="text-xs font-mono bg-ink-100 text-ink-700 px-2 py-0.5 rounded-full">
          {agents.length} Registered Roles
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
        {agents.map((agent) => {
          const badge = ROLE_BADGES[agent.name.toLowerCase()] || {
            label: agent.name,
            color: "bg-ink-100 text-ink-700",
            border: "border-ink-200",
          };
          const isExpanded = expandedId === agent.definition_id;

          return (
            <Card
              key={agent.definition_id}
              className={`p-3.5 border transition-all hover:shadow-sm ${
                agent.is_assigned ? "border-brand-200 bg-white" : "border-ink-200/80 bg-white"
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex items-center gap-2">
                  <div className={`p-1.5 rounded-lg border ${badge.border} ${badge.color}`}>
                    <Cpu size={15} />
                  </div>
                  <div>
                    <h4 className="text-[13px] font-semibold text-ink-900 capitalize">
                      {agent.name}
                    </h4>
                    <span className="text-[10px] font-mono text-ink-400">
                      {agent.model_policy_ref || "default_policy"}
                    </span>
                  </div>
                </div>

                <div className="flex items-center gap-1">
                  <span className={`text-[10.5px] font-medium px-2 py-0.5 rounded-md ${badge.color}`}>
                    {badge.label}
                  </span>
                </div>
              </div>

              <div className="mt-2.5 pt-2 border-t border-ink-100 flex items-center justify-between text-xs">
                <div className="flex items-center gap-1.5 text-ink-600">
                  <Pin size={12} className={agent.is_assigned ? "text-brand-600" : "text-ink-300"} />
                  <span className="text-[11px] font-mono">
                    {agent.is_assigned
                      ? `Pinned: v${agent.pinned_version_number || 1}`
                      : `Latest: v${agent.current_version_number || 1}`}
                  </span>
                </div>

                <div className="flex items-center gap-1.5">
                  <Button
                    size="sm"
                    variant="secondary"
                    className="h-6 px-2 text-[11px]"
                    onClick={() => handleOpenAssign(agent)}
                  >
                    <Sliders size={11} className="mr-1" />
                    Configure
                  </Button>
                  <button
                    onClick={() => setExpandedId(isExpanded ? null : agent.definition_id)}
                    className="p-1 hover:bg-ink-100 rounded text-ink-400 hover:text-ink-700 transition"
                  >
                    {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  </button>
                </div>
              </div>

              {isExpanded && (
                <div className="mt-2.5 pt-2 border-t border-dashed border-ink-100 space-y-2 text-xs">
                  <div>
                    <span className="text-[10px] uppercase font-semibold text-ink-400 tracking-wider">
                      Role Identity
                    </span>
                    <p className="text-[11.5px] text-ink-600 leading-relaxed mt-0.5 line-clamp-3">
                      {agent.soul_text || "No identity instructions configured."}
                    </p>
                  </div>
                  <div>
                    <span className="text-[10px] uppercase font-semibold text-ink-400 tracking-wider">
                      Capability Boundary
                    </span>
                    <div className="flex flex-wrap gap-1 mt-1">
                      {agent.capabilities.map((cap) => (
                        <span
                          key={cap}
                          className="px-1.5 py-0.5 bg-ink-100 text-ink-600 rounded text-[10px] font-mono"
                        >
                          {cap}
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
              )}
            </Card>
          );
        })}
      </div>

      {/* Assignment / Pinning Modal */}
      {selectedAgent && (
        <Modal
          isOpen={!!selectedAgent}
          onClose={() => setSelectedAgent(null)}
          title={`Configure & Pin: ${selectedAgent.name}`}
        >
          <div className="space-y-4">
            <div>
              <p className="text-xs text-ink-600">
                Pinning ensures that global prompt or instruction updates to this agent do not alter ongoing runs in this project.
              </p>
              <div className="mt-2 p-2.5 rounded-lg bg-ink-50 border border-ink-200 text-xs flex justify-between items-center">
                <span className="text-ink-600">Active Version to Pin:</span>
                <span className="font-mono font-semibold text-ink-900">
                  Version {selectedAgent.current_version_number || 1}
                </span>
              </div>
            </div>

            <div>
              <label className="text-xs font-semibold text-ink-800 block mb-1">
                Project Policy Override (JSON)
              </label>
              <textarea
                value={overrideJson}
                onChange={(e) => setOverrideJson(e.target.value)}
                rows={4}
                className="w-full font-mono text-xs p-2.5 rounded-lg border border-ink-200 focus:border-brand-500 focus:outline-none"
                placeholder='{"max_diff_lines": 200, "strict_types": true}'
              />
              <p className="text-[11px] text-ink-400 mt-1">
                Optional custom policy flags applied when this role executes tasks for this project.
              </p>
            </div>

            <div className="flex justify-end gap-2 pt-2 border-t border-ink-100">
              <Button variant="secondary" size="sm" onClick={() => setSelectedAgent(null)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={handleSaveAssignment}
                disabled={assignMutation.isPending}
              >
                {assignMutation.isPending ? "Pinning…" : "Pin to Project"}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
