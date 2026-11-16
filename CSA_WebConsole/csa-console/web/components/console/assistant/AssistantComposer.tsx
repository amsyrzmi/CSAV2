"use client";

import type { Ref } from "react";
import { ArrowUp } from "lucide-react";
import { PANEL } from "@/lib/assistant-copy";
import type { AssistantConversation } from "@/lib/assistant-conversation";
import { typedMessage } from "@/lib/assistant-view";

const sendLook =
  "inline-flex h-10 min-w-10 shrink-0 items-center justify-center gap-2 rounded-[var(--fs-radius-round)] bg-[var(--fs-primary)] text-sm font-medium text-[color:var(--fs-on-primary)] transition-colors duration-150 hover:bg-[var(--fs-primary-hover)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] active:scale-[0.97] disabled:cursor-not-allowed disabled:bg-[var(--fs-disabled-bg)] disabled:text-[color:var(--fs-disabled-text)] pointer-coarse:h-11";
/** The small outlined button the dock header and the retry row share. */
export const headerButton =
  "inline-flex min-h-9 items-center rounded-[var(--fs-radius-round)] border border-[color:var(--fs-border-control)] px-2.5 text-[13px] font-medium text-[color:var(--fs-text-secondary)] transition-colors duration-150 hover:bg-[var(--fs-surface-blue)] hover:text-[color:var(--fs-text)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] disabled:cursor-not-allowed disabled:opacity-50 pointer-coarse:min-h-11";

/**
 * The message form of the shared conversation: label, textarea, send, and the retry row after a
 * failed send. It sets no width; the caller gives it a box, an id for the textarea, and a ref to
 * it (for focus and for growing the box with what is typed).
 */
export function AssistantComposer({
  conversation,
  inputId,
  inputRef,
}: {
  conversation: AssistantConversation;
  inputId: string;
  inputRef: Ref<HTMLTextAreaElement>;
}) {
  const {
    loaded,
    turns,
    origins,
    body,
    setBody,
    busy,
    retry,
    resumeTab,
    language,
    ask,
    retrySame,
    dismissRetry,
  } = conversation;
  const text = PANEL[language];
  return (
    <form
      className="space-y-2 border-t border-[color:var(--fs-border)] px-4 py-3"
      onSubmit={(event) => {
        event.preventDefault();
        if (body.trim()) ask(typedMessage(body, turns, origins), true);
      }}
    >
      <label htmlFor={inputId} className="block text-xs font-medium text-[color:var(--fs-text-secondary)]">
        {text.message_label}
      </label>
      <div className="rounded-[var(--fs-radius-composer)] border border-[color:var(--fs-border-control)] bg-[var(--fs-surface)] shadow-[var(--fs-shadow-card)] transition-[border-color,box-shadow] duration-200 ease-(--fs-ease-out) motion-reduce:transition-none has-[textarea:focus-visible]:border-[color:var(--fs-primary)] has-[textarea:focus-visible]:shadow-[var(--fs-shadow-popover)] has-[textarea:focus-visible]:outline-2 has-[textarea:focus-visible]:outline-offset-2 has-[textarea:focus-visible]:outline-[color:var(--fs-focus)] has-[textarea:disabled]:bg-[var(--fs-disabled-bg)]">
        <div className="flex items-end gap-2 py-2 pl-4 pr-2">
          <textarea
            id={inputId}
            ref={inputRef}
            data-testid="assistant-input"
            rows={1}
            className="block max-h-[9.5rem] min-h-10 min-w-0 flex-1 resize-none bg-transparent py-2 text-sm leading-6 text-[color:var(--fs-text)] outline-none focus-visible:shadow-none focus-visible:outline-none! disabled:cursor-not-allowed disabled:text-[color:var(--fs-disabled-text)]"
            value={body}
            maxLength={4000}
            disabled={busy || retry}
            onChange={(event) => setBody(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                if (body.trim()) ask(typedMessage(body, turns, origins), true);
              }
            }}
          />
          {!retry && (
            <button
              type="submit"
              data-testid="assistant-send"
              className={`${sendLook} px-4`}
              disabled={busy || resumeTab !== null || !body.trim() || !loaded}
            >
              {busy ? text.button_sending : text.button_send}
              {!busy && <ArrowUp size={16} aria-hidden />}
            </button>
          )}
        </div>
        {retry && (
          <div className="flex flex-wrap items-center justify-end gap-2 px-2.5 pb-2.5">
            <button type="button" className={`${sendLook} px-4`} onClick={retrySame}>
              {text.retry_same}
            </button>
            <button type="button" className={headerButton} onClick={dismissRetry}>
              {text.start_new}
            </button>
          </div>
        )}
      </div>
    </form>
  );
}
