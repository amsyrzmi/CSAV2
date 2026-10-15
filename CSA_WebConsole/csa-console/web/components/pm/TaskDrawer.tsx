"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Bug,
  CheckSquare,
  FileText,
  MessageSquare,
  RefreshCw,
  Send,
  Trash2,
} from "lucide-react";
import { api, del, patch, post } from "@/lib/api";
import { Banner, Button, Drawer, Pill } from "@/components/ui/kit";
import { input, label, textarea } from "@/components/ui/input";
import { InvestigateModal } from "./hermes/InvestigateModal";
import { InvestigationReportCard } from "./hermes/InvestigationReportCard";
import type {
  AcceptanceCriterion,
  Assignee,
  BugInvestigation,
  Milestone,
  Task,
  TaskComment,
  TaskDetail,
  TaskPriority,
  TaskStatus,
} from "./types";

interface TaskDrawerProps {
  projectId: string;
  taskId: string | null;
  open: boolean;
  onClose: () => void;
  onUpdated: () => void;
  milestones: Milestone[];
  assignees: Assignee[];
  allTasks: Task[];
}

export function TaskDrawer({
  projectId,
  taskId,
  open,
  onClose,
  onUpdated,
  milestones,
  assignees,
  allTasks,
}: TaskDrawerProps) {
  const {
    data: task,
    isLoading,
    isError,
    refetch,
  } = useQuery({
    queryKey: ["admin-pm-task", projectId, taskId],
    queryFn: () =>
      api<TaskDetail>(`/api/admin/pm/projects/${projectId}/tasks/${taskId}`),
    enabled: !!projectId && !!taskId && open,
  });

  const { data: comments = [], refetch: refetchComments } = useQuery({
    queryKey: ["admin-pm-task-comments", projectId, taskId],
    queryFn: () =>
      api<TaskComment[]>(`/api/admin/pm/projects/${projectId}/tasks/${taskId}/comments`),
    enabled: !!projectId && !!taskId && open,
  });

  if (!open || !taskId) return null;

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={
        <div className="flex items-center gap-2">
          <span>Task Details</span>
          {task && (
            <span className="font-mono text-xs font-normal text-ink-400">
              v{task.version}
            </span>
          )}
        </div>
      }
    >
      {isLoading ? (
        <div className="py-12 text-center text-xs text-ink-400">Loading task...</div>
      ) : isError || !task ? (
        <div className="py-8 text-center text-xs text-red-600">Failed to load task.</div>
      ) : (
        <TaskDrawerContent
          key={`${task.id}-${task.version}`}
          projectId={projectId}
          task={task}
          comments={comments}
          milestones={milestones}
          assignees={assignees}
          allTasks={allTasks}
          onClose={onClose}
          onRefetch={refetch}
          onRefetchComments={refetchComments}
          onUpdated={onUpdated}
        />
      )}
    </Drawer>
  );
}

function TaskDrawerContent({
  projectId,
  task,
  comments,
  milestones,
  assignees,
  allTasks,
  onClose,
  onRefetch,
  onRefetchComments,
  onUpdated,
}: {
  projectId: string;
  task: TaskDetail;
  comments: TaskComment[];
  milestones: Milestone[];
  assignees: Assignee[];
  allTasks: Task[];
  onClose: () => void;
  onRefetch: () => void;
  onRefetchComments: () => void;
  onUpdated: () => void;
}) {
  const [title, setTitle] = useState(task.title);
  const [description, setDescription] = useState(task.description || "");
  const [priority, setPriority] = useState<TaskPriority>(task.priority);
  const [status, setStatus] = useState<TaskStatus>(task.status);
  const [milestoneId, setMilestoneId] = useState<string>(task.milestone_id || "");
  const [assigneeId, setAssigneeId] = useState<string>(task.human_assignee_id || "");
  const [criteria, setCriteria] = useState<AcceptanceCriterion[]>(task.acceptance_criteria || []);
  const [newCriterionText, setNewCriterionText] = useState("");
  const [overrideReason, setOverrideReason] = useState(task.override_reason || "");
  const [cancellationReason, setCancellationReason] = useState(task.cancellation_reason || "");

  // Dependency add state
  const [selectedDepTaskId, setSelectedDepTaskId] = useState("");
  const [depError, setDepError] = useState("");
  const [addingDep, setAddingDep] = useState(false);

  // New comment state
  const [commentText, setCommentText] = useState("");
  const [commentVisibility, setCommentVisibility] = useState<"admin_only" | "client_visible">("admin_only");
  const [postingComment, setPostingComment] = useState(false);

  // Linked notes state (Phase 03)
  const [projectNotes, setProjectNotes] = useState<{ id: string; title: string; folder: string }[]>([]);
  const [selectedNoteId, setSelectedNoteId] = useState("");
  const [selectedLinkType, setSelectedLinkType] = useState<"reference" | "evidence" | "spec">("reference");
  const [linkingNote, setLinkingNote] = useState(false);
  const [noteError, setNoteError] = useState("");

  // Hermes Bug Investigations state (Phase 07)
  const [investigateModalOpen, setInvestigateModalOpen] = useState(false);
  const { data: investigations = [], refetch: refetchInvestigations } = useQuery<BugInvestigation[]>({
    queryKey: ["admin-pm-investigations", projectId, task.id],
    queryFn: () =>
      api<BugInvestigation[]>(
        `/api/admin/pm/projects/${projectId}/investigations?task_id=${task.id}`
      ),
    enabled: !!projectId && !!task.id,
  });

  // Mutation states
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [isConflict, setIsConflict] = useState(false);

  // Acceptance Criteria handlers
  const toggleCriterion = (critId: string) => {
    setCriteria((prev) =>
      prev.map((c) => (c.id === critId ? { ...c, done: !c.done } : c)),
    );
  };

  const addCriterion = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newCriterionText.trim()) return;
    const newCrit: AcceptanceCriterion = {
      id: "crit-" + Date.now().toString(36),
      text: newCriterionText.trim(),
      done: false,
    };
    setCriteria((prev) => [...prev, newCrit]);
    setNewCriterionText("");
  };

  const removeCriterion = (critId: string) => {
    setCriteria((prev) => prev.filter((c) => c.id !== critId));
  };

  // Add dependency
  const handleAddDependency = async () => {
    if (!selectedDepTaskId) return;
    setAddingDep(true);
    setDepError("");
    try {
      await post(`/api/admin/pm/projects/${projectId}/tasks/${task.id}/dependencies`, {
        depends_on_task_id: selectedDepTaskId,
      });
      setSelectedDepTaskId("");
      onRefetch();
      onUpdated();
    } catch (err: unknown) {
      setDepError(err instanceof Error ? err.message : "Failed to add dependency");
    } finally {
      setAddingDep(false);
    }
  };

  // Remove dependency
  const handleRemoveDependency = async (depId: string) => {
    try {
      await del(`/api/admin/pm/projects/${projectId}/tasks/${task.id}/dependencies/${depId}`);
      onRefetch();
      onUpdated();
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : "Failed to remove dependency");
    }
  };

  // Linked Notes handlers (Phase 03)
  const loadProjectNotes = async () => {
    try {
      const res = await api<{ items: { id: string; title: string; folder: string }[] }>(
        `/api/admin/pm/projects/${projectId}/notes`
      );
      setProjectNotes(res.items || []);
    } catch (err) {
      console.error("Failed to load project notes for drawer:", err);
    }
  };

  const handleLinkNote = async () => {
    if (!selectedNoteId) return;
    setLinkingNote(true);
    setNoteError("");
    try {
      await post(`/api/admin/pm/projects/${projectId}/tasks/${task.id}/notes`, {
        note_id: selectedNoteId,
        link_type: selectedLinkType,
      });
      setSelectedNoteId("");
      onRefetch();
      onUpdated();
    } catch (err: unknown) {
      setNoteError(err instanceof Error ? err.message : "Failed to link note");
    } finally {
      setLinkingNote(false);
    }
  };

  const handleUnlinkNote = async (noteId: string) => {
    try {
      await del(`/api/admin/pm/projects/${projectId}/tasks/${task.id}/notes/${noteId}`);
      onRefetch();
      onUpdated();
    } catch (err: unknown) {
      setNoteError(err instanceof Error ? err.message : "Failed to unlink note");
    }
  };

  // Post comment
  const handlePostComment = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!commentText.trim()) return;
    setPostingComment(true);
    try {
      await post(`/api/admin/pm/projects/${projectId}/tasks/${task.id}/comments`, {
        body: commentText.trim(),
        visibility: commentVisibility,
      });
      setCommentText("");
      onRefetchComments();
      onUpdated();
    } catch (err: unknown) {
      setSaveError(err instanceof Error ? err.message : "Failed to post comment");
    } finally {
      setPostingComment(false);
    }
  };

  // Save Task
  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setSaveError("");
    setIsConflict(false);

    try {
      await patch(`/api/admin/pm/projects/${projectId}/tasks/${task.id}`, {
        version: task.version,
        title,
        description,
        priority,
        status,
        milestone_id: milestoneId || "",
        human_assignee_id: assigneeId || "",
        acceptance_criteria: criteria,
        override_reason: overrideReason,
        cancellation_reason: cancellationReason,
      });

      onRefetch();
      onUpdated();
      setSaveError("");
    } catch (err: unknown) {
      const apiErr = err as { status?: number; message?: string };
      if (apiErr?.status === 409) {
        setIsConflict(true);
        setSaveError("Version Conflict: This task was updated concurrently by another user.");
      } else {
        setSaveError(err instanceof Error ? err.message : "Failed to save task");
      }
    } finally {
      setSaving(false);
    }
  };

  const existingPredecessorIds = new Set(task.predecessors?.map((p) => p.task_id) || []);
  const eligibleDepTasks = allTasks.filter(
    (t) => t.id !== task.id && !existingPredecessorIds.has(t.id),
  );

  const isBlocked = task.is_blocked;
  const hasUncheckedCriteria = criteria.some((c) => !c.done);
  const requiresOverrideForStatus =
    (isBlocked && (status === "ready" || status === "in_progress")) ||
    (hasUncheckedCriteria && status === "done");

  return (
    <div className="space-y-6">
      {/* Version Conflict or Error Banner */}
      {saveError && (
        <Banner tone="error" onClose={() => setSaveError("")}>
          <div>
            <p className="text-xs font-medium">{saveError}</p>
            {isConflict && (
              <Button
                size="sm"
                variant="secondary"
                onClick={() => onRefetch()}
                className="mt-2 text-xs"
              >
                <RefreshCw size={12} className="mr-1" /> Reload Latest Data
              </Button>
            )}
          </div>
        </Banner>
      )}

      {/* Blocked warning */}
      {isBlocked && (
        <div className="rounded-xl border border-amber-300 bg-amber-50 p-3.5 text-xs text-amber-900">
          <div className="flex items-center gap-1.5 font-semibold text-amber-800">
            <AlertTriangle size={14} className="text-amber-600" />
            This task is currently blocked
          </div>
          <p className="mt-1 text-amber-700">
            Waiting on {task.blocked_by.length} incomplete predecessor tasks. Moving to
            ready or in-progress requires completing them or entering an authorized override.
          </p>
        </div>
      )}

      {/* Main Edit Form */}
      <form onSubmit={handleSave} className="space-y-4">
        {/* Title */}
        <div>
          <label className={label}>Title *</label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            required
            maxLength={200}
            className={input}
          />
        </div>

        {/* Status & Priority Row */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={label}>Status</label>
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value as TaskStatus)}
              className={input}
            >
              <option value="backlog">Backlog</option>
              <option value="ready">Ready</option>
              <option value="in_progress">In Progress</option>
              <option value="review">Review</option>
              <option value="done">Done</option>
              <option value="cancelled">Cancelled</option>
            </select>
          </div>

          <div>
            <label className={label}>Priority</label>
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value as TaskPriority)}
              className={input}
            >
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
              <option value="urgent">Urgent</option>
            </select>
          </div>
        </div>

        {/* Conditional Override Reason input */}
        {requiresOverrideForStatus && (
          <div className="rounded-lg border border-amber-200 bg-amber-50/60 p-3">
            <label className="block text-xs font-semibold text-amber-900">
              Authorized Override Reason *
            </label>
            <p className="mt-0.5 text-[11px] text-amber-700">
              {status === "done"
                ? "Required because open acceptance criteria remain."
                : "Required because predecessor dependencies are incomplete."}
            </p>
            <input
              type="text"
              value={overrideReason}
              onChange={(e) => setOverrideReason(e.target.value)}
              placeholder="e.g. Approved by lead developer for hotfix"
              required
              className="mt-1.5 w-full rounded-xl border border-amber-300 bg-white px-3 py-2 text-xs focus:outline-brand-600"
            />
          </div>
        )}

        {/* Conditional Cancellation Reason input */}
        {status === "cancelled" && (
          <div className="rounded-lg border border-red-200 bg-red-50/60 p-3">
            <label className="block text-xs font-semibold text-red-900">
              Cancellation Reason *
            </label>
            <input
              type="text"
              value={cancellationReason}
              onChange={(e) => setCancellationReason(e.target.value)}
              placeholder="e.g. Superseded by feature change"
              required
              className="mt-1.5 w-full rounded-xl border border-red-300 bg-white px-3 py-2 text-xs focus:outline-red-600"
            />
          </div>
        )}

        {/* Milestone & Assignee Row */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className={label}>Milestone</label>
            <select
              value={milestoneId}
              onChange={(e) => setMilestoneId(e.target.value)}
              className={input}
            >
              <option value="">No Milestone</option>
              {milestones.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.title}
                </option>
              ))}
            </select>
          </div>

          <div>
            <label className={label}>Assignee</label>
            <select
              value={assigneeId}
              onChange={(e) => setAssigneeId(e.target.value)}
              className={input}
            >
              <option value="">Unassigned</option>
              {assignees.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.display_name} ({a.role})
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Description */}
        <div>
          <label className={label}>Description</label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            placeholder="Details, requirements, and constraints..."
            className={textarea}
          />
        </div>

        {/* Acceptance Criteria Section */}
        <div className="border-t border-ink-100 pt-3">
          <div className="flex items-center justify-between mb-2">
            <label className="text-xs font-semibold text-ink-800 flex items-center gap-1.5">
              <CheckSquare size={14} className="text-brand-600" />
              Acceptance Criteria ({criteria.filter((c) => c.done).length}/{criteria.length})
            </label>
          </div>

          <div className="space-y-1.5 mb-2.5">
            {criteria.map((c) => (
              <div
                key={c.id}
                className="flex items-center justify-between rounded-lg border border-ink-200/70 bg-ink-50/40 px-2.5 py-1.5 text-xs"
              >
                <label className="flex items-center gap-2 cursor-pointer select-none min-w-0 flex-1">
                  <input
                    type="checkbox"
                    checked={c.done}
                    onChange={() => toggleCriterion(c.id)}
                    className="rounded border-ink-300 text-brand-600 focus:ring-brand-500"
                  />
                  <span className={c.done ? "line-through text-ink-400 truncate" : "truncate"}>
                    {c.text}
                  </span>
                </label>
                <button
                  type="button"
                  onClick={() => removeCriterion(c.id)}
                  className="ml-2 text-ink-400 hover:text-red-600 transition-colors"
                >
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
          </div>

          {/* Add Criterion Input */}
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={newCriterionText}
              onChange={(e) => setNewCriterionText(e.target.value)}
              placeholder="New acceptance criteria..."
              className="h-8 flex-1 rounded-xl border border-ink-200 bg-white px-3 text-xs focus:outline-brand-600"
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  addCriterion(e);
                }
              }}
            />
            <Button size="sm" variant="secondary" onClick={addCriterion}>
              Add
            </Button>
          </div>
        </div>

        {/* Save Buttons */}
        <div className="flex items-center justify-end gap-2 pt-2 border-t border-ink-100">
          <Button variant="secondary" size="sm" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button variant="primary" size="sm" type="submit" disabled={saving}>
            {saving ? "Saving…" : "Save Changes"}
          </Button>
        </div>
      </form>

      {/* Dependencies Section */}
      <div className="border-t border-ink-200 pt-4 space-y-3">
        <h4 className="text-xs font-semibold text-ink-800">Dependencies & Blockers</h4>

        {depError && (
          <Banner tone="error" onClose={() => setDepError("")}>
            {depError}
          </Banner>
        )}

        {/* Predecessors list ("Blocked by") */}
        <div>
          <p className="text-[11px] font-medium text-ink-500 mb-1.5">
            Blocked by (Predecessors):
          </p>
          {task.predecessors?.length === 0 ? (
            <p className="text-xs italic text-ink-400">No predecessors.</p>
          ) : (
            <div className="space-y-1">
              {task.predecessors.map((p) => (
                <div
                  key={p.dep_id}
                  className="flex items-center justify-between rounded-lg border border-ink-200 bg-white p-2 text-xs"
                >
                  <div className="flex items-center gap-2 truncate">
                    <Pill
                      tone={p.status === "done" ? "good" : "warn"}
                      className="text-[10px] uppercase font-bold"
                    >
                      {p.status}
                    </Pill>
                    <span className="truncate text-ink-800">{p.title}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => p.dep_id && handleRemoveDependency(p.dep_id)}
                    className="text-ink-400 hover:text-red-600 transition-colors ml-2"
                    title="Remove dependency"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Successors list ("Blocks") */}
        {task.successors?.length > 0 && (
          <div>
            <p className="text-[11px] font-medium text-ink-500 mb-1.5">
              Blocks (Successors waiting on this task):
            </p>
            <div className="space-y-1">
              {task.successors.map((s) => (
                <div
                  key={s.dep_id}
                  className="flex items-center justify-between rounded-lg border border-ink-100 bg-ink-50/50 p-2 text-xs text-ink-700"
                >
                  <span className="truncate">{s.title}</span>
                  <Pill tone="neutral" className="text-[10px]">
                    {s.status}
                  </Pill>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Add Dependency picker */}
        <div className="flex items-center gap-2 pt-1">
          <select
            value={selectedDepTaskId}
            onChange={(e) => setSelectedDepTaskId(e.target.value)}
            className="h-8 flex-1 rounded-xl border border-ink-200 bg-white px-2 text-xs text-ink-800"
          >
            <option value="">Select a task to depend on...</option>
            {eligibleDepTasks.map((t) => (
              <option key={t.id} value={t.id}>
                {t.title} ({t.status})
              </option>
            ))}
          </select>
          <Button
            size="sm"
            variant="secondary"
            disabled={!selectedDepTaskId || addingDep}
            onClick={handleAddDependency}
          >
            {addingDep ? "Adding..." : "Add Block"}
          </Button>
        </div>
      </div>

      {/* Linked Notes & Knowledge Evidence (Phase 03) */}
      <div className="border-t border-ink-200 pt-4 space-y-3">
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-semibold text-ink-800 flex items-center gap-1.5">
            <FileText size={13} />
            Linked Notes & Evidence ({task.linked_notes?.length || 0})
          </h4>
        </div>

        {noteError && (
          <Banner tone="error" onClose={() => setNoteError("")}>
            {noteError}
          </Banner>
        )}

        {/* Linked notes list */}
        {task.linked_notes && task.linked_notes.length > 0 ? (
          <div className="space-y-1.5">
            {task.linked_notes.map((ln) => (
              <div
                key={ln.note_id}
                className="flex items-center justify-between rounded-lg border border-ink-200 bg-white p-2 text-xs"
              >
                <div className="flex items-center gap-2 truncate flex-1">
                  <span className="text-blue-600">📄</span>
                  <div className="truncate">
                    <span className="font-medium text-ink-800">{ln.note_title}</span>
                    <span className="text-[10px] text-ink-400 font-mono ml-2">{ln.note_folder}</span>
                  </div>
                </div>

                <div className="flex items-center gap-1.5 ml-2">
                  <Pill tone="brand" className="text-[10px] uppercase font-bold">
                    {ln.link_type}
                  </Pill>
                  <button
                    type="button"
                    onClick={() => handleUnlinkNote(ln.note_id)}
                    className="text-ink-400 hover:text-red-600 transition-colors ml-1"
                    title="Unlink note"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs italic text-ink-400">No notes or ADR evidence linked yet.</p>
        )}

        {/* Link note picker */}
        <div className="flex items-center gap-2 pt-1">
          <select
            value={selectedNoteId}
            onFocus={() => {
              if (projectNotes.length === 0) loadProjectNotes();
            }}
            onChange={(e) => setSelectedNoteId(e.target.value)}
            className="h-8 flex-1 rounded-xl border border-ink-200 bg-white px-2 text-xs text-ink-800"
          >
            <option value="">Select a note to link as evidence/spec...</option>
            {projectNotes.map((pn) => (
              <option key={pn.id} value={pn.id}>
                {pn.title} ({pn.folder})
              </option>
            ))}
          </select>

          <select
            value={selectedLinkType}
            onChange={(e) => setSelectedLinkType(e.target.value as "reference" | "evidence" | "spec")}
            className="h-8 rounded-xl border border-ink-200 bg-white px-2 text-xs text-ink-700"
          >
            <option value="reference">Reference</option>
            <option value="evidence">Evidence</option>
            <option value="spec">Spec</option>
          </select>

          <Button
            size="sm"
            variant="secondary"
            disabled={!selectedNoteId || linkingNote}
            onClick={handleLinkNote}
          >
            {linkingNote ? "Linking..." : "Link"}
          </Button>
        </div>
      </div>

      {/* Hermes Bug Investigations & Evidence (Phase 07) */}
      <div className="border-t border-ink-200 pt-4 space-y-3">
        <div className="flex items-center justify-between">
          <h4 className="text-xs font-semibold text-ink-800 flex items-center gap-1.5">
            <Bug size={13} className="text-blue-600" />
            Hermes Bug Investigations ({investigations.length})
          </h4>
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setInvestigateModalOpen(true)}
          >
            <Bug size={12} className="mr-1 text-blue-600" />
            Investigate with Hermes
          </Button>
        </div>

        {investigations.length > 0 ? (
          <div className="space-y-3">
            {investigations.map((inv) => (
              <InvestigationReportCard
                key={inv.id}
                investigation={inv}
                onRefresh={refetchInvestigations}
              />
            ))}
          </div>
        ) : (
          <p className="text-xs italic text-ink-400">
            No Hermes bug investigations launched for this task yet.
          </p>
        )}
      </div>

      <InvestigateModal
        projectId={projectId}
        taskId={task.id}
        defaultTitle={`Investigate: ${task.title}`}
        defaultSymptom={task.description || ""}
        isOpen={investigateModalOpen}
        onClose={() => setInvestigateModalOpen(false)}
        onInvestigationCreated={() => {
          refetchInvestigations();
          onUpdated();
        }}
      />

      {/* Comments Section */}
      <div className="border-t border-ink-200 pt-4 space-y-3">
        <h4 className="text-xs font-semibold text-ink-800 flex items-center gap-1.5">
          <MessageSquare size={13} />
          Comments & Notes ({comments.length})
        </h4>

        {/* Comments List */}
        <div className="space-y-2.5 max-h-60 overflow-y-auto pr-1">
          {comments.length === 0 ? (
            <p className="text-xs italic text-ink-400">No comments on this task yet.</p>
          ) : (
            comments.map((c) => (
              <div
                key={c.id}
                className="rounded-lg border border-ink-100 bg-ink-50/60 p-2.5 text-xs space-y-1"
              >
                <div className="flex items-center justify-between text-[11px] text-ink-400">
                  <span className="font-semibold text-ink-700">
                    {c.author_name || "Admin"}
                  </span>
                  <span>{new Date(c.created_at).toLocaleTimeString()}</span>
                </div>
                <p className="text-ink-800 whitespace-pre-wrap">{c.body}</p>
              </div>
            ))
          )}
        </div>

        {/* Add Comment Box */}
        <form onSubmit={handlePostComment} className="space-y-2 pt-1">
          <textarea
            value={commentText}
            onChange={(e) => setCommentText(e.target.value)}
            placeholder="Write a comment or status note..."
            rows={2}
            className="w-full rounded-xl border border-ink-200 bg-white p-2.5 text-xs text-ink-800 focus:outline-brand-600"
          />
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5 text-xs text-ink-500">
              <span className="text-[11px]">Visibility:</span>
              <select
                value={commentVisibility}
                onChange={(e) =>
                  setCommentVisibility(e.target.value as "admin_only" | "client_visible")
                }
                className="rounded border border-ink-200 bg-white px-2 py-0.5 text-[11px] text-ink-700"
              >
                <option value="admin_only">Admin Only</option>
                <option value="client_visible">Client Visible</option>
              </select>
            </div>
            <Button
              size="sm"
              variant="primary"
              type="submit"
              disabled={!commentText.trim() || postingComment}
            >
              <Send size={11} className="mr-1" />
              {postingComment ? "Posting…" : "Post"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
