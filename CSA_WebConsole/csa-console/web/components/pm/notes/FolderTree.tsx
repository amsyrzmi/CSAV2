"use client";

import React, { useState } from "react";
import { FolderTreeNode } from "../types";

type FolderTreeProps = {
  tree: FolderTreeNode | null;
  activeFolder: string | null;
  activeNoteId: string | null;
  onSelectFolder: (folderPath: string | null) => void;
  onSelectNote: (noteId: string) => void;
};

export function FolderTree({
  tree,
  activeFolder,
  activeNoteId,
  onSelectFolder,
  onSelectNote,
}: FolderTreeProps) {
  const [collapsedPaths, setCollapsedPaths] = useState<Set<string>>(new Set());

  const toggleFolder = (path: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setCollapsedPaths((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  };

  const renderFolderNode = (node: FolderTreeNode, depth = 0) => {
    const isCollapsed = collapsedPaths.has(node.path);
    const isSelected = activeFolder === node.path;
    const hasChildren = (node.folders && node.folders.length > 0) || (node.notes && node.notes.length > 0);

    return (
      <div key={node.path} className="select-none">
        {/* Folder header item */}
        <div
          onClick={() => onSelectFolder(isSelected ? null : node.path)}
          className={`group flex items-center justify-between px-2.5 py-1.5 rounded-lg text-xs cursor-pointer transition-colors ${
            isSelected
              ? "bg-blue-50 text-blue-700 font-medium"
              : "text-slate-700 hover:bg-slate-100/80 hover:text-slate-900"
          }`}
          style={{ paddingLeft: `${Math.max(10, depth * 14 + 10)}px` }}
        >
          <div className="flex items-center gap-1.5 truncate">
            {hasChildren && (
              <button
                type="button"
                onClick={(e) => toggleFolder(node.path, e)}
                className="w-4 h-4 flex items-center justify-center text-[10px] text-slate-400 hover:text-slate-600 rounded transition-transform"
              >
                {isCollapsed ? "▶" : "▼"}
              </button>
            )}
            {!hasChildren && <span className="w-4" />}
            <span className="text-sm">{node.path === "/" ? "📁" : "📂"}</span>
            <span className="truncate">{node.name === "Root" ? "All Notes" : node.name}</span>
          </div>
          <span className="text-[10px] text-slate-400 font-mono px-1 rounded group-hover:bg-slate-200/50">
            {node.total_notes}
          </span>
        </div>

        {/* Folder content if expanded */}
        {!isCollapsed && (
          <div>
            {/* Child Folders */}
            {node.folders?.map((child) => renderFolderNode(child, depth + 1))}

            {/* Direct Notes under this folder */}
            {node.notes?.map((n) => {
              const isNoteActive = activeNoteId === n.id;
              return (
                <div
                  key={n.id}
                  onClick={() => onSelectNote(n.id)}
                  className={`flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs cursor-pointer truncate transition-colors ${
                    isNoteActive
                      ? "bg-blue-600 text-white font-medium shadow-xs"
                      : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                  }`}
                  style={{ paddingLeft: `${depth * 14 + 28}px` }}
                >
                  <span className="text-[11px] opacity-80">📄</span>
                  <span className="truncate flex-1">{n.title}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    );
  };

  if (!tree) {
    return <div className="text-xs text-slate-400 p-3 italic">Loading folders...</div>;
  }

  return (
    <div className="space-y-0.5 py-1">
      {renderFolderNode(tree, 0)}
    </div>
  );
}
