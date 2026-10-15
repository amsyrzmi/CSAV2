"use client";

import React, { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Note,
  NoteDetail,
  FolderTreeNode,
  NoteGraphData,
  StarterTemplate,
  NoteSearchResult,
} from "../types";
import { FolderTree } from "./FolderTree";
import { NoteEditor } from "./NoteEditor";
import { BacklinksPanel } from "./BacklinksPanel";
import { TemplatePickerModal } from "./TemplatePickerModal";
import { RevisionHistoryModal } from "./RevisionHistoryModal";
import { api, del, patch, post } from "../../../lib/api";

type NotesWorkspaceProps = {
  projectId: string;
};

export function NotesWorkspace({ projectId }: NotesWorkspaceProps) {
  const qc = useQueryClient();

  // Selection & UI state
  const [activeNoteId, setActiveNoteId] = useState<string | null>(null);
  const [activeFolder, setActiveFolder] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");

  // Modals
  const [isTemplateModalOpen, setIsTemplateModalOpen] = useState(false);
  const [isRevisionModalOpen, setIsRevisionModalOpen] = useState(false);

  // Queries
  const { data: notesData, isLoading: loadingNotes } = useQuery({
    queryKey: ["admin-pm-notes", projectId],
    queryFn: () => api<{ items: Note[] }>(`/api/admin/pm/projects/${projectId}/notes`),
  });

  const { data: tree } = useQuery({
    queryKey: ["admin-pm-notes-tree", projectId],
    queryFn: () => api<FolderTreeNode>(`/api/admin/pm/projects/${projectId}/notes/tree`),
  });

  const { data: templates } = useQuery({
    queryKey: ["admin-pm-notes-templates", projectId],
    queryFn: () => api<StarterTemplate[]>(`/api/admin/pm/projects/${projectId}/notes/templates`),
  });

  const notes = notesData?.items || [];
  const currentNoteId = activeNoteId || (notes[0]?.id ?? null);

  const { data: activeNoteDetail, isLoading: loadingDetail } = useQuery({
    queryKey: ["admin-pm-note-detail", projectId, currentNoteId],
    queryFn: () => api<NoteDetail>(`/api/admin/pm/projects/${projectId}/notes/${currentNoteId}`),
    enabled: !!currentNoteId,
  });

  const { data: graphData } = useQuery({
    queryKey: ["admin-pm-note-graph", projectId, currentNoteId],
    queryFn: () => api<NoteGraphData>(`/api/admin/pm/projects/${projectId}/notes/graph?note_id=${currentNoteId}`),
    enabled: !!currentNoteId,
  });

  const { data: searchResults, isFetching: isSearching } = useQuery({
    queryKey: ["admin-pm-notes-search", projectId, searchQuery.trim()],
    queryFn: () =>
      api<NoteSearchResult[]>(
        `/api/admin/pm/projects/${projectId}/notes/search?q=${encodeURIComponent(searchQuery.trim())}`
      ),
    enabled: searchQuery.trim().length >= 1,
  });

  // Create Note
  const handleCreateNote = async (initialData?: {
    title?: string;
    folder?: string;
    body_markdown?: string;
    tags?: string[];
  }) => {
    const defaultTitle = initialData?.title || `Untitled Note ${notes.length + 1}`;
    try {
      const newNote = await post<NoteDetail>(`/api/admin/pm/projects/${projectId}/notes`, {
        title: defaultTitle,
        folder: initialData?.folder || activeFolder || "/",
        body_markdown: initialData?.body_markdown || "",
        tags: initialData?.tags || [],
      });

      await qc.invalidateQueries({ queryKey: ["admin-pm-notes", projectId] });
      await qc.invalidateQueries({ queryKey: ["admin-pm-notes-tree", projectId] });
      setActiveNoteId(newNote.id);
    } catch (err: unknown) {
      alert(`Failed to create note: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  // Save Note updates
  const handleSaveNote = async (payload: {
    title: string;
    body_markdown: string;
    folder: string;
    tags: string[];
    version: number;
  }): Promise<NoteDetail> => {
    if (!currentNoteId) throw new Error("No active note selected");

    const updated = await patch<NoteDetail>(
      `/api/admin/pm/projects/${projectId}/notes/${currentNoteId}`,
      {
        title: payload.title,
        body_markdown: payload.body_markdown,
        folder: payload.folder,
        tags: payload.tags,
        version: payload.version,
      }
    );

    // Invalidate queries to refresh lists and detail
    await qc.invalidateQueries({ queryKey: ["admin-pm-notes", projectId] });
    await qc.invalidateQueries({ queryKey: ["admin-pm-notes-tree", projectId] });
    await qc.invalidateQueries({ queryKey: ["admin-pm-note-detail", projectId, currentNoteId] });
    await qc.invalidateQueries({ queryKey: ["admin-pm-note-graph", projectId, currentNoteId] });

    return updated;
  };

  // Delete Note
  const handleDeleteNote = async (noteId: string) => {
    if (!confirm("Are you sure you want to delete this note? This action cannot be undone.")) {
      return;
    }
    try {
      await del(`/api/admin/pm/projects/${projectId}/notes/${noteId}`);
      await qc.invalidateQueries({ queryKey: ["admin-pm-notes", projectId] });
      await qc.invalidateQueries({ queryKey: ["admin-pm-notes-tree", projectId] });
      if (currentNoteId === noteId) {
        const remaining = notes.filter((n) => n.id !== noteId);
        setActiveNoteId(remaining[0]?.id || null);
      }
    } catch (err: unknown) {
      alert(`Failed to delete note: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  // Restore Revision
  const handleRestoreRevision = async (revisionId: string) => {
    if (!currentNoteId) return;
    await post<NoteDetail>(
      `/api/admin/pm/projects/${projectId}/notes/${currentNoteId}/restore/${revisionId}`,
      {}
    );
    await qc.invalidateQueries({ queryKey: ["admin-pm-notes", projectId] });
    await qc.invalidateQueries({ queryKey: ["admin-pm-note-detail", projectId, currentNoteId] });
  };

  // WikiLink Navigation
  const handleNavigateWikiLink = (noteTitleOrId: string) => {
    const byId = notes.find((n) => n.id === noteTitleOrId);
    if (byId) {
      setActiveNoteId(byId.id);
      return;
    }
    const byTitle = notes.find((n) => n.title.toLowerCase() === noteTitleOrId.toLowerCase());
    if (byTitle) {
      setActiveNoteId(byTitle.id);
    } else {
      if (confirm(`Note "${noteTitleOrId}" does not exist yet. Would you like to create it now?`)) {
        handleCreateNote({ title: noteTitleOrId, folder: activeFolder || "/" });
      }
    }
  };

  // Export Notes
  const handleExport = async () => {
    try {
      const exp = await api<{ notes: NoteDetail[]; project_id: string }>(
        `/api/admin/pm/projects/${projectId}/notes/export`
      );
      const blob = new Blob([JSON.stringify(exp, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `project-${projectId}-notes-export.json`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      alert(`Export failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  // Import Notes
  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const text = await file.text();
      const parsed = JSON.parse(text);
      const noteList = Array.isArray(parsed) ? parsed : parsed.notes;
      if (!Array.isArray(noteList)) {
        throw new Error("Invalid format: expected array of notes or { notes: [...] }");
      }
      const res = await post<{ imported: number; failed: number }>(
        `/api/admin/pm/projects/${projectId}/notes/import`,
        { notes: noteList }
      );
      alert(`Import completed: ${res.imported} imported, ${res.failed} failed.`);
      await qc.invalidateQueries({ queryKey: ["admin-pm-notes", projectId] });
      await qc.invalidateQueries({ queryKey: ["admin-pm-notes-tree", projectId] });
    } catch (err: unknown) {
      alert(`Import failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      e.target.value = "";
    }
  };

  // Filter notes for list view
  const filteredNotes = notes.filter((n) => {
    if (activeFolder) {
      if (activeFolder === "/") return true;
      return n.folder === activeFolder || n.folder.startsWith(`${activeFolder.replace(/\/$/, "")}/`);
    }
    return true;
  });

  return (
    <div className="flex flex-col h-[calc(100vh-140px)] bg-slate-100 rounded-2xl border border-slate-200/80 overflow-hidden shadow-xs">
      {/* Top Workspace Navigation Bar */}
      <div className="px-4 py-2.5 bg-white border-b border-slate-200 flex items-center justify-between gap-4">
        {/* Left: Search input */}
        <div className="relative w-80">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search notes with full-text query..."
            className="w-full pl-8 pr-3 py-1.5 text-xs rounded-xl border border-slate-200 bg-slate-50 focus:bg-white focus:border-blue-500 focus:outline-none transition-all placeholder:text-slate-400"
          />
          <span className="absolute left-2.5 top-2 text-xs text-slate-400">🔍</span>

          {/* Search Dropdown Results */}
          {searchQuery.trim() && (
            <div className="absolute top-full left-0 right-0 mt-1.5 bg-white rounded-xl shadow-xl border border-slate-200 z-50 max-h-72 overflow-y-auto divide-y divide-slate-100">
              {isSearching ? (
                <div className="p-3 text-xs text-slate-400 text-center">Searching...</div>
              ) : !searchResults || searchResults.length === 0 ? (
                <div className="p-3 text-xs text-slate-400 text-center">No notes matched &quot;{searchQuery}&quot;</div>
              ) : (
                searchResults.map((res) => (
                  <button
                    key={res.id}
                    type="button"
                    onClick={() => {
                      setActiveNoteId(res.id);
                      setSearchQuery("");
                    }}
                    className="w-full text-left p-2.5 hover:bg-blue-50/50 transition-colors"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs font-semibold text-slate-900 truncate">{res.title}</span>
                      <span className="text-[10px] text-slate-400 font-mono">{res.folder}</span>
                    </div>
                    {res.snippet && (
                      <div
                        className="text-[11px] text-slate-500 line-clamp-2 mt-1"
                        dangerouslySetInnerHTML={{ __html: res.snippet }}
                      />
                    )}
                  </button>
                ))
              )}
            </div>
          )}
        </div>

        {/* Center: Folder / Filter Breadcrumb */}
        <div className="flex items-center gap-2 text-xs text-slate-600">
          <span className="text-slate-400">Current Scope:</span>
          <span className="font-mono text-slate-800 bg-slate-100 px-2 py-0.5 rounded">
            {activeFolder || "All Folders"}
          </span>
          {activeFolder && (
            <button
              type="button"
              onClick={() => setActiveFolder(null)}
              className="text-blue-600 hover:text-blue-800 font-medium ml-1"
            >
              Clear
            </button>
          )}
        </div>

        {/* Right: Actions (Template, New Note, Export, Import) */}
        <div className="flex items-center gap-2">
          {/* Template Picker */}
          <button
            type="button"
            onClick={() => setIsTemplateModalOpen(true)}
            className="px-3 py-1.5 text-xs font-medium text-slate-700 bg-slate-100 hover:bg-slate-200/80 rounded-xl transition-colors flex items-center gap-1.5"
            title="Create note from template"
          >
            <span>📋</span>
            <span>Templates</span>
          </button>

          {/* New Note */}
          <button
            type="button"
            onClick={() => handleCreateNote()}
            className="px-3.5 py-1.5 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-xl shadow-xs transition-colors flex items-center gap-1.5"
          >
            <span>+</span>
            <span>New Note</span>
          </button>

          {/* Export */}
          <button
            type="button"
            onClick={handleExport}
            className="p-1.5 text-slate-500 hover:text-slate-800 hover:bg-slate-100 rounded-lg transition-colors"
            title="Export project notes (JSON)"
          >
            📥
          </button>

          {/* Import */}
          <label className="p-1.5 text-slate-500 hover:text-slate-800 hover:bg-slate-100 rounded-lg cursor-pointer transition-colors" title="Import notes (JSON)">
            📤
            <input type="file" accept=".json" onChange={handleImport} className="hidden" />
          </label>
        </div>
      </div>

      {/* 3-Pane Body */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Sidebar: Folder Tree & Note List */}
        <div className="w-64 border-r border-slate-200 bg-slate-50/50 flex flex-col h-full overflow-hidden">
          {/* Section 1: Folder Tree */}
          <div className="p-3 border-b border-slate-200/80 bg-white">
            <div className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1">
              Folders
            </div>
            <div className="max-h-48 overflow-y-auto">
              <FolderTree
                tree={tree || null}
                activeFolder={activeFolder}
                activeNoteId={currentNoteId}
                onSelectFolder={setActiveFolder}
                onSelectNote={setActiveNoteId}
              />
            </div>
          </div>

          {/* Section 2: Notes List in selected folder */}
          <div className="flex-1 flex flex-col overflow-hidden bg-white/60">
            <div className="px-3 py-2 border-b border-slate-100 flex items-center justify-between text-xs text-slate-500">
              <span className="font-semibold text-slate-700">Notes ({filteredNotes.length})</span>
            </div>

            <div className="flex-1 overflow-y-auto p-2 space-y-1">
              {loadingNotes ? (
                <div className="text-xs text-slate-400 text-center py-6">Loading notes...</div>
              ) : filteredNotes.length === 0 ? (
                <div className="text-xs text-slate-400 text-center py-6">No notes found.</div>
              ) : (
                filteredNotes.map((n) => {
                  const isActive = currentNoteId === n.id;
                  return (
                    <div
                      key={n.id}
                      onClick={() => setActiveNoteId(n.id)}
                      className={`group flex flex-col p-2.5 rounded-xl cursor-pointer border transition-all ${
                        isActive
                          ? "bg-white border-blue-500 shadow-xs ring-1 ring-blue-500/10"
                          : "bg-white/80 border-slate-200 hover:border-slate-300 hover:bg-white"
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-xs font-semibold text-slate-900 truncate flex-1">
                          {n.title}
                        </span>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteNote(n.id);
                          }}
                          className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-red-600 p-0.5 text-xs transition-opacity"
                          title="Delete note"
                        >
                          ✕
                        </button>
                      </div>

                      <div className="flex items-center gap-1.5 mt-1.5 text-[10px] text-slate-400">
                        <span className="truncate max-w-[100px]">{n.folder}</span>
                        <span>•</span>
                        <span>v{n.version}</span>
                        {n.backlink_count > 0 && (
                          <span className="ml-auto font-mono text-blue-600 bg-blue-50 px-1 rounded">
                            🔗 {n.backlink_count}
                          </span>
                        )}
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>

        {/* Center: Editor / Preview */}
        <div className="flex-1 flex flex-col h-full overflow-hidden bg-white">
          {loadingDetail ? (
            <div className="flex-1 flex items-center justify-center text-xs text-slate-400">
              Loading note content...
            </div>
          ) : activeNoteDetail ? (
            <NoteEditor
              key={`${activeNoteDetail.id}-${activeNoteDetail.version}`}
              note={activeNoteDetail}
              onSave={handleSaveNote}
              onOpenRevisions={() => setIsRevisionModalOpen(true)}
              onOpenTemplates={() => setIsTemplateModalOpen(true)}
              onNavigateWikiLink={handleNavigateWikiLink}
            />
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center p-8 text-center text-slate-400">
              <span className="text-4xl mb-2">📝</span>
              <h3 className="text-sm font-semibold text-slate-700">No Note Selected</h3>
              <p className="text-xs text-slate-400 mt-1 max-w-sm">
                Select an existing note from the folder hierarchy or create a new note using starter templates.
              </p>
              <button
                type="button"
                onClick={() => handleCreateNote()}
                className="mt-4 px-4 py-2 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-xl shadow-xs transition-colors"
              >
                Create First Note
              </button>
            </div>
          )}
        </div>

        {/* Right Sidebar: Backlinks, Linked Tasks & Mini Graph */}
        {activeNoteDetail && (
          <BacklinksPanel
            note={activeNoteDetail}
            graphData={graphData || null}
            onNavigateNote={handleNavigateWikiLink}
          />
        )}
      </div>

      {/* Modals */}
      <TemplatePickerModal
        isOpen={isTemplateModalOpen}
        onClose={() => setIsTemplateModalOpen(false)}
        templates={templates || []}
        onSelectTemplate={(tmpl) => {
          handleCreateNote({
            title: tmpl.name,
            folder: tmpl.folder,
            body_markdown: tmpl.body,
            tags: tmpl.tags,
          });
        }}
      />

      {activeNoteDetail && (
        <RevisionHistoryModal
          isOpen={isRevisionModalOpen}
          onClose={() => setIsRevisionModalOpen(false)}
          note={activeNoteDetail}
          onRestoreRevision={handleRestoreRevision}
        />
      )}
    </div>
  );
}
