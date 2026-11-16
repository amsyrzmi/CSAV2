"use client";

import { useEffect, type RefObject } from "react";
import { ArrowUp } from "lucide-react";
import { PANEL } from "@/lib/assistant-copy";
import type { AssistantConversation } from "@/lib/assistant-conversation";
import { typedMessage } from "@/lib/assistant-view";
import { headerButton, sendLook } from "./looks";

/** The tallest the message box grows, in px: about six lines. Matches its `max-h-[9.5rem]`. */
const MAX_INPUT_PX = 152;

/**
 * The message form of the shared conversation: label, textarea, send, and the retry row after a
 * failed send. It sets no width; the caller gives it a box, an id for the textarea, and a ref to
 * it (for focus). The box grows with what is typed and shrinks back after a send.
 */
export function AssistantComposer({
  conversation,
  inputId,
  inputRef,
}: {
  conversation: AssistantConversation;
  inputId: string;
  inputRef: RefObject<HTMLTextAreaElement | null>;
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

  // The message box grows with what is typed (up to about six lines) and shrinks back after a send.
  useEffect(() => {
    const box = inputRef.current;
    if (!box) return;
    box.style.height = "auto";
    box.style.height = `${Math.min(box.scrollHeight, MAX_INPUT_PX)}px`;
  }, [body, inputRef]);

  return (
    <form
      className="space-y-2 border-t border-[color:var(--fs-border)] px-4 py-3"
      onSubmit={(event) => {
        event.preventDefault();
        // Until the stored thread is known a send would start a new one and hide it.
        if (loaded && body.trim()) ask(typedMessage(body, turns, origins), true);
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
                // The Send button's `loaded` rule, for Enter too.
                if (loaded && body.trim()) ask(typedMessage(body, turns, origins), true);
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
