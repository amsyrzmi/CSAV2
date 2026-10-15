"use client";

import React, { useState, useEffect, useRef, useCallback } from "react";
import { NoteDetail } from "../types";
import { MarkdownViewer } from "./MarkdownViewer";
import { Pill } from "../../ui/kit";

type NoteEditorProps = {
  note: NoteDetail;
  onSave: (payload: {
    title: string;
    body_markdown: string;
    folder: string;
    tags: string[];
    version: number;
  }) => Promise<NoteDetail>;
  onOpenRevisions: () => void;
  onOpenTemplates: () => void;
  onNavigateWikiLink: (noteTitle: string) => void;
};

export function NoteEditor({
  note,
  onSave,
  onOpenRevisions,
  onOpenTemplates,
  onNavigateWikiLink,
}: NoteEditorProps) {
  const [title, setTitle] = useState(note.title);
  const [folder, setFolder] = useState(note.folder);
  const [tags, setTags] = useState<string[]>(note.tags || []);
  const [tagInput, setTagInput] = useState("");
  const [body, setBody] = useState(note.body_markdown || "");
  const [mode, setMode] = useState<"split" | "edit" | "preview">("split");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [conflictError, setConflictError] = useState<string | null>(null);

  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Execute save
  const doSave = useCallback(async () => {
    if (saving) return;
    setSaving(true);
    setConflictError(null);
    try {
      await onSave({
        title,
        body_markdown: body,
        folder,
        tags,
        version: note.version,
      });
      setDirty(false);
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes("409") || msg.toLowerCase().includes("conflict")) {
        setConflictError("This note was modified concurrently by another process. Please review and refresh.");
      } else {
        alert(`Failed to save note: ${msg}`);
      }
    } finally {
      setSaving(false);
    }
  }, [body, folder, note.version, onSave, saving, tags, title]);

  // Debounced autosave (1500ms after edits)
  useEffect(() => {
    if (!dirty) return;
    const timer = setTimeout(() => {
      doSave();
    }, 1500);
    return () => clearTimeout(timer);
  }, [dirty, doSave]);

  const handleBodyChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setBody(e.target.value);
    setDirty(true);
  };

  const insertSnippet = (prefix: string, suffix = "") => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const selected = body.substring(start, end);
    const replacement = `${prefix}${selected || "text"}${suffix}`;
    const newBody = body.substring(0, start) + replacement + body.substring(end);
    setBody(newBody);
    setDirty(true);
    setTimeout(() => {
      textarea.focus();
      textarea.setSelectionRange(start + prefix.length, start + prefix.length + (selected ? selected.length : 4));
    }, 10);
  };

  const handleAddTag = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      const clean = tagInput.trim().replace(/^#/, "").toLowerCase();
      if (clean && !tags.includes(clean)) {
        setTags([...tags, clean]);
        setDirty(true);
      }
      setTagInput("");
    }
  };

  const handleRemoveTag = (tagToRemove: string) => {
    setTags(tags.filter((t) => t !== tagToRemove));
    setDirty(true);
  };

  return (
    <div className="flex-1 flex flex-col h-full bg-white overflow-hidden">
      {/* Conflict Alert Banner */}
      {conflictError && (
        <div className="px-4 py-2.5 bg-amber-50 border-b border-amber-200 text-amber-900 text-xs flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className="text-amber-600 font-bold">⚠️ Concurrency Conflict:</span>
            <span>{conflictError}</span>
          </div>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="px-2.5 py-1 text-xs font-semibold bg-amber-600 text-white rounded hover:bg-amber-700 transition-colors"
          >
            Refresh Latest Note
          </button>
        </div>
      )}

      {/* Editor Header */}
      <div className="p-4 border-b border-slate-100 bg-white">
        {/* Title Input */}
        <div className="flex items-center gap-3">
          <input
            type="text"
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              setDirty(true);
            }}
            placeholder="Note Title..."
            className="flex-1 text-xl font-bold text-slate-900 border-none outline-none focus:ring-0 placeholder:text-slate-300 bg-transparent"
          />

          <div className="flex items-center gap-2">
            {/* Dirty / Autosave status badge */}
            <span className="text-[11px] text-slate-400 font-mono flex items-center gap-1.5 mr-1">
              <span className={`w-1.5 h-1.5 rounded-full ${saving ? "bg-amber-500 animate-ping" : dirty ? "bg-amber-400" : "bg-emerald-500"}`} />
              {saving ? "Saving..." : dirty ? "Unsaved changes" : `Saved (v${note.version})`}
            </span>

            {/* Revisions history button */}
            <button
              type="button"
              onClick={onOpenRevisions}
              className="px-2.5 py-1 text-xs font-medium text-slate-600 hover:text-slate-900 bg-slate-100 hover:bg-slate-200/80 rounded-lg transition-colors flex items-center gap-1"
              title="Inspect revision history"
            >
              <span>🕒</span>
              <span>Revisions</span>
              <span className="text-[10px] text-slate-400 font-mono">#{note.revision_number}</span>
            </button>

            {/* Manual Save Button */}
            <button
              type="button"
              disabled={saving || !dirty}
              onClick={doSave}
              className="px-3 py-1 text-xs font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg shadow-sm transition-colors disabled:opacity-40"
            >
              {saving ? "Saving..." : "Save"}
            </button>
          </div>
        </div>

        {/* Metadata Row: Folder & Tags */}
        <div className="flex items-center gap-4 mt-2.5 pt-2 border-t border-slate-50 text-xs">
          {/* Folder input */}
          <div className="flex items-center gap-1.5 text-slate-500">
            <span className="text-slate-400">📁 Folder:</span>
            <input
              type="text"
              value={folder}
              onChange={(e) => {
                setFolder(e.target.value);
                setDirty(true);
              }}
              placeholder="/specs"
              className="px-2 py-0.5 rounded border border-slate-200 text-xs font-mono text-slate-700 w-32 focus:w-48 focus:border-blue-500 focus:outline-none transition-all"
            />
          </div>

          {/* Tags list */}
          <div className="flex items-center gap-1.5 flex-1 overflow-x-auto">
            <span className="text-slate-400">🏷️ Tags:</span>
            {tags.map((tg) => (
              <Pill key={tg} tone="brand" className="flex items-center gap-1 text-[11px]">
                <span>#{tg}</span>
                <button
                  type="button"
                  onClick={() => handleRemoveTag(tg)}
                  className="hover:text-blue-800 font-bold ml-0.5"
                >
                  ×
                </button>
              </Pill>
            ))}
            <input
              type="text"
              value={tagInput}
              onChange={(e) => setTagInput(e.target.value)}
              onKeyDown={handleAddTag}
              placeholder="+ add tag..."
              className="px-1.5 py-0.5 text-[11px] text-slate-600 border border-dashed border-slate-200 rounded w-20 focus:w-28 focus:border-blue-400 focus:outline-none transition-all"
            />
          </div>
        </div>
      </div>

      {/* Editor Formatting Toolbar */}
      <div className="px-4 py-2 border-b border-slate-100 bg-slate-50/60 flex items-center justify-between">
        <div className="flex items-center gap-1 text-xs">
          <button
            type="button"
            onClick={() => insertSnippet("## ", "")}
            className="px-2 py-1 rounded text-slate-600 hover:text-slate-900 hover:bg-slate-200 font-bold"
            title="Heading 2"
          >
            H2
          </button>
          <button
            type="button"
            onClick={() => insertSnippet("### ", "")}
            className="px-2 py-1 rounded text-slate-600 hover:text-slate-900 hover:bg-slate-200 font-semibold"
            title="Heading 3"
          >
            H3
          </button>
          <span className="w-px h-4 bg-slate-200 mx-1" />
          <button
            type="button"
            onClick={() => insertSnippet("**", "**")}
            className="px-2 py-1 rounded text-slate-600 hover:text-slate-900 hover:bg-slate-200 font-bold"
            title="Bold"
          >
            B
          </button>
          <button
            type="button"
            onClick={() => insertSnippet("*", "*")}
            className="px-2 py-1 rounded text-slate-600 hover:text-slate-900 hover:bg-slate-200 italic"
            title="Italic"
          >
            I
          </button>
          <button
            type="button"
            onClick={() => insertSnippet("`", "`")}
            className="px-2 py-1 rounded text-slate-600 hover:text-slate-900 hover:bg-slate-200 font-mono text-xs"
            title="Inline Code"
          >
            `code`
          </button>
          <button
            type="button"
            onClick={() => insertSnippet("```typescript\n", "\n```")}
            className="px-2 py-1 rounded text-slate-600 hover:text-slate-900 hover:bg-slate-200 font-mono text-xs"
            title="Code Block"
          >
            ```
          </button>
          <span className="w-px h-4 bg-slate-200 mx-1" />
          <button
            type="button"
            onClick={() => insertSnippet("- [ ] ", "")}
            className="px-2 py-1 rounded text-slate-600 hover:text-slate-900 hover:bg-slate-200"
            title="Checklist Item"
          >
            ☑ Checklist
          </button>
          <button
            type="button"
            onClick={() => insertSnippet("> ", "")}
            className="px-2 py-1 rounded text-slate-600 hover:text-slate-900 hover:bg-slate-200 italic"
            title="Quote"
          >
            &quot; Quote
          </button>
          <span className="w-px h-4 bg-slate-200 mx-1" />
          {/* Wiki-link helper button */}
          <button
            type="button"
            onClick={() => insertSnippet("[[", "]]")}
            className="px-2 py-1 rounded text-blue-700 bg-blue-50 hover:bg-blue-100 font-medium border border-blue-200"
            title="Insert [[WikiLink]]"
          >
            🔗 [[WikiLink]]
          </button>
          <button
            type="button"
            onClick={onOpenTemplates}
            className="px-2 py-1 rounded text-purple-700 bg-purple-50 hover:bg-purple-100 font-medium border border-purple-200"
            title="Insert Template"
          >
            📋 Template
          </button>
        </div>

        {/* View Mode Toggle: Split | Edit | Preview */}
        <div className="flex items-center rounded-lg border border-slate-200 bg-white p-0.5 text-xs">
          <button
            type="button"
            onClick={() => setMode("edit")}
            className={`px-2.5 py-1 rounded-md transition-colors ${
              mode === "edit" ? "bg-slate-100 text-slate-900 font-medium" : "text-slate-500 hover:text-slate-800"
            }`}
          >
            Edit
          </button>
          <button
            type="button"
            onClick={() => setMode("split")}
            className={`px-2.5 py-1 rounded-md transition-colors ${
              mode === "split" ? "bg-slate-100 text-slate-900 font-medium" : "text-slate-500 hover:text-slate-800"
            }`}
          >
            Split
          </button>
          <button
            type="button"
            onClick={() => setMode("preview")}
            className={`px-2.5 py-1 rounded-md transition-colors ${
              mode === "preview" ? "bg-slate-100 text-slate-900 font-medium" : "text-slate-500 hover:text-slate-800"
            }`}
          >
            Preview
          </button>
        </div>
      </div>

      {/* Editor Content Body */}
      <div className="flex-1 flex overflow-hidden">
        {/* Markdown Source Textarea */}
        {(mode === "edit" || mode === "split") && (
          <div className={`flex-1 flex flex-col h-full bg-slate-50/20 ${mode === "split" ? "border-r border-slate-200" : ""}`}>
            <textarea
              ref={textareaRef}
              value={body}
              onChange={handleBodyChange}
              placeholder="Write your markdown note here... Use [[WikiLinks]] to connect notes together."
              className="flex-1 p-5 font-mono text-xs text-slate-800 bg-transparent resize-none border-none outline-none leading-relaxed focus:ring-0 placeholder:text-slate-300"
            />
          </div>
        )}

        {/* Preview Pane */}
        {(mode === "preview" || mode === "split") && (
          <div className="flex-1 overflow-y-auto p-6 bg-white">
            <MarkdownViewer
              content={body}
              onNavigateNote={onNavigateWikiLink}
            />
          </div>
        )}
      </div>
    </div>
  );
}
