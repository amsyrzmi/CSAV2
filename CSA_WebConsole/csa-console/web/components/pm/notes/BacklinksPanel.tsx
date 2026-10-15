"use client";

import React from "react";
import { NoteDetail, NoteGraphData } from "../types";
import { NeighborhoodGraph } from "./NeighborhoodGraph";
import { Pill } from "../../ui/kit";

type BacklinksPanelProps = {
  note: NoteDetail;
  graphData: NoteGraphData | null;
  onNavigateNote: (noteIdOrTitle: string) => void;
  onUnlinkTask?: (taskId: string) => void;
};

export function BacklinksPanel({
  note,
  graphData,
  onNavigateNote,
  onUnlinkTask,
}: BacklinksPanelProps) {
  return (
    <div className="w-80 border-l border-slate-200 bg-slate-50/50 flex flex-col h-full overflow-y-auto divide-y divide-slate-100">
      {/* 1. Neighborhood Graph Widget */}
      <div className="p-4 bg-white">
        <div className="text-xs font-semibold text-slate-800 mb-2 flex items-center justify-between">
          <span>Neighborhood Graph</span>
          <span className="text-[10px] text-slate-400 font-mono">1-hop</span>
        </div>
        {graphData ? (
          <NeighborhoodGraph
            data={graphData}
            activeNoteId={note.id}
            onSelectNote={onNavigateNote}
            className="w-full"
          />
        ) : (
          <div className="text-xs text-slate-400 italic py-4 text-center">Loading graph...</div>
        )}
      </div>

      {/* 2. Incoming Backlinks */}
      <div className="p-4 bg-white">
        <div className="flex items-center justify-between mb-2">
          <div className="text-xs font-semibold text-slate-800 flex items-center gap-1.5">
            <span>⬅️</span>
            <span>Incoming Backlinks</span>
          </div>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-50 text-blue-700 font-mono font-semibold">
            {note.backlinks?.length || 0}
          </span>
        </div>

        {note.backlinks && note.backlinks.length > 0 ? (
          <div className="space-y-1.5">
            {note.backlinks.map((b) => (
              <button
                key={b.source_note_id}
                type="button"
                onClick={() => onNavigateNote(b.source_note_id)}
                className="w-full text-left p-2 rounded-lg bg-slate-50 hover:bg-blue-50/60 border border-slate-200/80 hover:border-blue-200 transition-all group"
              >
                <div className="text-xs font-medium text-slate-900 group-hover:text-blue-700 truncate">
                  📄 {b.source_title}
                </div>
                <div className="text-[10px] text-slate-400 font-mono mt-0.5 truncate">
                  {b.source_folder}
                </div>
              </button>
            ))}
          </div>
        ) : (
          <div className="text-xs text-slate-400 italic py-2">
            No other notes link to this note yet.
          </div>
        )}
      </div>

      {/* 3. Linked Tasks */}
      <div className="p-4 bg-white">
        <div className="flex items-center justify-between mb-2">
          <div className="text-xs font-semibold text-slate-800 flex items-center gap-1.5">
            <span>✅</span>
            <span>Linked Tasks</span>
          </div>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-purple-50 text-purple-700 font-mono font-semibold">
            {note.linked_tasks?.length || 0}
          </span>
        </div>

        {note.linked_tasks && note.linked_tasks.length > 0 ? (
          <div className="space-y-2">
            {note.linked_tasks.map((t) => (
              <div
                key={t.task_id}
                className="p-2 rounded-lg bg-slate-50 border border-slate-200/80 hover:border-purple-200 transition-all"
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs font-medium text-slate-900 truncate flex-1">
                    {t.task_title}
                  </span>
                  {onUnlinkTask && (
                    <button
                      type="button"
                      onClick={() => onUnlinkTask(t.task_id)}
                      className="text-slate-400 hover:text-red-600 text-xs ml-1"
                      title="Unlink task"
                    >
                      ×
                    </button>
                  )}
                </div>
                <div className="flex items-center gap-1.5 mt-1.5">
                  <Pill tone={t.task_status === "done" ? "good" : t.task_status === "in_progress" ? "brand" : "neutral"}>
                    {t.task_status}
                  </Pill>
                  <span className="text-[10px] text-slate-400 font-mono uppercase">{t.link_type}</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-xs text-slate-400 italic py-2">
            No tasks cite this note yet.
          </div>
        )}
      </div>

      {/* 4. Outgoing Links */}
      <div className="p-4 bg-white">
        <div className="flex items-center justify-between mb-2">
          <div className="text-xs font-semibold text-slate-800 flex items-center gap-1.5">
            <span>➡️</span>
            <span>Outgoing Links</span>
          </div>
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-600 font-mono font-semibold">
            {note.outgoing_links?.length || 0}
          </span>
        </div>

        {note.outgoing_links && note.outgoing_links.length > 0 ? (
          <div className="space-y-1.5">
            {note.outgoing_links.map((o) => (
              <button
                key={o.target_note_id}
                type="button"
                onClick={() => onNavigateNote(o.target_note_id)}
                className="w-full text-left p-2 rounded-lg bg-slate-50 hover:bg-slate-100 border border-slate-200/80 transition-colors group"
              >
                <div className="text-xs font-medium text-slate-800 group-hover:text-blue-700 truncate">
                  🔗 {o.target_title}
                </div>
                <div className="text-[10px] text-slate-400 font-mono mt-0.5 truncate">
                  {o.target_folder}
                </div>
              </button>
            ))}
          </div>
        ) : (
          <div className="text-xs text-slate-400 italic py-2">
            No outgoing links in this note.
          </div>
        )}
      </div>
    </div>
  );
}
