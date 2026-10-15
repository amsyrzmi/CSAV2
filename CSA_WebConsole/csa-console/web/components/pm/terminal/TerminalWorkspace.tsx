"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Terminal as TerminalIcon,
  Plus,
  Trash2,
  RefreshCw,
  FolderCode,
  Shield,
  ChevronRight,
} from "lucide-react";
import { api, post } from "@/lib/api";
import { Button } from "@/components/ui/kit";
import { TerminalSession } from "../types";
import { RunnerStatusBadge } from "./RunnerStatusBadge";
import { CreateSessionModal } from "./CreateSessionModal";

// Dynamically import XTermCanvas to disable SSR
const XTermCanvas = dynamic(
  () => import("./XTermCanvas").then((mod) => mod.XTermCanvas),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full w-full items-center justify-center rounded-xl border border-ink-800 bg-[#0A0E17] text-xs text-ink-400">
        <RefreshCw size={14} className="mr-2 animate-spin text-brand-400" />
        Loading terminal emulator...
      </div>
    ),
  }
);

type TerminalWorkspaceProps = {
  projectId: string;
  projectName?: string;
};

export function TerminalWorkspace({ projectId, projectName }: TerminalWorkspaceProps) {
  const qc = useQueryClient();
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [terminating, setTerminating] = useState(false);

  // Fetch active terminal sessions
  const {
    data: sessions = [],
    refetch,
  } = useQuery<TerminalSession[]>({
    queryKey: ["admin-pm-terminal-sessions", projectId],
    queryFn: () => api<TerminalSession[]>(`/api/admin/pm/projects/${projectId}/terminal-sessions`),
    refetchInterval: 15000,
  });

  const activeSessions = sessions.filter((s) => s.state !== "terminated");

  // Keep activeSessionId pointing to a valid session
  const currentSessionId =
    activeSessionId && activeSessions.some((s) => s.id === activeSessionId)
      ? activeSessionId
      : activeSessions[0]?.id || null;

  const currentSession = activeSessions.find((s) => s.id === currentSessionId);

  const handleTerminateSession = async (sessionId: string) => {
    if (!confirm("Are you sure you want to terminate this terminal session and kill its active processes?")) {
      return;
    }
    setTerminating(true);
    try {
      await post(`/api/admin/pm/terminal-sessions/${sessionId}/terminate`);
      await qc.invalidateQueries({ queryKey: ["admin-pm-terminal-sessions", projectId] });
      await qc.invalidateQueries({ queryKey: ["admin-pm-runner-status"] });
    } catch {
      // ignore
    } finally {
      setTerminating(false);
    }
  };

  const handleSessionCreated = (newSession: TerminalSession) => {
    qc.invalidateQueries({ queryKey: ["admin-pm-terminal-sessions", projectId] });
    qc.invalidateQueries({ queryKey: ["admin-pm-runner-status"] });
    setActiveSessionId(newSession.id);
  };

  return (
    <div className="flex h-[calc(100vh-14rem)] flex-col gap-3">
      {/* Top Header Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-ink-100 bg-white px-4 py-2.5 shadow-xs">
        <div className="flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-brand-50 text-brand-600">
            <TerminalIcon size={16} />
          </div>
          <div>
            <div className="flex items-center gap-1.5 text-xs text-ink-500">
              <span>{projectName || "Project"}</span>
              <ChevronRight size={12} />
              <span className="font-semibold text-ink-900">Dedicated Terminal</span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-3">
          <RunnerStatusBadge />
          <Button
            size="sm"
            variant="primary"
            onClick={() => setModalOpen(true)}
            className="text-xs"
          >
            <Plus size={13} className="mr-1" /> New Terminal
          </Button>
        </div>
      </div>

      {/* Main Terminal Container */}
      <div className="relative flex flex-1 flex-col overflow-hidden rounded-2xl border border-ink-200 bg-white shadow-sm">
        {/* Tab Bar */}
        {activeSessions.length > 0 && (
          <div className="flex items-center justify-between border-b border-ink-100 bg-ink-50/70 px-3 py-1.5">
            <div className="flex items-center gap-1.5 overflow-x-auto py-0.5">
              {activeSessions.map((session) => {
                const isActive = session.id === currentSessionId;
                return (
                  <div
                    key={session.id}
                    onClick={() => setActiveSessionId(session.id)}
                    className={`group flex cursor-pointer items-center gap-2 rounded-lg px-3 py-1.5 text-xs font-medium transition-all ${
                      isActive
                        ? "border border-ink-200/80 bg-white text-brand-700 shadow-xs"
                        : "text-ink-600 hover:bg-ink-100 hover:text-ink-900"
                    }`}
                  >
                    <span className="flex items-center gap-1.5">
                      <span
                        className={`h-1.5 w-1.5 rounded-full ${
                          session.state === "active" ? "bg-emerald-500" : "bg-ink-400"
                        }`}
                      />
                      <span>{session.title}</span>
                    </span>

                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleTerminateSession(session.id);
                      }}
                      className="ml-1 rounded-sm p-0.5 text-ink-400 opacity-0 transition-opacity hover:bg-rose-50 hover:text-rose-600 group-hover:opacity-100"
                      title="Terminate session"
                    >
                      <Trash2 size={12} />
                    </button>
                  </div>
                );
              })}

              <button
                type="button"
                onClick={() => setModalOpen(true)}
                className="flex items-center gap-1 rounded-lg px-2.5 py-1 text-xs text-ink-500 hover:bg-ink-100 hover:text-ink-800"
                title="Launch another terminal"
              >
                <Plus size={13} />
              </button>
            </div>

            {currentSession && (
              <div className="flex items-center gap-2">
                <span className="font-mono text-[11px] text-ink-400">
                  PID: {currentSession.runner_pty_id || "allocating..."}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => handleTerminateSession(currentSession.id)}
                  disabled={terminating}
                  className="h-7 text-xs text-rose-600 hover:bg-rose-50 hover:text-rose-700"
                >
                  <Trash2 size={12} className="mr-1" /> Terminate
                </Button>
              </div>
            )}
          </div>
        )}

        {/* Workspace Canvas / Empty State */}
        <div className="relative flex-1 p-3">
          {activeSessions.length > 0 && currentSessionId ? (
            <XTermCanvas
              key={currentSessionId}
              sessionId={currentSessionId}
              onTerminated={() => {
                refetch();
                qc.invalidateQueries({ queryKey: ["admin-pm-runner-status"] });
              }}
            />
          ) : (
            <div className="flex h-full flex-col items-center justify-center text-center p-8">
              <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-brand-50 text-brand-600 shadow-xs mb-4">
                <TerminalIcon size={32} />
              </div>
              <h3 className="text-base font-semibold text-ink-900">
                No Active Project Terminals
              </h3>
              <p className="mt-1.5 max-w-md text-xs leading-relaxed text-ink-500">
                Launch an isolated terminal session to execute shell commands, run test suites,
                or inspect code checkouts in a dedicated project runner workspace.
              </p>

              <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
                <Button variant="primary" onClick={() => setModalOpen(true)}>
                  <Plus size={14} className="mr-1" /> Launch Project Terminal
                </Button>
              </div>

              <div className="mt-8 grid max-w-xl grid-cols-1 gap-3 sm:grid-cols-2 text-left">
                <div className="rounded-xl border border-ink-100 bg-ink-50/50 p-3">
                  <div className="flex items-center gap-2 text-xs font-semibold text-ink-800">
                    <Shield size={14} className="text-emerald-600" /> Sandboxed Execution
                  </div>
                  <p className="mt-1 text-[11.5px] text-ink-500">
                    Runs under non-root identities with sensitive app DB credentials and host secrets stripped.
                  </p>
                </div>

                <div className="rounded-xl border border-ink-100 bg-ink-50/50 p-3">
                  <div className="flex items-center gap-2 text-xs font-semibold text-ink-800">
                    <FolderCode size={14} className="text-brand-600" /> Separate Worktrees
                  </div>
                  <p className="mt-1 text-[11.5px] text-ink-500">
                    Interactive shells and autonomous agent coding runs maintain distinct, server-managed checkouts.
                  </p>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Modal */}
      <CreateSessionModal
        projectId={projectId}
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        onSessionCreated={handleSessionCreated}
      />
    </div>
  );
}
