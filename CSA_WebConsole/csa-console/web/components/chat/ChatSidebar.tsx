"use client";
import { useState } from "react";
import clsx from "clsx";
import { Check, MessageSquare, Plus, Trash2, X } from "lucide-react";
import { ChatHistoryConversation } from "../../lib/chat-types";

interface ChatSidebarProps {
  conversations: ChatHistoryConversation[];
  activeId?: string;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onDelete?: (id: string) => Promise<void>;
}

export function ChatSidebar({ conversations, activeId, onSelect, onNewChat, onDelete }: ChatSidebarProps) {
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // Group by Today, Yesterday, Previous 7 Days, etc.
  const now = new Date();
  
  const groups: Record<string, ChatHistoryConversation[]> = {
    "Today": [],
    "Yesterday": [],
    "Previous 7 Days": [],
    "Older": [],
  };

  conversations.forEach(c => {
    const d = new Date(c.opened_at);
    const diffDays = Math.floor((now.getTime() - d.getTime()) / (1000 * 3600 * 24));
    
    if (diffDays === 0 && now.getDate() === d.getDate()) {
      groups["Today"].push(c);
    } else if (diffDays <= 1) {
      groups["Yesterday"].push(c);
    } else if (diffDays <= 7) {
      groups["Previous 7 Days"].push(c);
    } else {
      groups["Older"].push(c);
    }
  });

  const handleDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (confirmId !== id) {
      setConfirmId(id);
      return;
    }
    setDeletingId(id);
    try {
      if (onDelete) {
        await onDelete(id);
      }
    } finally {
      setDeletingId(null);
      setConfirmId(null);
    }
  };

  return (
    <div className="w-64 border-r border-ink-200 bg-ink-50 h-full flex flex-col shrink-0">
      <div className="p-3">
        <button
          onClick={onNewChat}
          className="w-full flex items-center gap-2 px-3 py-2 text-sm font-medium text-ink-900 bg-white border border-ink-200 rounded-lg shadow-sm hover:bg-ink-50"
        >
          <Plus size={16} />
          New chat
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-6">
        {Object.entries(groups).map(([label, items]) => {
          if (items.length === 0) return null;
          return (
            <div key={label}>
              <h3 className="px-3 text-xs font-semibold text-ink-500 mb-2">{label}</h3>
              <ul className="space-y-0.5">
                {items.map(c => {
                  const isConfirming = confirmId === c.id;
                  const isDeleting = deletingId === c.id;

                  return (
                    <li key={c.id} className="relative group">
                      <button
                        onClick={() => onSelect(c.id)}
                        className={clsx(
                          "w-full flex items-center gap-2 px-3 py-2 pr-8 rounded-lg text-sm text-left truncate transition-colors",
                          activeId === c.id
                            ? "bg-ink-200 text-ink-900 font-medium"
                            : "text-ink-700 hover:bg-ink-200/50"
                        )}
                      >
                        <MessageSquare size={14} className="shrink-0" />
                        <span className="truncate">{c.summary || "New conversation"}</span>
                      </button>

                      {onDelete && (
                        <>
                          {isConfirming ? (
                            <div
                              className="absolute right-1.5 top-1/2 -translate-y-1/2 flex items-center gap-1 bg-white border border-rose-200 rounded-md px-1.5 py-0.5 shadow-sm z-10"
                              onClick={(e) => e.stopPropagation()}
                            >
                              <span className="text-[11px] text-rose-600 font-medium">Delete?</span>
                              <button
                                type="button"
                                disabled={isDeleting}
                                onClick={(e) => handleDelete(e, c.id)}
                                className="p-0.5 text-rose-600 hover:bg-rose-50 rounded transition-colors disabled:opacity-50"
                                title="Confirm delete"
                              >
                                <Check size={13} />
                              </button>
                              <button
                                type="button"
                                disabled={isDeleting}
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setConfirmId(null);
                                }}
                                className="p-0.5 text-ink-400 hover:text-ink-600 hover:bg-ink-100 rounded transition-colors"
                                title="Cancel"
                              >
                                <X size={13} />
                              </button>
                            </div>
                          ) : (
                            <button
                              type="button"
                              onClick={(e) => handleDelete(e, c.id)}
                              className={clsx(
                                "absolute right-2 top-1/2 -translate-y-1/2 p-1 text-ink-400 hover:text-rose-600 hover:bg-white/80 rounded transition-opacity",
                                activeId === c.id
                                  ? "opacity-70 hover:opacity-100"
                                  : "opacity-0 group-hover:opacity-100"
                              )}
                              title="Delete conversation"
                            >
                              <Trash2 size={13} />
                            </button>
                          )}
                        </>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          );
        })}
      </div>
    </div>
  );
}

