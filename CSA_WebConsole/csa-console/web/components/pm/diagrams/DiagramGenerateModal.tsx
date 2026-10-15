"use client";

import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BookOpen,
  Loader2,
  Sparkles,
  X,
} from "lucide-react";
import { post } from "@/lib/api";
import { Banner, Button, Pill } from "@/components/ui/kit";
import { label, textarea } from "@/components/ui/input";
import { ArtifactDetail, Note } from "../types";

type Props = {
  projectId: string;
  projectName: string;
  isOpen: boolean;
  onClose: () => void;
  onArtifactGenerated: (artifact: ArtifactDetail) => void;
};

const DIAGRAM_TYPES = [
  { id: "sequence", label: "Sequence Diagram", format: "mermaid", kind: "diagram", desc: "Message flows and actor interactions" },
  { id: "erd", label: "Entity Relationship (ERD)", format: "mermaid", kind: "diagram", desc: "Database tables and relationships" },
  { id: "class", label: "Class Diagram", format: "mermaid", kind: "diagram", desc: "Object models and domain classes" },
  { id: "flowchart", label: "Flowchart / Workflow", format: "mermaid", kind: "diagram", desc: "Process logic and branching" },
  { id: "state", label: "State Machine", format: "mermaid", kind: "diagram", desc: "Finite states and transitions" },
  { id: "aws_cloud", label: "AWS Cloud Architecture", format: "architecture_json", kind: "architecture", desc: "VPC, Subnets, ECS, Aurora, and security boundaries" },
];

export function DiagramGenerateModal({
  projectId,
  projectName,
  isOpen,
  onClose,
  onArtifactGenerated,
}: Props) {
  const [selectedType, setSelectedType] = useState(DIAGRAM_TYPES[0]);
  const [instruction, setInstruction] = useState("");
  const [selectedNoteIds, setSelectedNoteIds] = useState<string[]>([]);
  const [isGenerating, setIsGenerating] = useState(false);
  const [errorMsg, setErrorMsg] = useState("");

  // Load project notes for context selection
  const { data: notesData } = useQuery<{ items: Note[] }>({
    queryKey: ["pm-notes", projectId],
    queryFn: () => fetch(`/api/admin/pm/projects/${projectId}/notes?limit=50`).then((r) => r.json()),
    enabled: isOpen,
  });

  const notes = notesData?.items || [];

  if (!isOpen) return null;

  const toggleNote = (id: string) => {
    setSelectedNoteIds((prev) =>
      prev.includes(id) ? prev.filter((item) => item !== id) : [...prev, id]
    );
  };

  const handleGenerate = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsGenerating(true);
    setErrorMsg("");

    try {
      const payload = {
        kind: selectedType.kind,
        format: selectedType.format,
        diagram_type: selectedType.id,
        instruction: instruction.trim() || `Synthesize a ${selectedType.label} for ${projectName}`,
        context_refs: {
          note_ids: selectedNoteIds,
        },
      };

      const res = await post<ArtifactDetail>(
        `/api/admin/pm/projects/${projectId}/artifacts/generate`,
        payload
      );

      onArtifactGenerated(res);
      onClose();
    } catch (err: unknown) {
      const e = err as { detail?: string; message?: string };
      setErrorMsg(e.detail || e.message || "Failed to generate diagram");
    } finally {
      setIsGenerating(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-xs animate-in fade-in duration-200">
      <div className="relative w-full max-w-2xl bg-white rounded-2xl shadow-2xl border border-slate-100 flex flex-col max-h-[90vh] overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between bg-gradient-to-r from-blue-50/50 via-indigo-50/20 to-white">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-blue-600/10 flex items-center justify-center text-blue-600 ring-1 ring-blue-500/20">
              <Sparkles className="w-5 h-5 text-blue-600" />
            </div>
            <div>
              <h2 className="text-base font-semibold text-slate-900 flex items-center gap-2">
                Generate Diagram or Architecture with AI
                <Pill tone="brand">Phase 05</Pill>
              </h2>
              <p className="text-xs text-slate-500">
                Synthesize sequence flows, data models, or AWS cloud designs
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
        <form onSubmit={handleGenerate} className="flex-1 overflow-y-auto p-6 space-y-6">
          {errorMsg && (
            <Banner tone="error" onClose={() => setErrorMsg("")}>
              <div>
                <strong>Generation Error:</strong> {errorMsg}
              </div>
            </Banner>
          )}

          {/* Diagram Type Grid */}
          <div className="space-y-2">
            <label className={label}>Select Diagram or Architecture Type</label>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              {DIAGRAM_TYPES.map((t) => {
                const isSelected = selectedType.id === t.id;
                return (
                  <div
                    key={t.id}
                    onClick={() => setSelectedType(t)}
                    className={`cursor-pointer p-3 rounded-xl border text-left transition-all ${
                      isSelected
                        ? "border-blue-600 bg-blue-50/40 ring-1 ring-blue-600"
                        : "border-slate-200 hover:border-slate-300 hover:bg-slate-50/50"
                    }`}
                  >
                    <div className="flex items-center justify-between mb-1">
                      <span className="font-semibold text-xs text-slate-900">{t.label}</span>
                      <Pill tone={t.kind === "architecture" ? "brand" : "neutral"}>
                        {t.format === "mermaid" ? "Mermaid" : "Cloud Graph"}
                      </Pill>
                    </div>
                    <p className="text-[11px] text-slate-500 leading-snug">{t.desc}</p>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Directive Input */}
          <div className="space-y-1.5">
            <label className={label}>
              Design Objectives &amp; Directives <span className="text-slate-400">(Optional)</span>
            </label>
            <textarea
              className={textarea}
              rows={3}
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
              placeholder="e.g. Design a high-availability AWS architecture with public ALB, private ECS backend, and encrypted Aurora PostgreSQL cluster."
            />
          </div>

          {/* Scoped Notes Context */}
          {notes.length > 0 && (
            <div className="space-y-2">
              <label className={label}>
                Grounding Evidence from Project Notes ({selectedNoteIds.length} selected)
              </label>
              <div className="max-h-36 overflow-y-auto border border-slate-200 rounded-xl divide-y divide-slate-100 bg-slate-50/30">
                {notes.map((note) => {
                  const isChecked = selectedNoteIds.includes(note.id);
                  return (
                    <label
                      key={note.id}
                      className="flex items-center gap-3 px-3.5 py-2 hover:bg-slate-50 cursor-pointer text-xs"
                    >
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={() => toggleNote(note.id)}
                        className="rounded border-slate-300 text-blue-600 focus:ring-blue-500"
                      />
                      <BookOpen className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                      <span className="font-medium text-slate-700 truncate">{note.title}</span>
                    </label>
                  );
                })}
              </div>
            </div>
          )}

          {/* Footer Actions */}
          <div className="pt-2 flex items-center justify-end gap-3 border-t border-slate-100">
            <Button variant="secondary" type="button" onClick={onClose} disabled={isGenerating}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" disabled={isGenerating}>
              {isGenerating ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin mr-2" />
                  Synthesizing Design...
                </>
              ) : (
                <>
                  <Sparkles className="w-4 h-4 mr-2" />
                  Generate Architecture Artifact
                </>
              )}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
