"use client";

import React, { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, GitMerge, Check } from "lucide-react";
import { post } from "@/lib/api";
import { Modal, Button } from "@/components/ui/kit";
import { ConflictCheckResult } from "../types";

interface ConflictReviewModalProps {
  projectId: string;
  isOpen: boolean;
  onClose: () => void;
}

export function ConflictReviewModal({ projectId, isOpen, onClose }: ConflictReviewModalProps) {
  const [filePath, setFilePath] = useState("src/services/billing.py");
  const [diffA, setDiffA] = useState("+    rate = 0.05  # Task A calculation\n+    tax = amount * rate\n");
  const [diffB, setDiffB] = useState("+    rate = 0.10  # Task B calculation\n+    tax = amount * rate * 1.1\n");
  const [result, setResult] = useState<ConflictCheckResult | null>(null);

  const checkMutation = useMutation({
    mutationFn: (payload: { base_file_path: string; branch_a_diff: string; branch_b_diff: string }) =>
      post<ConflictCheckResult>(`/api/admin/pm/projects/${projectId}/workroom/conflict-check`, payload),
    onSuccess: (data) => {
      setResult(data);
    },
  });

  const handleSimulate = (e: React.FormEvent) => {
    e.preventDefault();
    checkMutation.mutate({
      base_file_path: filePath.trim(),
      branch_a_diff: diffA,
      branch_b_diff: diffB,
    });
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title="Concurrent Task Integration & Conflict Review">
      <div className="space-y-4">
        <p className="text-xs text-ink-600">
          Verify whether parallel task branches editing the same repository file can be cleanly integrated or if an explicit conflict review artifact must be resolved.
        </p>

        <form onSubmit={handleSimulate} className="space-y-3">
          <div>
            <label className="text-xs font-semibold text-ink-800 block mb-1">Target Repository File</label>
            <input
              type="text"
              value={filePath}
              onChange={(e) => setFilePath(e.target.value)}
              required
              className="w-full text-xs px-2.5 py-2 rounded-lg border border-ink-200 focus:border-brand-500 font-mono"
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="text-xs font-semibold text-ink-700 block mb-1">Task A Branch Diff</label>
              <textarea
                value={diffA}
                onChange={(e) => setDiffA(e.target.value)}
                rows={4}
                className="w-full font-mono text-[11px] p-2 rounded-lg border border-ink-200 focus:border-brand-500"
              />
            </div>
            <div>
              <label className="text-xs font-semibold text-ink-700 block mb-1">Task B Branch Diff</label>
              <textarea
                value={diffB}
                onChange={(e) => setDiffB(e.target.value)}
                rows={4}
                className="w-full font-mono text-[11px] p-2 rounded-lg border border-ink-200 focus:border-brand-500"
              />
            </div>
          </div>

          <div className="flex justify-end">
            <Button type="submit" variant="primary" size="sm" disabled={checkMutation.isPending}>
              <GitMerge size={13} className="mr-1" />
              {checkMutation.isPending ? "Checking Collision…" : "Simulate Integration Check"}
            </Button>
          </div>
        </form>

        {result && (
          <div
            className={`p-3.5 rounded-xl border text-xs space-y-2 ${
              result.has_conflict ? "bg-amber-50/60 border-amber-200 text-amber-900" : "bg-emerald-50/60 border-emerald-200 text-emerald-900"
            }`}
          >
            <div className="flex items-center gap-2 font-semibold">
              {result.has_conflict ? (
                <>
                  <AlertTriangle size={15} className="text-amber-600" />
                  <span>Merge Conflict Detected: Review Artifact Created</span>
                </>
              ) : (
                <>
                  <Check size={15} className="text-emerald-600" />
                  <span>Clean Integration: Zero Collisions</span>
                </>
              )}
            </div>

            <p className="text-[11.5px] leading-relaxed">{result.summary}</p>

            {result.conflict_artifact_id && (
              <div className="pt-2 border-t border-amber-200/60 flex items-center justify-between text-[11px] font-mono">
                <span className="text-amber-800">Conflict Artifact ID:</span>
                <span className="bg-white/80 px-2 py-0.5 rounded border border-amber-200 text-amber-900">
                  {result.conflict_artifact_id}
                </span>
              </div>
            )}
          </div>
        )}

        <div className="flex justify-end pt-2 border-t border-ink-100">
          <Button variant="secondary" size="sm" onClick={onClose}>
            Close
          </Button>
        </div>
      </div>
    </Modal>
  );
}
