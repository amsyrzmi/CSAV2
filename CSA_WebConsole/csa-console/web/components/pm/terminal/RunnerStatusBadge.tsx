"use client";

import { useQuery } from "@tanstack/react-query";
import { Server, AlertTriangle, ShieldCheck } from "lucide-react";
import { api } from "@/lib/api";
import { RunnerStatus } from "../types";

export function RunnerStatusBadge() {
  const { data: status, isLoading } = useQuery<RunnerStatus>({
    queryKey: ["admin-pm-runner-status"],
    queryFn: () => api<RunnerStatus>("/api/admin/pm/runner/status"),
    refetchInterval: 10000,
  });

  if (isLoading || !status) {
    return (
      <div className="flex items-center gap-1.5 rounded-full border border-ink-200 bg-ink-50 px-2.5 py-1 text-xs text-ink-500">
        <Server size={12} className="animate-pulse" />
        <span>Checking runner...</span>
      </div>
    );
  }

  const isReady = status.status === "ready";
  const isDegraded = status.status === "degraded";

  return (
    <div
      className={`flex items-center gap-2 rounded-full border px-3 py-1 text-xs font-medium shadow-xs transition-colors ${
        isReady
          ? "border-emerald-200 bg-emerald-50/80 text-emerald-800"
          : isDegraded
          ? "border-amber-200 bg-amber-50/80 text-amber-800"
          : "border-rose-200 bg-rose-50/80 text-rose-800"
      }`}
      title={`Runner ID: ${status.runner_id}\nType: ${status.runner_type}\nWorkspaces: ${status.workspaces_dir}`}
    >
      <span className="relative flex h-2 w-2">
        {isReady && (
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
        )}
        <span
          className={`relative inline-flex h-2 w-2 rounded-full ${
            isReady ? "bg-emerald-500" : isDegraded ? "bg-amber-500" : "bg-rose-500"
          }`}
        />
      </span>

      <span className="flex items-center gap-1">
        {isReady ? (
          <ShieldCheck size={13} className="text-emerald-600" />
        ) : (
          <AlertTriangle size={13} className="text-amber-600" />
        )}
        <span>
          {isReady
            ? "Isolated Runner Active"
            : isDegraded
            ? "Runner Degraded"
            : "Runner Offline"}
        </span>
      </span>

      {status.active_ptys > 0 && (
        <span className="ml-0.5 rounded-full bg-emerald-200/60 px-1.5 py-0.2 font-mono text-[10px] text-emerald-900">
          {status.active_ptys} active
        </span>
      )}
    </div>
  );
}
