"use client";

import React, { useState } from "react";
import { NoteDetail, NoteRevision } from "../types";
import { Pill } from "../../ui/kit";

type RevisionHistoryModalProps = {
  isOpen: boolean;
  onClose: () => void;
  note: NoteDetail;
  onRestoreRevision: (revisionId: string) => Promise<void>;
};

export function RevisionHistoryModal({
  isOpen,
  onClose,
  note,
  onRestoreRevision,
}: RevisionHistoryModalProps) {
  const [restoringId, setRestoringId] = useState<string | null>(null);
  const [selectedRev, setSelectedRev] = useState<NoteRevision | null>(note.revisions[0] || null);

  if (!isOpen) return null;

  const handleRestore = async (revId: string) => {
    if (!confirm("Are you sure you want to restore this revision? A new revision with this historical content will be appended.")) {
      return;
    }
    setRestoringId(revId);
    try {
      await onRestoreRevision(revId);
      onClose();
    } finally {
      setRestoringId(null);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-900/40 backdrop-blur-sm animate-in fade-in duration-150">
      <div className="bg-white rounded-2xl shadow-2xl border border-slate-200/80 w-full max-w-3xl max-h-[85vh] flex flex-col overflow-hidden">
        {/* Header */}
        <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between bg-slate-50/50">
          <div>
            <h2 className="text-base font-semibold text-slate-900 flex items-center gap-2">
              <span>🕒</span> Revision History: {note.title}
            </h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Compare immutable revisions and restore earlier versions cleanly.
            </p>
          </div>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-600 p-1.5 rounded-lg hover:bg-slate-100 transition-colors"
          >
            ✕
          </button>
        </div>

        {/* Content Body */}
        <div className="flex-1 flex overflow-hidden divide-x divide-slate-100">
          {/* Revisions list */}
          <div className="w-72 overflow-y-auto p-4 space-y-2 bg-slate-50/30">
            {note.revisions.length === 0 ? (
              <div className="text-xs text-slate-400 text-center py-6">No revisions found.</div>
            ) : (
              note.revisions.map((rev) => {
                const isCurrent = rev.id === note.current_revision_id;
                const isSelected = selectedRev?.id === rev.id;

                return (
                  <button
                    key={rev.id}
                    type="button"
                    onClick={() => setSelectedRev(rev)}
                    className={`w-full text-left p-3 rounded-xl border transition-all ${
                      isSelected
                        ? "bg-white border-blue-500 shadow-sm ring-1 ring-blue-500/20"
                        : "bg-white/80 border-slate-200 hover:border-slate-300 hover:bg-white"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-xs text-slate-900">
                        Revision #{rev.revision_number}
                      </span>
                      {isCurrent && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded font-semibold bg-emerald-50 text-emerald-700 border border-emerald-200">
                          Current
                        </span>
                      )}
                    </div>
                    <div className="text-xs text-slate-600 mt-1 truncate">{rev.title}</div>
                    <div className="text-[11px] text-slate-400 mt-1">
                      {new Date(rev.created_at).toLocaleString([], {
                        month: "short",
                        day: "numeric",
                        hour: "2-digit",
                        minute: "2-digit",
                      })}
                    </div>
                    <div className="text-[10px] text-slate-400 font-mono mt-1 truncate">
                      SHA: {rev.content_hash.slice(0, 12)}...
                    </div>
                  </button>
                );
              })
            )}
          </div>

          {/* Revision Detail Inspector */}
          <div className="flex-1 flex flex-col overflow-hidden p-6 bg-white">
            {selectedRev ? (
              <>
                <div className="flex items-center justify-between pb-3 border-b border-slate-100">
                  <div>
                    <div className="flex items-center gap-2">
                      <h3 className="text-sm font-semibold text-slate-900">
                        Revision #{selectedRev.revision_number}
                      </h3>
                      {selectedRev.id === note.current_revision_id && (
                        <Pill tone="good">Active Current</Pill>
                      )}
                    </div>
                    <p className="text-xs text-slate-500 mt-0.5">
                      Created by {selectedRev.author_name || selectedRev.author_email || "System"} on{" "}
                      {new Date(selectedRev.created_at).toLocaleString()}
                    </p>
                  </div>

                  {selectedRev.id !== note.current_revision_id && (
                    <button
                      type="button"
                      disabled={restoringId === selectedRev.id}
                      onClick={() => handleRestore(selectedRev.id)}
                      className="px-3 py-1.5 text-xs font-medium text-blue-700 bg-blue-50 hover:bg-blue-100 border border-blue-200 rounded-lg transition-colors flex items-center gap-1.5 disabled:opacity-50"
                    >
                      <span>🔄</span>
                      <span>{restoringId === selectedRev.id ? "Restoring..." : "Restore this Version"}</span>
                    </button>
                  )}
                </div>

                <div className="flex items-center gap-2 my-2 py-1 text-xs text-slate-500">
                  <span className="font-mono text-[11px] text-slate-400">Content Hash:</span>
                  <span className="font-mono text-[11px] text-slate-600 bg-slate-100 px-2 py-0.5 rounded">
                    {selectedRev.content_hash}
                  </span>
                </div>

                <div className="flex-1 overflow-y-auto mt-2 p-4 rounded-xl bg-slate-50/80 border border-slate-200/60 font-mono text-xs text-slate-800 whitespace-pre-wrap leading-relaxed">
                  {selectedRev.id === note.current_revision_id ? (
                    note.body_markdown
                  ) : (
                    <div className="text-slate-500 italic">
                      [Historical snapshot. Click &apos;Restore this Version&apos; to roll back note body to this snapshot.]
                    </div>
                  )}
                </div>
              </>
            ) : (
              <div className="flex-1 flex items-center justify-center text-xs text-slate-400">
                Select a revision to inspect.
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-3 border-t border-slate-100 bg-slate-50/50 flex justify-end">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-xs font-medium text-slate-600 hover:text-slate-800 rounded-lg hover:bg-slate-100 transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
