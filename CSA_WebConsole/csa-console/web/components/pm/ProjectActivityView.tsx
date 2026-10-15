"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  CheckCircle2,
  Clock,
  Layers,
  Milestone as MilestoneIcon,
  RefreshCw,
  User,
} from "lucide-react";
import { api } from "@/lib/api";
import { Skeleton } from "@/components/ui/kit";
import type { ActivityItem } from "./types";

export function ProjectActivityView({ projectId }: { projectId: string }) {
  const { data, isLoading, refetch } = useQuery({
    queryKey: ["admin-pm-activity", projectId],
    queryFn: () =>
      api<{ items: ActivityItem[]; next_cursor: number | null }>(
        `/api/admin/pm/projects/${projectId}/activity?limit=50`,
      ),
    enabled: !!projectId,
  });

  const items = data?.items || [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-ink-900 flex items-center gap-2">
            <Activity size={16} className="text-brand-600" />
            Project Audit & Activity Log
          </h3>
          <p className="mt-0.5 text-xs text-ink-500">
            Immutable, audited trail of project lifecycle, tasks, milestones, and status changes.
          </p>
        </div>
        <button
          type="button"
          onClick={() => refetch()}
          className="flex h-8 w-8 items-center justify-center rounded-lg border border-ink-200 text-ink-500 hover:bg-ink-50 hover:text-ink-800 transition-colors"
          title="Refresh activity"
        >
          <RefreshCw size={13} />
        </button>
      </div>

      {isLoading ? (
        <div className="space-y-3 rounded-2xl border border-ink-200 bg-white p-5">
          <Skeleton className="h-6 w-3/4 rounded-md" />
          <Skeleton className="h-6 w-1/2 rounded-md" />
          <Skeleton className="h-6 w-2/3 rounded-md" />
        </div>
      ) : items.length === 0 ? (
        <div className="rounded-2xl border border-dashed border-ink-200 p-8 text-center text-xs text-ink-400">
          No audit events recorded for this project yet.
        </div>
      ) : (
        <div className="rounded-2xl border border-ink-200 bg-white shadow-xs divide-y divide-ink-100 overflow-hidden">
          {items.map((item) => (
            <div key={item.id} className="p-4 flex items-start justify-between gap-4">
              <div className="flex items-start gap-3">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-ink-100 text-ink-600 mt-0.5">
                  {item.action.startsWith("pm.task") ? (
                    <CheckCircle2 size={15} />
                  ) : item.action.startsWith("pm.milestone") ? (
                    <MilestoneIcon size={15} />
                  ) : (
                    <Layers size={15} />
                  )}
                </span>
                <div>
                  <div className="flex items-center gap-2">
                    <p className="text-[13px] font-medium text-ink-900">{item.summary}</p>
                    <span className="rounded bg-ink-100 px-1.5 py-0.5 font-mono text-[10px] text-ink-600">
                      {item.action}
                    </span>
                  </div>
                  <div className="mt-1 flex items-center gap-3 text-xs text-ink-400">
                    <span className="flex items-center gap-1 text-ink-600">
                      <User size={12} />
                      {item.actor_name}
                    </span>
                    <span>•</span>
                    <span className="flex items-center gap-1">
                      <Clock size={12} />
                      {new Date(item.at).toLocaleString()}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
