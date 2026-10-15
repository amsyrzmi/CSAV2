"use client";

import { useState } from "react";
import { Bug, Sparkles, AlertCircle, Loader2 } from "lucide-react";
import { post } from "@/lib/api";
import { Banner, Button } from "@/components/ui/kit";
import { input, label, textarea } from "@/components/ui/input";
import type { BugInvestigation } from "../types";

type InvestigateModalProps = {
  projectId: string;
  taskId?: string | null;
  defaultTitle?: string;
  defaultSymptom?: string;
  isOpen: boolean;
  onClose: () => void;
  onInvestigationCreated: (inv: BugInvestigation) => void;
};

export function InvestigateModal({
  projectId,
  taskId,
  defaultTitle = "Bug Investigation",
  defaultSymptom = "",
  isOpen,
  onClose,
  onInvestigationCreated,
}: InvestigateModalProps) {
  const [title, setTitle] = useState(defaultTitle);
  const [symptom, setSymptom] = useState(defaultSymptom);
  const [suspectedCausesStr, setSuspectedCausesStr] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!symptom.trim()) {
      setError("Please describe the symptom, error message, or failing test.");
      return;
    }

    setLoading(true);
    setError("");

    try {
      const suspectedCauses = suspectedCausesStr
        .split("\n")
        .map((s) => s.trim())
        .filter(Boolean);

      const res = await post<BugInvestigation>(
        `/api/admin/pm/projects/${projectId}/investigations`,
        {
          title: title.trim() || "Bug Investigation",
          task_id: taskId || null,
          symptom: symptom.trim(),
          suspected_causes: suspectedCauses,
        }
      );

      onInvestigationCreated(res);
      onClose();
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to launch investigation";
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4 animate-in fade-in duration-200"
    >
      <div className="bg-white rounded-xl shadow-2xl border border-slate-200 w-full max-w-lg overflow-hidden flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-100 bg-gradient-to-r from-blue-50/60 to-indigo-50/30">
          <div className="flex items-center gap-2.5">
            <div className="w-9 h-9 rounded-lg bg-blue-600 text-white flex items-center justify-center shadow-sm shadow-blue-500/20">
              <Bug className="w-5 h-5" />
            </div>
            <div>
              <h3 className="font-semibold text-slate-900 text-base flex items-center gap-1.5">
                Investigate with Hermes
                <span className="text-[10px] font-medium bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded">
                  Autonomous
                </span>
              </h3>
              <p className="text-xs text-slate-500">
                Run an isolated bug reproduction and evidence collection worker
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            type="button"
            className="text-slate-400 hover:text-slate-600 p-1.5 rounded-lg hover:bg-slate-100 transition-colors"
          >
            ✕
          </button>
        </div>

        {/* Form Body */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {error && <Banner tone="error">{error}</Banner>}

          <div>
            <label className={label}>Investigation Title</label>
            <input
              type="text"
              className={input}
              placeholder="e.g. UniqueViolation on concurrent slug creation"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              required
            />
          </div>

          <div>
            <label className={label}>
              Symptom / Observed Error <span className="text-red-500">*</span>
            </label>
            <textarea
              className={`${textarea} min-h-[110px] font-mono text-xs`}
              placeholder="Paste stack trace, failing test output, or exact reproduction steps..."
              value={symptom}
              onChange={(e) => setSymptom(e.target.value)}
              required
            />
            <span className="text-[11px] text-slate-500 mt-1 block">
              Hermes will analyze code citations, construct a reproduction command, and verify hypotheses.
            </span>
          </div>

          <div>
            <label className={label}>Suspected Causes (Optional)</label>
            <textarea
              className={`${textarea} min-h-[70px] text-xs`}
              placeholder="Enter one suspected cause per line..."
              value={suspectedCausesStr}
              onChange={(e) => setSuspectedCausesStr(e.target.value)}
            />
          </div>

          <div className="p-3 bg-slate-50 rounded-lg border border-slate-200/80 flex items-start gap-2.5 text-xs text-slate-600">
            <AlertCircle className="w-4 h-4 text-blue-500 shrink-0 mt-0.5" />
            <p>
              Executes in an isolated project sandbox. Sensitive database credentials and application keys are excluded from the runner runtime.
            </p>
          </div>

          <div className="flex items-center justify-end gap-3 pt-3 border-t border-slate-100">
            <Button variant="ghost" onClick={onClose} type="button" disabled={loading}>
              Cancel
            </Button>
            <Button variant="primary" type="submit" disabled={loading}>
              {loading ? (
                <>
                  <Loader2 className="w-4 h-4 mr-1.5 animate-spin" />
                  Dispatching Hermes...
                </>
              ) : (
                <>
                  <Sparkles className="w-4 h-4 mr-1.5" />
                  Launch Investigation
                </>
              )}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
