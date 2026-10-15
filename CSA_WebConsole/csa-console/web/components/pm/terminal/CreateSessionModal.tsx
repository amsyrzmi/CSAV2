"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Terminal, Plus, FolderKanban } from "lucide-react";
import { api, post } from "@/lib/api";
import { Banner, Button } from "@/components/ui/kit";
import { input, label } from "@/components/ui/input";
import { ProjectWorkspace, TerminalSession } from "../types";

type CreateSessionModalProps = {
  projectId: string;
  isOpen: boolean;
  onClose: () => void;
  onSessionCreated: (session: TerminalSession) => void;
};

export function CreateSessionModal({
  projectId,
  isOpen,
  onClose,
  onSessionCreated,
}: CreateSessionModalProps) {
  const [title, setTitle] = useState("Terminal");
  const [workspaceId, setWorkspaceId] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  // Load existing workspaces for this project
  const { data: workspaces, isLoading: loadingWorkspaces } = useQuery<ProjectWorkspace[]>({
    queryKey: ["admin-pm-workspaces", projectId],
    queryFn: () => api<ProjectWorkspace[]>(`/api/admin/pm/projects/${projectId}/workspaces`),
    enabled: isOpen,
  });

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");

    try {
      let targetWorkspaceId = workspaceId;

      // If no workspace selected, or none exist, provision one automatically
      if (!targetWorkspaceId) {
        if (workspaces && workspaces.length > 0) {
          targetWorkspaceId = workspaces[0].id;
        } else {
          const newWs = await post<ProjectWorkspace>(
            `/api/admin/pm/projects/${projectId}/workspaces`,
            { workspace_type: "human_terminal" }
          );
          targetWorkspaceId = newWs.id;
        }
      }

      // Create the terminal session
      const newSession = await post<TerminalSession>(
        `/api/admin/pm/projects/${projectId}/terminal-sessions`,
        {
          workspace_id: targetWorkspaceId,
          title: title.trim() || "Terminal",
        }
      );

      onSessionCreated(newSession);
      onClose();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to launch terminal session");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-ink-950/40 backdrop-blur-xs p-4 animate-in fade-in duration-150">
      <div className="w-full max-w-md rounded-2xl border border-ink-100 bg-white p-6 shadow-xl">
        <div className="flex items-center gap-3 border-b border-ink-100 pb-4">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-brand-50 text-brand-600">
            <Terminal size={20} />
          </div>
          <div>
            <h3 className="text-base font-semibold text-ink-900">Launch Project Terminal</h3>
            <p className="text-xs text-ink-500">
              Spawns an interactive shell inside the project sandbox runner.
            </p>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="mt-5 space-y-4">
          {error && <Banner tone="error">{error}</Banner>}

          <div>
            <label className={label}>Terminal Tab Title</label>
            <input
              type="text"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Main Shell, Tests, Debug"
              required
              maxLength={40}
              className={input}
            />
          </div>

          <div>
            <label className={label}>Target Workspace</label>
            {loadingWorkspaces ? (
              <p className="text-xs text-ink-400">Loading project workspaces...</p>
            ) : workspaces && workspaces.length > 0 ? (
              <select
                value={workspaceId || workspaces[0]?.id || ""}
                onChange={(e) => setWorkspaceId(e.target.value)}
                className={input}
              >
                {workspaces.map((ws) => (
                  <option key={ws.id} value={ws.id}>
                    {ws.workspace_type === "human_terminal" ? "Interactive Workspace" : "Agent Coding Worktree"} ({ws.id.slice(0, 8)})
                  </option>
                ))}
              </select>
            ) : (
              <div className="flex items-center gap-2 rounded-lg border border-ink-200 bg-ink-50/60 p-2.5 text-xs text-ink-600">
                <FolderKanban size={15} className="text-brand-600 shrink-0" />
                <span>A new isolated workspace directory will be automatically provisioned.</span>
              </div>
            )}
          </div>

          <div className="flex justify-end gap-2.5 pt-3 border-t border-ink-100">
            <Button variant="secondary" onClick={onClose} disabled={loading} type="button">
              Cancel
            </Button>
            <Button variant="primary" type="submit" disabled={loading}>
              <Plus size={14} className="mr-1" />
              {loading ? "Launching..." : "Launch Terminal"}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
