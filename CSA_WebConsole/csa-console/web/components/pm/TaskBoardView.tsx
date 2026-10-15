"use client";

import clsx from "clsx";
import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Kanban,
  List,
  Milestone as MilestoneIcon,
  Plus,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { api, post } from "@/lib/api";
import { Banner, Button, Pill, Skeleton } from "@/components/ui/kit";
import { input } from "@/components/ui/input";
import { TaskCard } from "./TaskCard";
import { TaskDrawer } from "./TaskDrawer";
import { MilestoneModal } from "./MilestoneModal";
import type {
  Assignee,
  Milestone,
  Task,
  TaskStatus,
} from "./types";

const COLUMNS: { id: TaskStatus; label: string; tone: string; pillTone: "neutral" | "brand" | "warn" | "good" }[] = [
  { id: "backlog", label: "Backlog", tone: "border-ink-200 bg-ink-50/40", pillTone: "neutral" },
  { id: "ready", label: "Ready", tone: "border-brand-200 bg-brand-50/30", pillTone: "brand" },
  { id: "in_progress", label: "In Progress", tone: "border-amber-200 bg-amber-50/30", pillTone: "warn" },
  { id: "review", label: "Review", tone: "border-purple-200 bg-purple-50/20", pillTone: "brand" },
  { id: "done", label: "Done", tone: "border-emerald-200 bg-emerald-50/30", pillTone: "good" },
];

export function TaskBoardView({ projectId }: { projectId: string }) {
  const qc = useQueryClient();

  // View state
  const [viewMode, setViewMode] = useState<"board" | "list">("board");
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedMilestone, setSelectedMilestone] = useState<string>("all");
  const [selectedPriority, setSelectedPriority] = useState<string>("all");
  const [selectedAssignee, setSelectedAssignee] = useState<string>("all");

  // Drawers and modals
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [milestonesOpen, setMilestonesOpen] = useState(false);

  // Quick inline creation state per column
  const [inlineCol, setInlineCol] = useState<TaskStatus | null>(null);
  const [inlineTitle, setInlineTitle] = useState("");
  const [inlineCreating, setInlineCreating] = useState(false);

  // Errors and banners
  const [boardError, setBoardError] = useState("");

  // Queries
  const {
    data: tasksData,
    isLoading: tasksLoading,
    refetch: refetchTasks,
  } = useQuery({
    queryKey: ["admin-pm-tasks", projectId],
    queryFn: () => api<{ items: Task[] }>(`/api/admin/pm/projects/${projectId}/tasks?limit=200`),
    enabled: !!projectId,
  });

  const { data: milestones = [], refetch: refetchMilestones } = useQuery({
    queryKey: ["admin-pm-milestones", projectId],
    queryFn: () => api<Milestone[]>(`/api/admin/pm/projects/${projectId}/milestones`),
    enabled: !!projectId,
  });

  const { data: assignees = [] } = useQuery({
    queryKey: ["admin-pm-assignees", projectId],
    queryFn: () => api<Assignee[]>(`/api/admin/pm/projects/${projectId}/assignees`),
    enabled: !!projectId,
  });

  const allTasks: Task[] = useMemo(() => tasksData?.items || [], [tasksData]);

  // Filter tasks
  const filteredTasks = useMemo(() => {
    return allTasks.filter((t) => {
      if (selectedMilestone !== "all" && t.milestone_id !== selectedMilestone) {
        return false;
      }
      if (selectedPriority !== "all" && t.priority !== selectedPriority) {
        return false;
      }
      if (selectedAssignee !== "all" && t.human_assignee_id !== selectedAssignee) {
        return false;
      }
      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase();
        const matchTitle = t.title.toLowerCase().includes(q);
        const matchDesc = t.description?.toLowerCase().includes(q);
        if (!matchTitle && !matchDesc) return false;
      }
      return true;
    });
  }, [allTasks, selectedMilestone, selectedPriority, selectedAssignee, searchQuery]);

  // Group tasks by column
  const tasksByColumn = useMemo(() => {
    const map: Record<TaskStatus, Task[]> = {
      backlog: [],
      ready: [],
      in_progress: [],
      review: [],
      done: [],
      cancelled: [],
    };
    for (const t of filteredTasks) {
      if (map[t.status]) {
        map[t.status].push(t);
      }
    }
    return map;
  }, [filteredTasks]);

  // Move task handler (with optimistic update and rollback)
  const handleMoveTask = async (
    taskId: string,
    targetStatus: TaskStatus,
    targetPos: number,
    expectedVersion: number,
  ) => {
    const originalTasks = tasksData?.items || [];
    const targetTask = originalTasks.find((t) => t.id === taskId);
    if (!targetTask) return;

    // Optimistic cache update
    qc.setQueryData(["admin-pm-tasks", projectId], {
      items: originalTasks.map((t) =>
        t.id === taskId ? { ...t, status: targetStatus, version: t.version + 1 } : t,
      ),
    });

    try {
      await post(`/api/admin/pm/projects/${projectId}/tasks/${taskId}/move`, {
        target_status: targetStatus,
        target_position: targetPos,
        expected_version: expectedVersion,
      });
      await refetchTasks();
      setBoardError("");
    } catch (err: unknown) {
      // Rollback on failure
      qc.setQueryData(["admin-pm-tasks", projectId], { items: originalTasks });
      setBoardError(err instanceof Error ? err.message : "Failed to move task");
    }
  };

  // Drag-and-drop drop handler
  const handleDropOnColumn = (e: React.DragEvent, colStatus: TaskStatus) => {
    e.preventDefault();
    try {
      const dataStr = e.dataTransfer.getData("application/json");
      if (!dataStr) return;
      const { taskId, fromStatus, version } = JSON.parse(dataStr);
      if (fromStatus === colStatus) return;
      handleMoveTask(taskId, colStatus, 0, version);
    } catch {}
  };

  // Inline task creation in column
  const handleInlineCreate = async (col: TaskStatus) => {
    if (!inlineTitle.trim()) return;
    setInlineCreating(true);
    setBoardError("");
    try {
      await post(`/api/admin/pm/projects/${projectId}/tasks`, {
        title: inlineTitle.trim(),
        status: col,
        priority: "medium",
        milestone_id: selectedMilestone !== "all" ? selectedMilestone : undefined,
      });
      setInlineTitle("");
      setInlineCol(null);
      await refetchTasks();
    } catch (err: unknown) {
      setBoardError(err instanceof Error ? err.message : "Failed to create task");
    } finally {
      setInlineCreating(false);
    }
  };

  const openTaskDetail = (t: Task) => {
    setSelectedTaskId(t.id);
    setDrawerOpen(true);
  };

  return (
    <div className="space-y-4">
      {boardError && (
        <Banner tone="error" onClose={() => setBoardError("")}>
          {boardError}
        </Banner>
      )}

      {/* Filter and Controls Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-ink-200/80 bg-white p-3.5 shadow-xs">
        <div className="flex flex-wrap items-center gap-2.5 flex-1 min-w-[280px]">
          {/* Search */}
          <div className="relative min-w-[200px] max-w-xs flex-1">
            <Search size={14} className="absolute left-2.5 top-2.5 text-ink-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search tasks..."
              className={clsx(input, "h-9 pl-8 text-xs")}
            />
            {searchQuery && (
              <button
                type="button"
                onClick={() => setSearchQuery("")}
                className="absolute right-2.5 top-2.5 text-ink-400 hover:text-ink-600"
              >
                <X size={13} />
              </button>
            )}
          </div>

          {/* Milestone Filter */}
          <div className="flex items-center gap-1.5">
            <select
              value={selectedMilestone}
              onChange={(e) => setSelectedMilestone(e.target.value)}
              className={clsx(input, "h-9 text-xs max-w-[170px]")}
            >
              <option value="all">All Milestones</option>
              {milestones.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.title}
                </option>
              ))}
            </select>
          </div>

          {/* Priority Filter */}
          <div className="flex items-center gap-1.5">
            <select
              value={selectedPriority}
              onChange={(e) => setSelectedPriority(e.target.value)}
              className={clsx(input, "h-9 text-xs capitalize max-w-[140px]")}
            >
              <option value="all">All Priorities</option>
              <option value="urgent">Urgent</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </div>

          {/* Assignee Filter */}
          <div className="flex items-center gap-1.5">
            <select
              value={selectedAssignee}
              onChange={(e) => setSelectedAssignee(e.target.value)}
              className={clsx(input, "h-9 text-xs max-w-[160px]")}
            >
              <option value="all">All Assignees</option>
              {assignees.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.display_name}
                </option>
              ))}
            </select>
          </div>
        </div>

        {/* Action Controls & View Switcher */}
        <div className="flex items-center gap-2">
          {/* View Mode Toggle */}
          <div className="flex items-center rounded-xl border border-ink-200 bg-ink-50 p-0.5">
            <button
              type="button"
              onClick={() => setViewMode("board")}
              className={clsx(
                "flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors",
                viewMode === "board"
                  ? "bg-white text-ink-900 shadow-xs"
                  : "text-ink-500 hover:text-ink-800",
              )}
            >
              <Kanban size={13} /> Board
            </button>
            <button
              type="button"
              onClick={() => setViewMode("list")}
              className={clsx(
                "flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-xs font-medium transition-colors",
                viewMode === "list"
                  ? "bg-white text-ink-900 shadow-xs"
                  : "text-ink-500 hover:text-ink-800",
              )}
            >
              <List size={13} /> List
            </button>
          </div>

          {/* Milestone Manager button */}
          <Button
            size="sm"
            variant="secondary"
            onClick={() => setMilestonesOpen(true)}
            className="text-xs"
          >
            <MilestoneIcon size={13} className="mr-1 text-brand-600" /> Milestones
          </Button>

          {/* New Task button */}
          <Button
            size="sm"
            variant="primary"
            onClick={() => {
              setInlineCol("backlog");
            }}
            className="text-xs"
          >
            <Plus size={13} className="mr-1" /> New Task
          </Button>

          {/* Refresh button */}
          <button
            type="button"
            onClick={() => {
              refetchTasks();
              refetchMilestones();
            }}
            className="flex h-9 w-9 items-center justify-center rounded-xl border border-ink-200 text-ink-500 hover:bg-ink-50 hover:text-ink-800 transition-colors"
            title="Refresh board"
          >
            <RefreshCw size={13} />
          </button>
        </div>
      </div>

      {/* Main Board Kanban View */}
      {viewMode === "board" ? (
        tasksLoading ? (
          <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
            {COLUMNS.map((col) => (
              <div key={col.id} className="space-y-3 rounded-2xl bg-ink-50/50 p-3">
                <Skeleton className="h-6 w-24 rounded-md" />
                <Skeleton className="h-24 w-full rounded-xl" />
                <Skeleton className="h-24 w-full rounded-xl" />
              </div>
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-5 gap-3.5 items-start">
            {COLUMNS.map((col) => {
              const colTasks = tasksByColumn[col.id] || [];

              return (
                <div
                  key={col.id}
                  onDragOver={(e) => e.preventDefault()}
                  onDrop={(e) => handleDropOnColumn(e, col.id)}
                  className={clsx(
                    "flex flex-col rounded-2xl border p-3 transition-colors min-h-[500px]",
                    col.tone,
                  )}
                >
                  {/* Column Header */}
                  <div className="flex items-center justify-between pb-2.5 mb-2 border-b border-ink-200/60">
                    <div className="flex items-center gap-2">
                      <span className="text-[13px] font-semibold text-ink-900">
                        {col.label}
                      </span>
                      <span className="flex h-5 w-5 items-center justify-center rounded-full bg-ink-200/60 font-mono text-[11px] font-semibold text-ink-700">
                        {colTasks.length}
                      </span>
                    </div>

                    <button
                      type="button"
                      onClick={() => {
                        setInlineCol(inlineCol === col.id ? null : col.id);
                        setInlineTitle("");
                      }}
                      className="rounded p-1 text-ink-400 hover:bg-ink-200/60 hover:text-ink-800 transition-colors"
                      title={`Add task to ${col.label}`}
                    >
                      <Plus size={14} />
                    </button>
                  </div>

                  {/* Tasks in Column */}
                  <div className="space-y-2.5 flex-1">
                    {colTasks.length === 0 ? (
                      <div className="flex flex-col items-center justify-center py-8 text-center text-xs text-ink-400">
                        <span>No tasks</span>
                      </div>
                    ) : (
                      colTasks.map((t) => (
                        <TaskCard
                          key={t.id}
                          task={t}
                          onSelect={openTaskDetail}
                          onMove={handleMoveTask}
                        />
                      ))
                    )}

                    {/* Inline Creation Input */}
                    {inlineCol === col.id && (
                      <div className="rounded-xl border border-brand-300 bg-white p-2.5 shadow-sm space-y-2">
                        <textarea
                          autoFocus
                          rows={2}
                          value={inlineTitle}
                          onChange={(e) => setInlineTitle(e.target.value)}
                          placeholder={`Task title in ${col.label}...`}
                          className={clsx(input, "text-xs resize-none")}
                          onKeyDown={(e) => {
                            if (e.key === "Enter" && !e.shiftKey) {
                              e.preventDefault();
                              handleInlineCreate(col.id);
                            }
                            if (e.key === "Escape") {
                              setInlineCol(null);
                            }
                          }}
                        />
                        <div className="flex items-center justify-between pt-1">
                          <span className="text-[10px] text-ink-400">Enter to add</span>
                          <div className="flex items-center gap-1.5">
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => setInlineCol(null)}
                              className="h-7 px-2 text-xs"
                            >
                              Cancel
                            </Button>
                            <Button
                              size="sm"
                              variant="primary"
                              disabled={!inlineTitle.trim() || inlineCreating}
                              onClick={() => handleInlineCreate(col.id)}
                              className="h-7 px-2.5 text-xs"
                            >
                              {inlineCreating ? "Adding…" : "Add"}
                            </Button>
                          </div>
                        </div>
                      </div>
                    )}
                  </div>

                  {/* Add task quick button at bottom */}
                  {inlineCol !== col.id && (
                    <button
                      type="button"
                      onClick={() => {
                        setInlineCol(col.id);
                        setInlineTitle("");
                      }}
                      className="mt-2.5 flex w-full items-center justify-center gap-1 rounded-xl border border-dashed border-ink-300/70 py-1.5 text-xs font-medium text-ink-500 hover:border-brand-400 hover:bg-white hover:text-brand-600 transition-all"
                    >
                      <Plus size={13} /> Add Task
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        )
      ) : (
        /* List / Table View */
        <div className="rounded-2xl border border-ink-200 bg-white overflow-hidden shadow-xs">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-ink-200 bg-ink-50/60 text-ink-500 font-medium">
              <tr>
                <th className="py-3 px-4">Task</th>
                <th className="py-3 px-3">Status</th>
                <th className="py-3 px-3">Priority</th>
                <th className="py-3 px-3">Milestone</th>
                <th className="py-3 px-3">Assignee</th>
                <th className="py-3 px-3">Blockers</th>
                <th className="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-ink-100">
              {filteredTasks.length === 0 ? (
                <tr>
                  <td colSpan={7} className="py-12 text-center text-ink-400 italic">
                    No tasks match the selected filters.
                  </td>
                </tr>
              ) : (
                filteredTasks.map((t) => (
                  <tr
                    key={t.id}
                    onClick={() => openTaskDetail(t)}
                    className="hover:bg-brand-50/20 cursor-pointer transition-colors"
                  >
                    <td className="py-3 px-4 max-w-xs">
                      <div className="font-medium text-ink-900 truncate">{t.title}</div>
                      {t.description && (
                        <div className="text-[11px] text-ink-400 truncate">{t.description}</div>
                      )}
                    </td>
                    <td className="py-3 px-3">
                      <Pill
                        tone={
                          t.status === "done"
                            ? "good"
                            : t.status === "in_progress"
                            ? "warn"
                            : t.status === "ready"
                            ? "brand"
                            : "neutral"
                        }
                        className="capitalize text-[11px]"
                      >
                        {t.status.replace("_", " ")}
                      </Pill>
                    </td>
                    <td className="py-3 px-3 capitalize">{t.priority}</td>
                    <td className="py-3 px-3 text-ink-600">
                      {t.milestone_title || <span className="text-ink-400 italic">—</span>}
                    </td>
                    <td className="py-3 px-3">
                      {t.assignee_name ? (
                        <span className="font-medium text-ink-800">{t.assignee_name}</span>
                      ) : (
                        <span className="text-ink-400 italic">Unassigned</span>
                      )}
                    </td>
                    <td className="py-3 px-3">
                      {t.is_blocked ? (
                        <span className="inline-flex items-center gap-1 text-[11px] font-semibold text-amber-700">
                          <AlertTriangle size={12} /> Blocked ({t.blocked_by.length})
                        </span>
                      ) : (
                        <span className="text-ink-400">—</span>
                      )}
                    </td>
                    <td className="py-3 px-4 text-right">
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={(e) => {
                          e.stopPropagation();
                          openTaskDetail(t);
                        }}
                        className="text-xs text-brand-600"
                      >
                        Inspect
                      </Button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Task Detail Drawer */}
      <TaskDrawer
        projectId={projectId}
        taskId={selectedTaskId}
        open={drawerOpen}
        onClose={() => {
          setDrawerOpen(false);
          setSelectedTaskId(null);
        }}
        onUpdated={() => {
          refetchTasks();
        }}
        milestones={milestones}
        assignees={assignees}
        allTasks={allTasks}
      />

      {/* Milestone Modal */}
      <MilestoneModal
        projectId={projectId}
        open={milestonesOpen}
        onClose={() => setMilestonesOpen(false)}
        onUpdated={() => {
          refetchMilestones();
          refetchTasks();
        }}
      />
    </div>
  );
}
