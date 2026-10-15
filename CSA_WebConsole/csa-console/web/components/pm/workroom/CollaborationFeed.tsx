"use client";

import React, { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  MessageSquare,
  Send,
  Target,
  HelpCircle,
  FileCode,
  ArrowRightCircle,
  ShieldCheck,
  Flag,
  FileCheck2,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { api, post } from "@/lib/api";
import { Card, Button } from "@/components/ui/kit";
import { WorkroomMessage, WorkroomMessageType, WorkroomRole } from "../types";

const MESSAGE_TYPE_CONFIG: Record<
  WorkroomMessageType,
  { label: string; bg: string; text: string; border: string; icon: React.ComponentType<{ size?: number; className?: string }> }
> = {
  proposal: { label: "Proposal", bg: "bg-amber-50", text: "text-amber-800", border: "border-amber-200", icon: Target },
  question: { label: "Question / Guidance", bg: "bg-sky-50", text: "text-sky-800", border: "border-sky-200", icon: HelpCircle },
  evidence: { label: "Evidence", bg: "bg-purple-50", text: "text-purple-800", border: "border-purple-200", icon: FileCode },
  handoff: { label: "Handoff", bg: "bg-indigo-50", text: "text-indigo-800", border: "border-indigo-200", icon: ArrowRightCircle },
  review: { label: "Review Feedback", bg: "bg-rose-50", text: "text-rose-800", border: "border-rose-200", icon: ShieldCheck },
  final: { label: "Final Result", bg: "bg-teal-50", text: "text-teal-800", border: "border-teal-200", icon: Flag },
};

interface CollaborationFeedProps {
  projectId: string;
  runId?: string | null;
}

export function CollaborationFeed({ projectId, runId }: CollaborationFeedProps) {
  const qc = useQueryClient();
  const [filterType, setFilterType] = useState<string>("all");
  const [operatorInput, setOperatorInput] = useState("");
  const [expandedMessageIds, setExpandedMessageIds] = useState<Set<string>>(new Set());

  const url = runId
    ? `/api/admin/pm/projects/${projectId}/workroom/messages?run_id=${runId}&limit=100`
    : `/api/admin/pm/projects/${projectId}/workroom/messages?limit=100`;

  const { data: messages = [], isLoading } = useQuery({
    queryKey: ["pm-workroom-messages", projectId, runId],
    queryFn: () => api<WorkroomMessage[]>(url),
    refetchInterval: 3000,
  });

  const postMutation = useMutation({
    mutationFn: (payload: { author_role: WorkroomRole; message_type: WorkroomMessageType; summary: string; body_markdown: string }) =>
      post(`/api/admin/pm/projects/${projectId}/workroom/messages?run_id=${runId}`, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pm-workroom-messages", projectId, runId] });
      setOperatorInput("");
    },
  });

  const handleSendOperatorMessage = (e: React.FormEvent) => {
    e.preventDefault();
    if (!operatorInput.trim() || !runId) return;

    postMutation.mutate({
      author_role: "human_operator",
      message_type: "question",
      summary: operatorInput.trim().slice(0, 80),
      body_markdown: operatorInput.trim(),
    });
  };

  const toggleExpand = (id: string) => {
    setExpandedMessageIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const filtered = messages.filter((m) => filterType === "all" || m.message_type === filterType);

  return (
    <Card className="flex flex-col h-[650px] p-0 overflow-hidden border border-ink-200 bg-white">
      {/* Header with Type Filters */}
      <div className="p-3.5 border-b border-ink-100 bg-ink-50/50 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <MessageSquare size={16} className="text-brand-600" />
          <h3 className="text-xs font-semibold text-ink-900">
            Workroom Collaboration Stream
          </h3>
          <span className="text-[10px] font-mono text-ink-400">
            ({messages.length} events)
          </span>
        </div>

        <div className="flex items-center gap-1 overflow-x-auto">
          {["all", "proposal", "evidence", "handoff", "review", "question"].map((t) => (
            <button
              key={t}
              onClick={() => setFilterType(t)}
              className={`text-[10.5px] px-2 py-0.5 rounded-full capitalize font-medium transition ${
                filterType === t
                  ? "bg-brand-600 text-white shadow-xs"
                  : "bg-ink-100 text-ink-600 hover:bg-ink-200"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      {/* Message List */}
      <div className="flex-1 overflow-y-auto p-3.5 space-y-3">
        {isLoading ? (
          <div className="space-y-3 py-6">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-20 rounded-xl bg-ink-50 animate-pulse border border-ink-100" />
            ))}
          </div>
        ) : filtered.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center py-12 text-ink-400">
            <MessageSquare size={32} className="mb-2 text-ink-300 stroke-[1.5]" />
            <p className="text-xs font-medium text-ink-600">No collaboration events recorded</p>
            <p className="text-[11px] text-ink-400 mt-0.5">
              Launch a multi-agent workflow to observe coordinated stage execution.
            </p>
          </div>
        ) : (
          filtered.map((msg) => {
            const cfg = MESSAGE_TYPE_CONFIG[msg.message_type] || {
              label: msg.message_type,
              bg: "bg-ink-100",
              text: "text-ink-700",
              border: "border-ink-200",
              icon: MessageSquare,
            };
            const Icon = cfg.icon;
            const isExpanded = expandedMessageIds.has(msg.id);
            const hasExtra = msg.body_markdown || (msg.evidence_refs && msg.evidence_refs.length > 0);

            return (
              <div
                key={msg.id}
                className={`p-3 rounded-xl border transition-all text-xs ${cfg.border} ${
                  msg.author_role === "human_operator" ? "bg-amber-50/30" : "bg-white hover:bg-ink-50/30"
                }`}
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span className={`inline-flex items-center gap-1 text-[10.5px] font-semibold px-2 py-0.5 rounded-md ${cfg.bg} ${cfg.text} border ${cfg.border}`}>
                      <Icon size={11} />
                      {cfg.label}
                    </span>

                    <span className="font-semibold text-ink-900 capitalize text-[11px]">
                      {msg.author_role.replace("_", " ")}
                    </span>

                    {msg.target_role && (
                      <span className="text-[10px] text-ink-400 flex items-center gap-0.5">
                        ➔ <span className="capitalize">{msg.target_role.replace("_", " ")}</span>
                      </span>
                    )}
                  </div>

                  <span className="text-[10px] font-mono text-ink-400">
                    {new Date(msg.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
                  </span>
                </div>

                <p className="mt-1.5 text-[12px] font-medium text-ink-800 leading-snug">
                  {msg.summary}
                </p>

                {hasExtra && (
                  <div className="mt-2">
                    {isExpanded ? (
                      <div className="space-y-2 pt-2 border-t border-ink-100">
                        {msg.body_markdown && (
                          <div className="text-[11.5px] text-ink-700 whitespace-pre-wrap font-mono bg-ink-50 p-2.5 rounded-lg border border-ink-200/80 leading-relaxed overflow-x-auto">
                            {msg.body_markdown}
                          </div>
                        )}

                        {msg.evidence_refs && msg.evidence_refs.length > 0 && (
                          <div className="space-y-1">
                            <span className="text-[10px] uppercase font-semibold text-ink-400">
                              Evidence Citations
                            </span>
                            <div className="flex flex-wrap gap-1.5">
                              {msg.evidence_refs.map((ev, i) => (
                                <span
                                  key={i}
                                  className="inline-flex items-center gap-1 px-2 py-0.5 bg-purple-50 text-purple-700 border border-purple-200 rounded text-[10.5px] font-mono"
                                >
                                  <FileCheck2 size={11} />
                                  {typeof ev === "object" && ev !== null
                                    ? String(ev.file || ev.type || ev.artifact_id || JSON.stringify(ev))
                                    : String(ev)}
                                </span>
                              ))}
                            </div>
                          </div>
                        )}
                      </div>
                    ) : null}

                    <button
                      onClick={() => toggleExpand(msg.id)}
                      className="mt-1 inline-flex items-center gap-1 text-[10.5px] text-brand-600 hover:text-brand-800 font-medium"
                    >
                      {isExpanded ? (
                        <>
                          <ChevronUp size={12} /> Hide details
                        </>
                      ) : (
                        <>
                          <ChevronDown size={12} /> View details & evidence
                        </>
                      )}
                    </button>
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>

      {/* Operator Input Bar */}
      {runId && (
        <form
          onSubmit={handleSendOperatorMessage}
          className="p-3 border-t border-ink-100 bg-white flex items-center gap-2"
        >
          <input
            type="text"
            value={operatorInput}
            onChange={(e) => setOperatorInput(e.target.value)}
            placeholder="Send operator guidance or ask a question to the workroom..."
            className="flex-1 text-xs px-3 py-2 rounded-lg border border-ink-200 focus:border-brand-500 focus:outline-none"
          />
          <Button
            type="submit"
            variant="primary"
            size="sm"
            disabled={!operatorInput.trim() || postMutation.isPending}
            className="px-3"
          >
            <Send size={13} className="mr-1" />
            Send
          </Button>
        </form>
      )}
    </Card>
  );
}
