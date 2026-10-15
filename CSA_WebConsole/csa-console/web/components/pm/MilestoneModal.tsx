"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Calendar,
  CheckCircle2,
  Milestone as MilestoneIcon,
  Plus,
  Trash2,
} from "lucide-react";
import { api, del, patch, post } from "@/lib/api";
import { Banner, Button, Drawer, Pill } from "@/components/ui/kit";
import { input, label, textarea } from "@/components/ui/input";
import type { Milestone } from "./types";

export function MilestoneModal({
  projectId,
  open,
  onClose,
  onUpdated,
}: {
  projectId: string;
  open: boolean;
  onClose: () => void;
  onUpdated: () => void;
}) {
  const { data: milestones = [], refetch, isLoading } = useQuery({
    queryKey: ["admin-pm-milestones", projectId],
    queryFn: () => api<Milestone[]>(`/api/admin/pm/projects/${projectId}/milestones`),
    enabled: !!projectId && open,
  });

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [dueAt, setDueAt] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    setCreating(true);
    setError("");

    try {
      await post(`/api/admin/pm/projects/${projectId}/milestones`, {
        title: title.trim(),
        description: description.trim(),
        due_at: dueAt ? new Date(dueAt).toISOString() : null,
      });

      setTitle("");
      setDescription("");
      setDueAt("");
      await refetch();
      onUpdated();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to create milestone");
    } finally {
      setCreating(false);
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm("Are you sure you want to delete this milestone? Tasks will be unlinked.")) {
      return;
    }
    try {
      await del(`/api/admin/pm/projects/${projectId}/milestones/${id}`);
      await refetch();
      onUpdated();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to delete milestone");
    }
  };

  const handleToggleStatus = async (m: Milestone) => {
    const nextStatus = m.status === "completed" ? "open" : "completed";
    try {
      await patch(`/api/admin/pm/projects/${projectId}/milestones/${m.id}`, {
        version: m.version,
        status: nextStatus,
      });
      await refetch();
      onUpdated();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to update milestone status");
    }
  };

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={
        <div className="flex items-center gap-2">
          <MilestoneIcon size={18} className="text-brand-600" />
          <span>Project Milestones</span>
        </div>
      }
    >
      <div className="space-y-6">
        {error && (
          <Banner tone="error" onClose={() => setError("")}>
            {error}
          </Banner>
        )}

        {/* Existing Milestones List */}
        <div className="space-y-3">
          <h4 className="text-xs font-semibold text-ink-800">
            Active Milestones ({milestones.length})
          </h4>

          {isLoading ? (
            <p className="text-xs text-ink-400">Loading milestones...</p>
          ) : milestones.length === 0 ? (
            <p className="rounded-lg border border-dashed border-ink-200 p-4 text-center text-xs text-ink-500">
              No milestones created yet. Milestones group tasks to track phase delivery.
            </p>
          ) : (
            <div className="divide-y divide-ink-100 rounded-xl border border-ink-200 bg-white shadow-xs">
              {milestones.map((m) => {
                const total = m.total_tasks || 0;
                const completed = m.completed_tasks || 0;
                const percent = total > 0 ? Math.round((completed / total) * 100) : 0;

                return (
                  <div key={m.id} className="p-3.5 space-y-2">
                    <div className="flex items-start justify-between gap-2">
                      <div>
                        <div className="flex items-center gap-2">
                          <h5 className="text-[13px] font-semibold text-ink-900">
                            {m.title}
                          </h5>
                          <Pill
                            tone={
                              m.status === "completed"
                                ? "good"
                                : m.status === "cancelled"
                                ? "bad"
                                : "brand"
                            }
                            className="text-[10px] capitalize"
                          >
                            {m.status}
                          </Pill>
                        </div>
                        {m.description && (
                          <p className="mt-0.5 text-xs text-ink-500">{m.description}</p>
                        )}
                      </div>

                      <div className="flex items-center gap-1.5">
                        <button
                          type="button"
                          onClick={() => handleToggleStatus(m)}
                          className="rounded p-1 text-ink-400 hover:bg-ink-100 hover:text-ink-700"
                          title={m.status === "completed" ? "Mark open" : "Mark completed"}
                        >
                          <CheckCircle2
                            size={15}
                            className={m.status === "completed" ? "text-emerald-600" : ""}
                          />
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDelete(m.id)}
                          className="rounded p-1 text-ink-400 hover:bg-red-50 hover:text-red-600"
                          title="Delete milestone"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>

                    {/* Progress Bar & Due Date */}
                    <div className="flex items-center justify-between text-[11px] text-ink-500 pt-1">
                      <div className="flex items-center gap-2 flex-1 max-w-[200px]">
                        <div className="h-1.5 flex-1 rounded-full bg-ink-100 overflow-hidden">
                          <div
                            className="h-full bg-brand-600 rounded-full transition-all"
                            style={{ width: `${percent}%` }}
                          />
                        </div>
                        <span className="font-mono text-[10.5px]">
                          {completed}/{total}
                        </span>
                      </div>

                      {m.due_at && (
                        <span className="inline-flex items-center gap-1 text-ink-400">
                          <Calendar size={11} />
                          {new Date(m.due_at).toLocaleDateString()}
                        </span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Create Milestone Form */}
        <div className="rounded-xl border border-ink-200 bg-ink-50/50 p-4 space-y-3.5">
          <h4 className="text-xs font-semibold text-ink-800 flex items-center gap-1.5">
            <Plus size={14} className="text-brand-600" />
            Create New Milestone
          </h4>

          <form onSubmit={handleCreate} className="space-y-3">
            <div>
              <label className={label}>Milestone Title *</label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. Phase 02: Kanban Core"
                required
                maxLength={120}
                className={input}
              />
            </div>

            <div>
              <label className={label}>Target Due Date</label>
              <input
                type="date"
                value={dueAt}
                onChange={(e) => setDueAt(e.target.value)}
                className={input}
              />
            </div>

            <div>
              <label className={label}>Scope Description</label>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Milestone goals, deliverables, and focus areas..."
                rows={2}
                maxLength={1000}
                className={textarea}
              />
            </div>

            <Button
              type="submit"
              variant="primary"
              size="sm"
              disabled={!title.trim() || creating}
              className="w-full"
            >
              {creating ? "Creating…" : "Create Milestone"}
            </Button>
          </form>
        </div>
      </div>
    </Drawer>
  );
}
