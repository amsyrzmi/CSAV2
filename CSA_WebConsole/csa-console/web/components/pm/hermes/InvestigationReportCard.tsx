"use client";

import { useState } from "react";
import {
  Bug,
  CheckCircle2,
  AlertTriangle,
  Clock,
  Terminal,
  FileCode,
  Copy,
  Check,
  Wrench,
  XCircle,
  Loader2,
  ChevronDown,
  ChevronRight,
  ShieldCheck,
} from "lucide-react";
import { post } from "@/lib/api";
import { Banner, Button, Pill } from "@/components/ui/kit";
import type { BugInvestigation } from "../types";

type InvestigationReportCardProps = {
  investigation: BugInvestigation;
  onRefresh?: () => void;
};

export function InvestigationReportCard({
  investigation,
  onRefresh,
}: InvestigationReportCardProps) {
  const [copiedCmd, setCopiedCmd] = useState(false);
  const [draftingPatch, setDraftingPatch] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [patchSuccess, setPatchSuccess] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(true);

  const copyRepro = () => {
    if (!investigation.reproduction_command) return;
    navigator.clipboard.writeText(investigation.reproduction_command);
    setCopiedCmd(true);
    setTimeout(() => setCopiedCmd(false), 2000);
  };

  const handleDraftPatch = async () => {
    if (!investigation.proposed_fix) return;
    setDraftingPatch(true);
    setError(null);
    try {
      const firstTargetFile =
        investigation.evidence_locations[0]?.file_path || "src/main.py";

      await post(
        `/api/admin/pm/projects/${investigation.project_id}/investigations/${investigation.id}/patch`,
        {
          proposed_fix: investigation.proposed_fix,
          target_file: firstTargetFile,
        }
      );
      setPatchSuccess("Patch artifact generated and linked to this investigation!");
      onRefresh?.();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to draft patch artifact";
      setError(msg);
    } finally {
      setDraftingPatch(false);
    }
  };

  const handleCancel = async () => {
    setCancelling(true);
    setError(null);
    try {
      await post(
        `/api/admin/pm/projects/${investigation.project_id}/investigations/${investigation.id}/cancel`,
        {}
      );
      onRefresh?.();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to cancel investigation";
      setError(msg);
    } finally {
      setCancelling(false);
    }
  };

  const getStatusBadge = (status: BugInvestigation["status"]) => {
    switch (status) {
      case "reproduced":
        return <Pill tone="good">Reproduced</Pill>;
      case "suspected":
        return <Pill tone="warn">Suspected</Pill>;
      case "running":
      case "queued":
        return <Pill tone="brand">In Progress</Pill>;
      case "cancelled":
        return <Pill tone="neutral">Cancelled</Pill>;
      case "failed":
      default:
        return <Pill tone="bad">Failed</Pill>;
    }
  };

  const isRunning = investigation.status === "running" || investigation.status === "queued";

  return (
    <div className="border border-slate-200/90 rounded-xl bg-white shadow-sm overflow-hidden mb-4 transition-all">
      {/* Header bar */}
      <div className="px-4 py-3.5 bg-slate-50/80 border-b border-slate-100 flex items-center justify-between gap-3">
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          className="flex items-center gap-2.5 text-left flex-1 min-w-0"
        >
          {expanded ? (
            <ChevronDown className="w-4 h-4 text-slate-400 shrink-0" />
          ) : (
            <ChevronRight className="w-4 h-4 text-slate-400 shrink-0" />
          )}
          <div className="flex items-center gap-2 min-w-0">
            <Bug className="w-4 h-4 text-blue-600 shrink-0" />
            <span className="font-semibold text-slate-800 text-sm truncate">
              {investigation.title}
            </span>
          </div>
        </button>

        <div className="flex items-center gap-2 shrink-0">
          <span className="text-[11px] font-mono text-slate-400 hidden sm:inline">
            {investigation.external_job_id}
          </span>
          {getStatusBadge(investigation.status)}
          {investigation.confidence && (
            <span className="text-[10px] font-medium uppercase px-2 py-0.5 rounded-full bg-slate-100 text-slate-600 border border-slate-200">
              {investigation.confidence} conf
            </span>
          )}
        </div>
      </div>

      {expanded && (
        <div className="p-4 space-y-4">
          {error && <Banner tone="error">{error}</Banner>}
          {patchSuccess && <Banner tone="success">{patchSuccess}</Banner>}

          {/* Running State Banner */}
          {isRunning && (
            <div className="flex items-center justify-between p-3 bg-blue-50/60 border border-blue-100 rounded-lg text-xs text-blue-800">
              <div className="flex items-center gap-2">
                <Loader2 className="w-4 h-4 text-blue-600 animate-spin" />
                <span>Hermes worker is currently investigating code citations and running tests...</span>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={handleCancel}
                disabled={cancelling}
              >
                {cancelling ? "Stopping..." : "Cancel"}
              </Button>
            </div>
          )}

          {/* Symptom */}
          <div>
            <h5 className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1">
              Observed Symptom
            </h5>
            <div className="p-2.5 rounded-lg bg-slate-50 border border-slate-100 text-xs font-mono text-slate-700 whitespace-pre-wrap break-all">
              {investigation.symptom}
            </div>
          </div>

          {/* Reproduction Command */}
          {investigation.reproduction_command && (
            <div>
              <div className="flex items-center justify-between mb-1">
                <h5 className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider flex items-center gap-1.5">
                  <Terminal className="w-3.5 h-3.5 text-slate-600" />
                  Reproduction Command
                </h5>
                <button
                  type="button"
                  onClick={copyRepro}
                  className="text-xs text-blue-600 hover:text-blue-800 flex items-center gap-1 font-medium"
                >
                  {copiedCmd ? (
                    <>
                      <Check className="w-3 h-3 text-emerald-600" />
                      <span>Copied!</span>
                    </>
                  ) : (
                    <>
                      <Copy className="w-3 h-3" />
                      <span>Copy</span>
                    </>
                  )}
                </button>
              </div>
              <div className="p-2.5 bg-slate-900 text-emerald-400 font-mono text-xs rounded-lg overflow-x-auto shadow-inner">
                <code>{investigation.reproduction_command}</code>
              </div>
            </div>
          )}

          {/* Observed vs Expected */}
          {(investigation.observed_result || investigation.expected_result) && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3 text-xs">
              {investigation.observed_result && (
                <div className="p-3 bg-red-50/50 rounded-lg border border-red-100">
                  <div className="font-semibold text-red-800 mb-1 flex items-center gap-1.5">
                    <XCircle className="w-3.5 h-3.5" />
                    Observed Result
                  </div>
                  <p className="text-slate-700">{investigation.observed_result}</p>
                </div>
              )}
              {investigation.expected_result && (
                <div className="p-3 bg-emerald-50/50 rounded-lg border border-emerald-100">
                  <div className="font-semibold text-emerald-800 mb-1 flex items-center gap-1.5">
                    <CheckCircle2 className="w-3.5 h-3.5" />
                    Expected Result
                  </div>
                  <p className="text-slate-700">{investigation.expected_result}</p>
                </div>
              )}
            </div>
          )}

          {/* Evidence Locations */}
          {investigation.evidence_locations && investigation.evidence_locations.length > 0 && (
            <div>
              <h5 className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1.5 flex items-center gap-1.5">
                <FileCode className="w-3.5 h-3.5 text-slate-600" />
                Line-Level Evidence ({investigation.evidence_locations.length})
              </h5>
              <div className="space-y-2">
                {investigation.evidence_locations.map((loc, idx) => (
                  <div
                    key={idx}
                    className="p-2.5 bg-slate-50/90 rounded-lg border border-slate-200/80 text-xs font-mono"
                  >
                    <div className="flex items-center justify-between text-slate-600 font-medium mb-1">
                      <span className="text-blue-700 font-semibold">{loc.file_path}</span>
                      {loc.line_start && (
                        <span className="bg-slate-200/80 text-slate-700 px-1.5 py-0.5 rounded text-[11px]">
                          L{loc.line_start}
                          {loc.line_end ? `-${loc.line_end}` : ""}
                        </span>
                      )}
                    </div>
                    {loc.snippet && (
                      <pre className="p-2 bg-white rounded border border-slate-200 text-slate-800 text-[11px] overflow-x-auto whitespace-pre">
                        {loc.snippet}
                      </pre>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Suspected Causes */}
          {investigation.suspected_causes && investigation.suspected_causes.length > 0 && (
            <div>
              <h5 className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1 flex items-center gap-1.5">
                <AlertTriangle className="w-3.5 h-3.5 text-amber-500" />
                Suspected Causes
              </h5>
              <ul className="list-disc pl-5 space-y-1 text-xs text-slate-700">
                {investigation.suspected_causes.map((cause, idx) => (
                  <li key={idx}>{cause}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Proposed Fix & Patch Artifact */}
          {investigation.proposed_fix && (
            <div className="p-3.5 bg-gradient-to-r from-blue-50/40 to-slate-50 rounded-xl border border-blue-100 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="space-y-1">
                <h5 className="text-xs font-semibold text-slate-800 flex items-center gap-1.5">
                  <Wrench className="w-4 h-4 text-blue-600" />
                  Proposed Fix
                </h5>
                <p className="text-xs text-slate-600">{investigation.proposed_fix}</p>
              </div>

              <div className="shrink-0 flex items-center gap-2">
                {investigation.patch_artifact_id ? (
                  <span className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-50 text-emerald-700 border border-emerald-200 text-xs font-medium">
                    <ShieldCheck className="w-4 h-4" />
                    Patch Drafted
                  </span>
                ) : (
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={handleDraftPatch}
                    disabled={draftingPatch || isRunning}
                  >
                    {draftingPatch ? (
                      <>
                        <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" />
                        Generating Patch...
                      </>
                    ) : (
                      <>
                        <Wrench className="w-3.5 h-3.5 mr-1" />
                        Draft Patch Artifact
                      </>
                    )}
                  </Button>
                )}
              </div>
            </div>
          )}

          {/* Footer Telemetry */}
          {investigation.telemetry && Object.keys(investigation.telemetry).length > 0 && (
            <div className="pt-2 border-t border-slate-100 flex flex-wrap items-center gap-4 text-[11px] text-slate-400">
              {Number(investigation.telemetry.wall_time_sec ?? 0) > 0 && (
                <span className="flex items-center gap-1">
                  <Clock className="w-3 h-3" />
                  {Number(investigation.telemetry.wall_time_sec).toFixed(1)}s wall time
                </span>
              )}
              {Number(investigation.telemetry.tokens_used ?? 0) > 0 && (
                <span>{Number(investigation.telemetry.tokens_used).toLocaleString()} tokens</span>
              )}
              {Number(investigation.telemetry.estimated_cost_usd ?? 0) > 0 && (
                <span>${Number(investigation.telemetry.estimated_cost_usd).toFixed(4)} estimated cost</span>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
