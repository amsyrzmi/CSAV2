"use client";

import React, { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Pause,
  Play,
  RotateCcw,
  XCircle,
  MessageSquarePlus,
} from "lucide-react";
import { post } from "@/lib/api";
import { Button, Modal } from "@/components/ui/kit";
import { WorkroomRun } from "../types";

interface InterventionBarProps {
  projectId: string;
  run: WorkroomRun;
}

export function InterventionBar({ projectId, run }: InterventionBarProps) {
  const qc = useQueryClient();
  const [guidanceModalOpen, setGuidanceModalOpen] = useState(false);
  const [revisionModalOpen, setRevisionModalOpen] = useState(false);
  const [guidanceText, setGuidanceText] = useState("");
  const [revisionFeedback, setRevisionFeedback] = useState("");

  const interveneMutation = useMutation({
    mutationFn: (payload: { action: string; guidance_text?: string; feedback?: string }) =>
      post(`/api/admin/pm/projects/${projectId}/workroom/runs/${run.id}/intervene`, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pm-workroom-run", projectId, run.id] });
      qc.invalidateQueries({ queryKey: ["pm-workroom-runs", projectId] });
      qc.invalidateQueries({ queryKey: ["pm-workroom-messages", projectId, run.id] });
      setGuidanceModalOpen(false);
      setRevisionModalOpen(false);
    },
  });

  const handlePause = () => {
    interveneMutation.mutate({ action: "pause" });
  };

  const handleResume = () => {
    interveneMutation.mutate({ action: "resume" });
  };

  const handleCancel = () => {
    if (confirm("Are you sure you want to cancel this multi-agent workflow?")) {
      interveneMutation.mutate({ action: "cancel" });
    }
  };

  const handleSendGuidance = () => {
    if (!guidanceText.trim()) return;
    interveneMutation.mutate({
      action: "guidance",
      guidance_text: guidanceText.trim(),
    });
  };

  const handleRequestRevision = () => {
    if (!revisionFeedback.trim()) return;
    interveneMutation.mutate({
      action: "request_revision",
      feedback: revisionFeedback.trim(),
    });
  };

  const isTerminal = ["succeeded", "failed", "budget_exceeded", "cancelled"].includes(run.status);
  const isPaused = run.status === "paused";
  const canRevise = run.repair_cycles < run.max_repair_cycles && !isTerminal;

  return (
    <div className="flex flex-wrap items-center justify-between gap-2.5 p-3 rounded-xl border border-ink-200 bg-white shadow-xs">
      <div className="flex items-center gap-2">
        <span className="text-xs font-semibold text-ink-700">
          Operator Controls:
        </span>
        <span className="text-xs font-mono px-2 py-0.5 rounded-md bg-ink-100 text-ink-800 capitalize">
          Status: {run.status.replace("_", " ")}
        </span>
      </div>

      <div className="flex items-center gap-1.5">
        {!isTerminal && (
          <>
            {isPaused ? (
              <Button
                size="sm"
                variant="primary"
                onClick={handleResume}
                disabled={interveneMutation.isPending}
                className="h-7 text-xs bg-emerald-600 hover:bg-emerald-700"
              >
                <Play size={12} className="mr-1 fill-current" /> Resume Run
              </Button>
            ) : (
              <Button
                size="sm"
                variant="secondary"
                onClick={handlePause}
                disabled={interveneMutation.isPending}
                className="h-7 text-xs"
              >
                <Pause size={12} className="mr-1" /> Pause Boundary
              </Button>
            )}

            <Button
              size="sm"
              variant="secondary"
              onClick={() => setGuidanceModalOpen(true)}
              disabled={interveneMutation.isPending}
              className="h-7 text-xs"
            >
              <MessageSquarePlus size={12} className="mr-1" /> Inject Guidance
            </Button>

            {canRevise && (
              <Button
                size="sm"
                variant="secondary"
                onClick={() => setRevisionModalOpen(true)}
                disabled={interveneMutation.isPending}
                className="h-7 text-xs text-amber-700 hover:text-amber-800"
              >
                <RotateCcw size={12} className="mr-1" /> Request Revision
              </Button>
            )}

            <Button
              size="sm"
              variant="secondary"
              onClick={handleCancel}
              disabled={interveneMutation.isPending}
              className="h-7 text-xs text-rose-600 hover:text-rose-700 hover:bg-rose-50"
            >
              <XCircle size={12} className="mr-1" /> Cancel
            </Button>
          </>
        )}
      </div>

      {/* Guidance Modal */}
      {guidanceModalOpen && (
        <Modal
          isOpen={guidanceModalOpen}
          onClose={() => setGuidanceModalOpen(false)}
          title="Inject Scoped Operator Guidance"
        >
          <div className="space-y-3.5">
            <p className="text-xs text-ink-600">
              Provide specific domain advice, constraints, or answers to questions asked by the working agents.
            </p>
            <textarea
              value={guidanceText}
              onChange={(e) => setGuidanceText(e.target.value)}
              rows={4}
              placeholder="e.g. Ensure the fix also checks for None and empty string values..."
              className="w-full text-xs p-2.5 rounded-lg border border-ink-200 focus:border-brand-500 focus:outline-none"
            />
            <div className="flex justify-end gap-2 pt-2 border-t border-ink-100">
              <Button variant="secondary" size="sm" onClick={() => setGuidanceModalOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={handleSendGuidance}
                disabled={!guidanceText.trim() || interveneMutation.isPending}
              >
                {interveneMutation.isPending ? "Sending…" : "Send to Workroom"}
              </Button>
            </div>
          </div>
        </Modal>
      )}

      {/* Request Revision Modal */}
      {revisionModalOpen && (
        <Modal
          isOpen={revisionModalOpen}
          onClose={() => setRevisionModalOpen(false)}
          title="Request Implementation Revision"
        >
          <div className="space-y-3.5">
            <p className="text-xs text-ink-600">
              Trigger a repair cycle back to the Implementer with explicit feedback. Current repair count: {run.repair_cycles}/{run.max_repair_cycles}.
            </p>
            <textarea
              value={revisionFeedback}
              onChange={(e) => setRevisionFeedback(e.target.value)}
              rows={4}
              placeholder="e.g. The proposed fix does not satisfy acceptance criterion #2. Please handle boundary check..."
              className="w-full text-xs p-2.5 rounded-lg border border-ink-200 focus:border-brand-500 focus:outline-none"
            />
            <div className="flex justify-end gap-2 pt-2 border-t border-ink-100">
              <Button variant="secondary" size="sm" onClick={() => setRevisionModalOpen(false)}>
                Cancel
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={handleRequestRevision}
                disabled={!revisionFeedback.trim() || interveneMutation.isPending}
                className="bg-amber-600 hover:bg-amber-700"
              >
                {interveneMutation.isPending ? "Submitting…" : "Request Revision"}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
