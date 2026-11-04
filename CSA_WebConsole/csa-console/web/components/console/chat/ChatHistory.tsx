"use client";

import { useId } from "react";
import { SquarePen } from "lucide-react";
import type { SandboxThread } from "@/lib/sandbox-chat";
import { action, secondary } from "../ui";

/** A conversation's name in the list. The list read carries no message preview, so its number. */
export function threadLabel(thread: SandboxThread): string {
  return `Conversation ${thread.sequence}`;
}

/**
 * The history column (and, on a phone, the slide-over's body): "New chat", then the private
 * conversations as buttons, the open one tinted. Archived conversations are not listed.
 */
export function ChatHistory({ threads, selected, onSelect, onNew, newLabel, newDisabled, selectDisabled,
  hasOlder, onOlder, loaded, showLabel = true }: {
  threads: SandboxThread[];
  /** False until the list read succeeds: a failed read never shows as an empty list. */
  loaded: boolean;
  selected: string;
  onSelect: (id: string) => void;
  onNew: () => void;
  newLabel: string;
  newDisabled: boolean;
  selectDisabled: boolean;
  hasOlder: boolean;
  onOlder: () => void;
  /** Off inside the slide-over, whose title already says it. */
  showLabel?: boolean;
}) {
  const labelId = useId();
  const visible = threads.filter((thread) => !thread.archived_at);
  return <div className="flex h-full min-h-0 flex-col gap-4">
    <button type="button" className={`${action} w-full`} disabled={newDisabled} onClick={onNew}>
      <SquarePen size={16} aria-hidden />{newLabel}</button>
    <nav aria-label={showLabel ? undefined : "Your private conversations"} aria-labelledby={showLabel ? labelId : undefined}
      className="flex min-h-0 flex-1 flex-col">
      {showLabel && <p id={labelId} className="px-2 pb-2 text-xs font-medium text-[color:var(--fs-text-secondary)]">
        Your private conversations</p>}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {visible.length ? <ul className="space-y-1">
          {visible.map((thread) => {
            const current = thread.id === selected;
            return <li key={thread.id}>
              <button type="button" aria-current={current ? "true" : undefined} disabled={selectDisabled}
                title={new Date(thread.created_at).toLocaleString()} onClick={() => onSelect(thread.id)}
                className={`flex min-h-10 w-full items-center rounded-[var(--fs-radius-control)] px-3 py-2 text-left text-sm transition-colors duration-150 focus-visible:outline-3 focus-visible:-outline-offset-3 focus-visible:outline-[color:var(--fs-focus)] disabled:cursor-not-allowed pointer-coarse:min-h-11 ${current
                  ? "bg-[var(--fs-surface-blue)] font-medium text-[color:var(--fs-primary-hover)]"
                  : "text-[color:var(--fs-text)] hover:bg-[var(--fs-surface-blue)] disabled:text-[color:var(--fs-text-secondary)] disabled:hover:bg-transparent"}`}>
                <span className="truncate">{threadLabel(thread)}</span></button>
            </li>;
          })}
        </ul> : loaded && <p className="px-2 text-sm text-[color:var(--fs-text-secondary)]">No conversations yet.</p>}
        {hasOlder && <button type="button" className={`${secondary} mt-3 w-full`} disabled={selectDisabled} onClick={onOlder}>
          Older conversations</button>}
      </div>
    </nav>
  </div>;
}
