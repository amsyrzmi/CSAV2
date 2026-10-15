"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BookOpen,
  Loader2,
  Sliders,
  Sparkles,
  Target,
  X,
} from "lucide-react";
import { api, post } from "@/lib/api";
import { Banner, Button, Pill } from "@/components/ui/kit";
import { label, textarea } from "@/components/ui/input";
import { Note } from "../types";

type Props = {
  projectId: string;
  projectName: string;
  projectGoals?: string;
  isOpen: boolean;
  onClose: () => void;
  onRunStarted: (runId: string) => void;
};

export function PlannerModal({
  projectId,
  projectName,
  projectGoals,
  isOpen,
  onClose,
  onRunStarted,
}: Props) {
  const [instruction, setInstruction] = useState("");
  const [includeGoals, setIncludeGoals] = useState(true);
  const [selectedNoteIds, setSelectedNoteIds] = useState<string[]>([]);
  const [showAdvancedBudget, setShowAdvancedBudget] = useState(false);
  const [maxTokens, setMaxTokens] = useState(50000);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  // Fetch project notes to select as grounded context
  const { data: notesData, isLoading: notesLoading } = useQuery<{ items: Note[] }>({
    queryKey: ["pm-notes", projectId],
    queryFn: () => api<{ items: Note[] }>(`/api/admin/pm/projects/${projectId}/notes?limit=50`),
    enabled: isOpen,
  });

  const notes = notesData?.items || [];

  if (!isOpen) return null;

  const toggleNote = (id: string) => {
    setSelectedNoteIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
  };

  const handleStartPlanning = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsSubmitting(true);
    setErrorMsg("");

    try {
      const payload = {
        instruction: instruction.trim(),
        context_refs: {
          include_goals: includeGoals,
          note_ids: selectedNoteIds,
        },
        budgets: {
          max_tokens: maxTokens,
          max_wall_time_sec: 900,
          max_cost_usd: 0.5,
        },
      };

      const res = await post<{ id: string }>(
        `/api/admin/pm/projects/${projectId}/runs`,
        payload
      );
      onRunStarted(res.id);
    } catch (err: unknown) {
      const e = err as { detail?: string; message?: string };
      setErrorMsg(e.detail || e.message || "Failed to start AI planning run");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-sm animate-in fade-in duration-200">
      <div className="relative w-full max-w-2xl bg-white rounded-2xl shadow-2xl border border-slate-100 flex flex-col max-h-[90vh] overflow-hidden">
        {/* Header */}
        <div className="px-6 py-5 border-b border-slate-100 flex items-center justify-between bg-gradient-to-r from-blue-50/50 via-indigo-50/20 to-white">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-blue-600/10 flex items-center justify-center text-blue-600 ring-1 ring-blue-500/20">
              <Sparkles className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <h2 className="text-lg font-semibold text-slate-900 flex items-center gap-2">
                Plan Project with AI
                <Pill tone="brand">Phase 04</Pill>
              </h2>
              <p className="text-xs text-slate-500">
                Ground the planner in project evidence, notes, and goals
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-slate-600 rounded-lg hover:bg-slate-100 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Form Body */}
        <form onSubmit={handleStartPlanning} className="flex-1 overflow-y-auto p-6 space-y-6">
          {errorMsg && (
            <Banner tone="error" onClose={() => setErrorMsg("")}>
              <div>
                <strong>Execution Error:</strong> {errorMsg}
              </div>
            </Banner>
          )}

          {/* User Directive / Instruction */}
          <div className="space-y-1.5">
            <label className={label}>
              Planner Directive & Objectives <span className="text-slate-400">(Optional)</span>
            </label>
            <textarea
              className={textarea}
              rows={3}
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              placeholder="e.g. Focus on MVP core authentication, payment gateway integration, and webhook handlers. Break down into 2-week milestones."
            />
            <p className="text-xs text-slate-500">
              Direct the AI on scope, timeline preferences, architectural constraints, or specific focus areas.
            </p>
          </div>

          {/* Context Evidence Selection */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <label className={label}>Project Evidence & Context References</label>
              <span className="text-xs text-slate-500">
                {selectedNoteIds.length} notes selected
              </span>
            </div>

            {/* Project Goals toggle */}
            <div
              onClick={() => setIncludeGoals(!includeGoals)}
              className={`p-3 rounded-xl border flex items-start gap-3 cursor-pointer transition-all ${
                includeGoals
                  ? "bg-blue-50/60 border-blue-200"
                  : "bg-slate-50/50 border-slate-200 opacity-60"
              }`}
            >
              <div className="mt-0.5 text-blue-600">
                <Target className="w-4 h-4" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-900">
                    Project Goals & Brief
                  </span>
                  <input
                    type="checkbox"
                    checked={includeGoals}
                    onChange={(e) => setIncludeGoals(e.target.checked)}
                    className="rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                  />
                </div>
                <p className="text-xs text-slate-500 truncate mt-0.5">
                  {projectGoals || `Primary goals defined for ${projectName}`}
                </p>
              </div>
            </div>

            {/* Notes Multi-Selector */}
            <div className="border border-slate-200 rounded-xl overflow-hidden divide-y divide-slate-100 max-h-48 overflow-y-auto bg-slate-50/30">
              <div className="px-3 py-2 bg-slate-100/60 text-[11px] font-semibold text-slate-600 flex items-center justify-between">
                <span>PROJECT KNOWLEDGE NOTES</span>
                <span className="text-slate-400">SELECT TO GROUND AI</span>
              </div>

              {notesLoading ? (
                <div className="p-4 flex items-center justify-center gap-2 text-xs text-slate-400">
                  <Loader2 className="w-4 h-4 animate-spin text-blue-500" />
                  Loading project notes...
                </div>
              ) : notes.length === 0 ? (
                <div className="p-4 text-center text-xs text-slate-400">
                  No notes created yet. You can create notes in the &quot;Notes &amp; Knowledge&quot; tab.
                </div>
              ) : (
                notes.map((note) => {
                  const selected = selectedNoteIds.includes(note.id);
                  return (
                    <div
                      key={note.id}
                      onClick={() => toggleNote(note.id)}
                      className={`px-3 py-2.5 flex items-center justify-between gap-3 cursor-pointer hover:bg-slate-100/80 transition-colors ${
                        selected ? "bg-blue-50/40" : ""
                      }`}
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <BookOpen className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                        <span className="text-xs font-medium text-slate-800 truncate">
                          {note.title}
                        </span>
                        <span className="text-[10px] text-slate-400 font-mono">
                          {note.folder}
                        </span>
                      </div>
                      <input
                        type="checkbox"
                        checked={selected}
                        onChange={() => toggleNote(note.id)}
                        className="rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                      />
                    </div>
                  );
                })
              )}
            </div>
          </div>

          {/* Budget & Model Settings */}
          <div className="border border-slate-100 rounded-xl p-3 bg-slate-50/50">
            <div
              onClick={() => setShowAdvancedBudget(!showAdvancedBudget)}
              className="flex items-center justify-between cursor-pointer"
            >
              <div className="flex items-center gap-2 text-xs font-medium text-slate-700">
                <Sliders className="w-3.5 h-3.5 text-slate-500" />
                Budget & Safety Limits
              </div>
              <span className="text-xs text-blue-600 hover:underline">
                {showAdvancedBudget ? "Hide" : "Configure"}
              </span>
            </div>

            {showAdvancedBudget && (
              <div className="mt-3 pt-3 border-t border-slate-200/60 space-y-3">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-slate-600">Token Limit Ceiling:</span>
                  <span className="font-mono text-slate-800">{maxTokens.toLocaleString()} tokens</span>
                </div>
                <input
                  type="range"
                  min={10000}
                  max={100000}
                  step={5000}
                  value={maxTokens}
                  onChange={(e) => setMaxTokens(Number(e.target.value))}
                  className="w-full accent-blue-600"
                />
                <div className="flex items-center justify-between text-[11px] text-slate-500">
                  <span>Max Cost Cap: $0.50</span>
                  <span>Self-Repair Limit: 2 cycles</span>
                  <span>Wall Time: 15 min</span>
                </div>
              </div>
            )}
          </div>

          {/* Footer Actions */}
          <div className="pt-2 flex items-center justify-end gap-3 border-t border-slate-100">
            <Button variant="secondary" type="button" onClick={onClose} disabled={isSubmitting}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" disabled={isSubmitting}>
              {isSubmitting ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin mr-2" />
                  Initiating Run...
                </>
              ) : (
                <>
                  <Sparkles className="w-4 h-4 mr-2" />
                  Generate AI Proposal
                </>
              )}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
