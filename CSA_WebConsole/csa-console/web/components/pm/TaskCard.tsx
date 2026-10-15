"use client";

import clsx from "clsx";
import { useState } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  CheckSquare,
  MessageSquare,
  Milestone as MilestoneIcon,
  User,
} from "lucide-react";
import { Pill } from "@/components/ui/kit";
import type { Task, TaskPriority, TaskStatus } from "./types";

const PRIORITY_TONES: Record<TaskPriority, "bad" | "warn" | "brand" | "neutral"> = {
  urgent: "bad",
  high: "warn",
  medium: "brand",
  low: "neutral",
};

const STATUS_ORDER: TaskStatus[] = ["backlog", "ready", "in_progress", "review", "done"];

export function TaskCard({
  task,
  onSelect,
  onMove,
}: {
  task: Task;
  onSelect: (t: Task) => void;
  onMove: (taskId: string, targetStatus: TaskStatus, targetPos: number, expectedVersion: number) => void;
}) {
  const [isHovered, setIsHovered] = useState(false);

  const doneCriteria = task.acceptance_criteria.filter((c) => c.done).length;
  const totalCriteria = task.acceptance_criteria.length;

  const currentStatusIndex = STATUS_ORDER.indexOf(task.status);
  const prevStatus = currentStatusIndex > 0 ? STATUS_ORDER[currentStatusIndex - 1] : null;
  const nextStatus =
    currentStatusIndex >= 0 && currentStatusIndex < STATUS_ORDER.length - 1
      ? STATUS_ORDER[currentStatusIndex + 1]
      : null;

  const handleDragStart = (e: React.DragEvent) => {
    e.dataTransfer.setData("application/json", JSON.stringify({
      taskId: task.id,
      fromStatus: task.status,
      version: task.version,
    }));
    e.dataTransfer.effectAllowed = "move";
  };

  return (
    <div
      draggable
      onDragStart={handleDragStart}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
      onClick={() => onSelect(task)}
      className={clsx(
        "group relative flex flex-col gap-2.5 rounded-xl border border-ink-200/80 bg-white p-3.5 shadow-xs transition-all duration-150 cursor-pointer",
        "hover:border-brand-300 hover:shadow-md",
        task.is_blocked && "border-amber-300 bg-amber-50/20",
      )}
    >
      {/* Top row: Priority & Badges */}
      <div className="flex items-center justify-between gap-1.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <Pill tone={PRIORITY_TONES[task.priority]} className="capitalize text-[11px] font-semibold">
            {task.priority}
          </Pill>

          {task.is_blocked && (
            <span
              title={`Blocked by: ${task.blocked_by.map((b) => b.title).join(", ")}`}
              className="inline-flex items-center gap-1 rounded-md bg-amber-100 px-1.5 py-0.5 text-[10.5px] font-semibold text-amber-800"
            >
              <AlertTriangle size={11} className="text-amber-600" />
              Blocked ({task.blocked_by.length})
            </span>
          )}

          {task.milestone_title && (
            <span className="inline-flex max-w-[120px] truncate items-center gap-1 rounded-md bg-ink-100 px-1.5 py-0.5 text-[10.5px] text-ink-600">
              <MilestoneIcon size={10} className="shrink-0 text-ink-400" />
              <span className="truncate">{task.milestone_title}</span>
            </span>
          )}
        </div>

        <span className="font-mono text-[10px] text-ink-400 opacity-60 group-hover:opacity-100">
          v{task.version}
        </span>
      </div>

      {/* Title */}
      <h4 className="text-[13px] font-medium leading-snug text-ink-900 group-hover:text-brand-600">
        {task.title}
      </h4>

      {/* Description excerpt (if present) */}
      {task.description && (
        <p className="line-clamp-2 text-xs leading-relaxed text-ink-500">
          {task.description}
        </p>
      )}

      {/* Blocked by explanation banner if blocked */}
      {task.is_blocked && task.blocked_by.length > 0 && (
        <div className="rounded-lg bg-amber-50/80 p-2 text-[11px] text-amber-900 border border-amber-200/50">
          <p className="font-medium text-amber-800">Waiting on:</p>
          <ul className="mt-0.5 list-inside list-disc space-y-0.5 text-amber-700">
            {task.blocked_by.slice(0, 2).map((b) => (
              <li key={b.task_id} className="truncate">
                {b.title} <span className="text-[10px] opacity-75">({b.status})</span>
              </li>
            ))}
            {task.blocked_by.length > 2 && (
              <li className="text-[10px] italic">+{task.blocked_by.length - 2} more</li>
            )}
          </ul>
        </div>
      )}

      {/* Bottom row: Criteria, Comments, Assignee, Accessible Move controls */}
      <div className="mt-1 flex items-center justify-between border-t border-ink-100/70 pt-2 text-xs text-ink-500">
        <div className="flex items-center gap-2.5">
          {totalCriteria > 0 && (
            <span
              className={clsx(
                "inline-flex items-center gap-1 text-[11px]",
                doneCriteria === totalCriteria
                  ? "text-emerald-600 font-semibold"
                  : "text-ink-500",
              )}
            >
              <CheckSquare size={12} />
              {doneCriteria}/{totalCriteria}
            </span>
          )}

          {task.comment_count > 0 && (
            <span className="inline-flex items-center gap-1 text-[11px] text-ink-500">
              <MessageSquare size={12} />
              {task.comment_count}
            </span>
          )}

          <div className="flex items-center gap-1 text-[11px]">
            {task.assignee_name ? (
              <span className="flex h-5 w-5 items-center justify-center rounded-full bg-brand-100 text-[10px] font-bold text-brand-700" title={task.assignee_name}>
                {task.assignee_name.slice(0, 1).toUpperCase()}
              </span>
            ) : (
              <span className="inline-flex items-center gap-0.5 text-[10.5px] text-ink-400" title="Unassigned">
                <User size={11} />
                Unassigned
              </span>
            )}
          </div>
        </div>

        {/* Accessible Keyboard & Button Column Move Controls */}
        <div
          onClick={(e) => e.stopPropagation()}
          className={clsx(
            "flex items-center gap-1 transition-opacity",
            isHovered ? "opacity-100" : "opacity-0 md:opacity-40",
          )}
        >
          {prevStatus && (
            <button
              type="button"
              title={`Move back to ${prevStatus.replace("_", " ")}`}
              onClick={() => onMove(task.id, prevStatus, 0, task.version)}
              className="flex h-6 w-6 items-center justify-center rounded-md bg-ink-100 text-ink-600 hover:bg-ink-200 hover:text-ink-900 transition-colors"
            >
              <ArrowLeft size={12} />
            </button>
          )}

          {nextStatus && (
            <button
              type="button"
              title={`Move to ${nextStatus.replace("_", " ")}`}
              onClick={() => onMove(task.id, nextStatus, 0, task.version)}
              className="flex h-6 w-6 items-center justify-center rounded-md bg-ink-100 text-ink-600 hover:bg-brand-600 hover:text-white transition-colors"
            >
              <ArrowRight size={12} />
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
