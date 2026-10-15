"use client";

import React, { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  CheckCircle2,
  Code,
  Download,
  FileText,
  History,
  Layers,
  Loader2,
  Save,
  Shield,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { api, post } from "@/lib/api";
import { Banner, Button, Pill } from "@/components/ui/kit";
import {
  ArchitectureGraph,
  ArtifactDetail,
  ArtifactRevisionDetail,
  CatalogItem,
} from "../types";
import { ArchitectureCanvas } from "./ArchitectureCanvas";
import { DiagramGenerateModal } from "./DiagramGenerateModal";
import { MermaidViewer } from "./MermaidViewer";

type Props = {
  projectId: string;
  projectName: string;
};

type WorkspaceProps = {
  projectId: string;
  artifact: ArtifactDetail;
  revisions: ArtifactRevisionDetail[];
  currentRev: ArtifactRevisionDetail;
  selectedRevisionId: string | null;
  onSelectRevision: (id: string) => void;
  onOpenGenerate: () => void;
};

function ArtifactWorkspace({
  projectId,
  artifact,
  revisions,
  currentRev,
  selectedRevisionId,
  onSelectRevision,
  onOpenGenerate,
}: WorkspaceProps) {
  const qc = useQueryClient();
  const [sourceDraft, setSourceDraft] = useState<string>(() => {
    if (currentRev.format === "architecture_json") {
      return JSON.stringify(currentRev.source_json || {}, null, 2);
    }
    return currentRev.source_text || "";
  });
  const [activeTab, setActiveTab] = useState<"diagram" | "assumptions" | "adr">("diagram");
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [exportSuccess, setExportSuccess] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [renderedSvg, setRenderedSvg] = useState<string>("");

  // Save new revision
  const handleSaveRevision = async () => {
    setIsSaving(true);
    setErrorMessage("");
    setSaveSuccess(false);

    try {
      let payload: Record<string, unknown> = {};
      if (currentRev.format === "architecture_json") {
        const parsed = JSON.parse(sourceDraft);
        payload = { source_json: parsed };
      } else {
        payload = { source_text: sourceDraft };
      }

      const newRev = await post<ArtifactRevisionDetail>(
        `/api/admin/pm/projects/${projectId}/artifacts/${artifact.id}/revisions`,
        payload
      );

      qc.invalidateQueries({ queryKey: ["pm-artifact", projectId, artifact.id] });
      qc.invalidateQueries({ queryKey: ["pm-artifacts", projectId] });
      onSelectRevision(newRev.id);
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 2000);
    } catch (err: unknown) {
      const e = err as { detail?: string; message?: string };
      setErrorMessage(e.detail || e.message || "Failed to save revision");
    } finally {
      setIsSaving(false);
    }
  };

  // Export Sanitized SVG
  const handleExportSvg = async () => {
    if (!renderedSvg) return;
    try {
      await post(`/api/admin/pm/projects/${projectId}/artifacts/${artifact.id}/export`, {
        export_format: "svg",
        content_svg: renderedSvg,
      });

      // Download file to client
      const blob = new Blob([renderedSvg], { type: "image/svg+xml;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${artifact.title.replace(/\s+/g, "_").toLowerCase()}_rev${currentRev.revision_number}.svg`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);

      setExportSuccess(true);
      setTimeout(() => setExportSuccess(false), 2000);
    } catch (err: unknown) {
      const e = err as { detail?: string; message?: string };
      setErrorMessage(e.detail || e.message || "Failed to export SVG");
    }
  };

  return (
    <>
      {/* Top Workspace Header */}
      <div className="h-14 border-b border-slate-100 px-5 flex items-center justify-between bg-white shrink-0">
        <div className="flex items-center gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="font-semibold text-sm text-slate-900 truncate max-w-sm">
                {artifact.title}
              </h2>
              <Pill tone={artifact.kind === "architecture" ? "brand" : "neutral"}>
                {artifact.kind}
              </Pill>
              <Pill tone={currentRev.validation_status === "valid" ? "good" : "bad"}>
                {currentRev.validation_status}
              </Pill>
            </div>
            <p className="text-[11px] text-slate-400 font-mono">
              Hash: {currentRev.content_hash?.slice(0, 12)}...
            </p>
          </div>

          {/* Revision Selector */}
          {revisions.length > 1 && (
            <div className="flex items-center gap-1.5 ml-3 pl-3 border-l border-slate-100 text-xs">
              <History className="w-3.5 h-3.5 text-slate-400" />
              <select
                value={selectedRevisionId || currentRev.id}
                onChange={(e) => onSelectRevision(e.target.value)}
                className="text-xs bg-slate-50 border border-slate-200 rounded-lg px-2 py-1 text-slate-700 font-medium"
              >
                {revisions.map((r) => (
                  <option key={r.id} value={r.id}>
                    Rev {r.revision_number} {r.id === artifact.current_revision_id ? "(Current)" : ""}
                  </option>
                ))}
              </select>
            </div>
          )}
        </div>

        {/* Action Buttons */}
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={onOpenGenerate}
            className="gap-1.5 text-xs text-blue-600 hover:text-blue-700"
          >
            <Sparkles className="w-3.5 h-3.5 text-blue-600" />
            AI Assist
          </Button>

          {artifact.kind === "diagram" && (
            <Button
              size="sm"
              variant="secondary"
              onClick={handleExportSvg}
              disabled={!renderedSvg}
              className="gap-1.5 text-xs"
              title="Export Sanitized SVG"
            >
              {exportSuccess ? <Check className="w-3.5 h-3.5 text-emerald-600" /> : <Download className="w-3.5 h-3.5" />}
              Export SVG
            </Button>
          )}

          <Button
            size="sm"
            variant="primary"
            onClick={handleSaveRevision}
            disabled={isSaving}
            className="gap-1.5 text-xs"
          >
            {isSaving ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : saveSuccess ? (
              <Check className="w-3.5 h-3.5" />
            ) : (
              <Save className="w-3.5 h-3.5" />
            )}
            Save Rev {revisions.length + 1}
          </Button>
        </div>
      </div>

      {/* Error Notification */}
      {errorMessage && (
        <div className="px-5 pt-3">
          <Banner tone="error" onClose={() => setErrorMessage("")}>
            <div>{errorMessage}</div>
          </Banner>
        </div>
      )}

      {/* Navigation Tabs (Diagram vs Assumptions vs ADR) */}
      <div className="px-5 pt-2 border-b border-slate-100 flex items-center gap-6 text-xs bg-slate-50/30">
        <button
          onClick={() => setActiveTab("diagram")}
          className={`pb-2.5 font-medium transition-colors border-b-2 flex items-center gap-1.5 ${
            activeTab === "diagram"
              ? "border-blue-600 text-blue-600 font-semibold"
              : "border-transparent text-slate-500 hover:text-slate-700"
          }`}
        >
          <Code className="w-3.5 h-3.5" />
          {artifact.kind === "architecture" ? "Interactive Architecture Canvas" : "Diagram Source & Preview"}
        </button>

        <button
          onClick={() => setActiveTab("assumptions")}
          className={`pb-2.5 font-medium transition-colors border-b-2 flex items-center gap-1.5 ${
            activeTab === "assumptions"
              ? "border-blue-600 text-blue-600 font-semibold"
              : "border-transparent text-slate-500 hover:text-slate-700"
          }`}
        >
          <Shield className="w-3.5 h-3.5" />
          Assumptions, Boundaries &amp; Failures ({(currentRev.assumptions || []).length})
        </button>

        {currentRev.adr_content && (
          <button
            onClick={() => setActiveTab("adr")}
            className={`pb-2.5 font-medium transition-colors border-b-2 flex items-center gap-1.5 ${
              activeTab === "adr"
                ? "border-blue-600 text-blue-600 font-semibold"
                : "border-transparent text-slate-500 hover:text-slate-700"
            }`}
          >
            <FileText className="w-3.5 h-3.5" />
            Architectural Decision Record (ADR)
          </button>
        )}
      </div>

      {/* Studio Body */}
      <div className="flex-1 overflow-hidden">
        {activeTab === "diagram" ? (
          artifact.kind === "architecture" && currentRev.source_json ? (
            <ArchitectureCanvas graph={currentRev.source_json} />
          ) : (
            <div className="flex h-full divide-x divide-slate-100">
              {/* Left: Code Source Editor */}
              <div className="w-1/2 flex flex-col bg-slate-50/20">
                <div className="px-4 py-2 border-b border-slate-100 bg-slate-50/80 text-[11px] font-mono text-slate-500 flex items-center justify-between">
                  <span>Mermaid Syntax Editor</span>
                  <span>{sourceDraft.split("\n").length} lines</span>
                </div>
                <textarea
                  value={sourceDraft}
                  onChange={(e) => setSourceDraft(e.target.value)}
                  className="flex-1 p-4 font-mono text-xs text-slate-800 bg-transparent resize-none focus:outline-hidden leading-relaxed"
                  placeholder="Enter Mermaid diagram syntax..."
                  spellCheck={false}
                />
              </div>

              {/* Right: Live Visual Preview */}
              <div className="w-1/2 flex flex-col bg-white">
                <div className="px-4 py-2 border-b border-slate-100 bg-slate-50/80 text-[11px] text-slate-500 flex items-center justify-between">
                  <span>Live Visual Preview</span>
                  <Pill tone="good">SVG Safe</Pill>
                </div>
                <div className="flex-1 overflow-auto">
                  <MermaidViewer source={sourceDraft} onSvgRendered={setRenderedSvg} />
                </div>
              </div>
            </div>
          )
        ) : activeTab === "assumptions" ? (
          <div className="p-6 overflow-y-auto space-y-6 max-w-4xl text-xs">
            {/* Assumptions */}
            <div className="space-y-2">
              <h4 className="font-semibold text-slate-800 flex items-center gap-1.5">
                <CheckCircle2 className="w-4 h-4 text-emerald-600" />
                Architectural Assumptions
              </h4>
              <ul className="list-disc list-inside space-y-1 text-slate-600 bg-slate-50 p-4 rounded-xl border border-slate-100">
                {(currentRev.assumptions || []).map((a, i) => (
                  <li key={i}>{a}</li>
                ))}
                {(currentRev.assumptions || []).length === 0 && (
                  <li className="italic text-slate-400">No explicit assumptions captured.</li>
                )}
              </ul>
            </div>

            {/* Trust Boundaries */}
            <div className="space-y-2">
              <h4 className="font-semibold text-slate-800 flex items-center gap-1.5">
                <Shield className="w-4 h-4 text-rose-600" />
                Trust Boundaries &amp; Encryption
              </h4>
              <ul className="list-disc list-inside space-y-1 text-slate-600 bg-slate-50 p-4 rounded-xl border border-slate-100">
                {(currentRev.trust_boundaries || []).map((t, i) => (
                  <li key={i}>{t}</li>
                ))}
                {(currentRev.trust_boundaries || []).length === 0 && (
                  <li className="italic text-slate-400">No external trust crossings defined.</li>
                )}
              </ul>
            </div>

            {/* Failure Modes & Recovery */}
            <div className="space-y-2">
              <h4 className="font-semibold text-slate-800 flex items-center gap-1.5">
                <ShieldAlert className="w-4 h-4 text-amber-600" />
                Failure Modes &amp; Recovery Choices
              </h4>
              <ul className="list-disc list-inside space-y-1 text-slate-600 bg-slate-50 p-4 rounded-xl border border-slate-100">
                {(currentRev.failure_modes || []).map((f, i) => (
                  <li key={i}>{f}</li>
                ))}
                {(currentRev.failure_modes || []).length === 0 && (
                  <li className="italic text-slate-400">No failure modes specified.</li>
                )}
              </ul>
            </div>
          </div>
        ) : (
          /* ADR Tab */
          <div className="p-6 overflow-y-auto max-w-3xl">
            <div className="prose prose-slate max-w-none text-xs bg-slate-50/50 p-6 rounded-2xl border border-slate-100 font-mono whitespace-pre-wrap leading-relaxed">
              {currentRev.adr_content}
            </div>
          </div>
        )}
      </div>
    </>
  );
}

export function DiagramStudio({ projectId, projectName }: Props) {
  const qc = useQueryClient();
  const [selectedArtifactId, setSelectedArtifactId] = useState<string | null>(null);
  const [selectedRevisionId, setSelectedRevisionId] = useState<string | null>(null);
  const [errorMessage, setErrorMessage] = useState("");
  const [isGenerateOpen, setIsGenerateOpen] = useState(false);

  // 1. Fetch catalog items for quick template creation
  const { data: catalog } = useQuery<CatalogItem[]>({
    queryKey: ["pm-diagrams-catalog"],
    queryFn: () => api<CatalogItem[]>("/api/admin/pm/diagrams/catalog"),
  });

  // 2. Fetch project artifacts
  const { data: artifactsData, isLoading: isLoadingArtifacts } = useQuery<{ artifacts: ArtifactDetail[] }>({
    queryKey: ["pm-artifacts", projectId],
    queryFn: () => api<{ artifacts: ArtifactDetail[] }>(`/api/admin/pm/projects/${projectId}/artifacts`),
  });

  const artifacts = artifactsData?.artifacts || [];

  // Set initial selected artifact if none selected
  const activeArtifact = artifacts.find((a) => a.id === selectedArtifactId) || artifacts[0] || null;

  // 3. Fetch detailed artifact with all historical revisions
  const { data: artifactDetail } = useQuery<ArtifactDetail>({
    queryKey: ["pm-artifact", projectId, activeArtifact?.id],
    queryFn: () => api<ArtifactDetail>(`/api/admin/pm/projects/${projectId}/artifacts/${activeArtifact?.id}`),
    enabled: !!activeArtifact?.id,
  });

  // Active revision resolution
  const revisions = artifactDetail?.revisions || [];
  const currentRev =
    revisions.find((r) => r.id === selectedRevisionId) ||
    artifactDetail?.current_revision ||
    revisions[0] ||
    null;

  // Handle Quick Template Creation
  const handleCreateFromTemplate = async (catItem: CatalogItem) => {
    setErrorMessage("");
    try {
      let srcJson: ArchitectureGraph | null = null;
      let srcText: string | null = null;

      if (catItem.engine === "architecture_json") {
        srcJson = JSON.parse(catItem.example_fixture);
      } else {
        srcText = catItem.example_fixture;
      }

      const res = await post<ArtifactDetail>(`/api/admin/pm/projects/${projectId}/artifacts`, {
        kind: catItem.engine === "architecture_json" ? "architecture" : "diagram",
        title: `${projectName} ${catItem.label}`,
        description: catItem.description,
        format: catItem.engine,
        diagram_type: catItem.diagram_type,
        source_text: srcText,
        source_json: srcJson,
        assumptions: ["Initial baseline architecture design"],
        evidence: ["Project creation template"],
      });

      qc.invalidateQueries({ queryKey: ["pm-artifacts", projectId] });
      setSelectedArtifactId(res.id);
      setSelectedRevisionId(res.current_revision_id || null);
    } catch (err: unknown) {
      const e = err as { detail?: string; message?: string };
      setErrorMessage(e.detail || e.message || "Failed to create artifact from template");
    }
  };

  return (
    <div className="flex flex-col gap-3">
      {errorMessage && (
        <Banner tone="error" onClose={() => setErrorMessage("")}>
          <div>{errorMessage}</div>
        </Banner>
      )}
      <div className="flex h-[calc(100vh-14rem)] min-h-[580px] w-full border border-slate-200 rounded-2xl bg-white shadow-xs overflow-hidden animate-fade-in">
        {/* Left Sidebar: Artifacts Tree & Templates */}
        <div className="w-64 border-r border-slate-100 flex flex-col bg-slate-50/50">
        <div className="p-3.5 border-b border-slate-100 flex items-center justify-between">
          <div className="flex items-center gap-1.5">
            <Layers className="w-4 h-4 text-blue-600" />
            <h3 className="text-xs font-semibold text-slate-800">Designs &amp; Models</h3>
          </div>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setIsGenerateOpen(true)}
            className="text-blue-600 hover:bg-blue-50 h-7 px-2 text-xs"
            title="Generate with AI"
          >
            <Sparkles className="w-3.5 h-3.5" />
          </Button>
        </div>

        {/* Artifacts List */}
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {isLoadingArtifacts ? (
            <div className="p-4 flex items-center justify-center text-xs text-slate-400 gap-1.5">
              <Loader2 className="w-3.5 h-3.5 animate-spin text-blue-500" />
              Loading artifacts...
            </div>
          ) : artifacts.length === 0 ? (
            <div className="p-4 text-center text-xs text-slate-400 italic">
              No diagrams yet. Choose a template below or generate with AI.
            </div>
          ) : (
            artifacts.map((art) => {
              const isSelected = (activeArtifact?.id || "") === art.id;
              return (
                <button
                  key={art.id}
                  onClick={() => {
                    setSelectedArtifactId(art.id);
                    setSelectedRevisionId(null);
                  }}
                  className={`w-full text-left p-2.5 rounded-xl transition-all flex flex-col gap-1 ${
                    isSelected
                      ? "bg-white shadow-xs border border-slate-200 text-slate-900"
                      : "text-slate-600 hover:bg-slate-100/60"
                  }`}
                >
                  <div className="flex items-center justify-between gap-1.5">
                    <span className="font-semibold text-xs truncate">{art.title}</span>
                    <Pill tone={art.kind === "architecture" ? "brand" : "neutral"}>
                      {art.kind === "architecture" ? "AWS Cloud" : "Diagram"}
                    </Pill>
                  </div>
                  <div className="flex items-center gap-2 text-[10px] text-slate-400">
                    <span>Rev {art.current_revision?.revision_number || 1}</span>
                    <span>•</span>
                    <span className="capitalize">{art.current_revision?.diagram_type || "Model"}</span>
                  </div>
                </button>
              );
            })
          )}
        </div>

        {/* Quick Catalog Starter Templates */}
        <div className="p-3 border-t border-slate-100 bg-white">
          <label className="text-[10px] font-semibold uppercase tracking-wider text-slate-400 block mb-2">
            Add Starter Template
          </label>
          <div className="grid grid-cols-2 gap-1.5">
            {(catalog || []).slice(0, 4).map((c) => (
              <button
                key={c.id}
                onClick={() => handleCreateFromTemplate(c)}
                className="text-left p-1.5 rounded-lg border border-slate-100 bg-slate-50/50 hover:bg-blue-50/50 hover:border-blue-200 transition-colors text-[11px] font-medium text-slate-700 truncate"
              >
                + {c.label.split(" ")[0]}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Main Studio Editor & Preview Area */}
      <div className="flex-1 flex flex-col bg-white overflow-hidden">
        {activeArtifact && currentRev ? (
          <ArtifactWorkspace
            key={`${activeArtifact.id}-${currentRev.id}`}
            projectId={projectId}
            artifact={artifactDetail || activeArtifact}
            revisions={revisions}
            currentRev={currentRev}
            selectedRevisionId={selectedRevisionId}
            onSelectRevision={(id) => setSelectedRevisionId(id)}
            onOpenGenerate={() => setIsGenerateOpen(true)}
          />
        ) : (
          /* Empty State */
          <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-slate-400">
            <div className="w-12 h-12 rounded-2xl bg-slate-100 flex items-center justify-center text-slate-400 mb-3">
              <Layers className="w-6 h-6" />
            </div>
            <h3 className="font-semibold text-slate-700 text-sm mb-1">Diagram &amp; Architecture Studio</h3>
            <p className="text-xs text-slate-500 max-w-sm mb-5 leading-relaxed">
              Design and version sequence diagrams, data ERDs, flowcharts, or AWS cloud topologies.
            </p>
            <div className="flex items-center gap-3">
              <Button
                variant="primary"
                onClick={() => setIsGenerateOpen(true)}
                className="gap-2 text-xs"
              >
                <Sparkles className="w-4 h-4" />
                Generate Design with AI
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* AI Generator Modal */}
      <DiagramGenerateModal
        projectId={projectId}
        projectName={projectName}
        isOpen={isGenerateOpen}
        onClose={() => setIsGenerateOpen(false)}
        onArtifactGenerated={(art) => {
          qc.invalidateQueries({ queryKey: ["pm-artifacts", projectId] });
          setSelectedArtifactId(art.id);
          setSelectedRevisionId(art.current_revision_id || null);
        }}
      />
    </div>
    </div>
  );
}
