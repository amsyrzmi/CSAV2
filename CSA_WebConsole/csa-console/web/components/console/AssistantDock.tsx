"use client";

import { useEffect, useId, useRef, useState } from "react";
import { MessageSquare, X } from "lucide-react";
import { panelText, PANEL } from "@/lib/assistant-copy";
import { useAssistantConversation, type AssistantConversation } from "@/lib/assistant-conversation";
import { tabLabel } from "@/lib/assistant-view";
import AgentAvatar from "./fs/AgentAvatar";
import { AssistantComposer, headerButton } from "./assistant/AssistantComposer";
import { AssistantTurns } from "./assistant/AssistantTurns";
import { action } from "./ui";

/** The corner the launcher and the panel share: 20 px in, or clear of a notch or home bar. */
const corner = "bottom-[max(1.25rem,env(safe-area-inset-bottom))] right-[max(1.25rem,env(safe-area-inset-right))]";

/**
 * The floating view of the shared conversation; it renders nothing where none is offered or
 * while the conversation is not ready.
 */
export function AssistantDock() {
  const conversation = useAssistantConversation();
  if (!conversation) return null;
  // Keyed by company, so a change of company also starts the panel closed.
  return <Dock key={conversation.tenant} conversation={conversation} />;
}

function Dock({ conversation }: { conversation: AssistantConversation }) {
  const { me, packages, ensureLoaded, turns, body, busy, context, language, newConversation } = conversation;
  const panelId = useId();
  const [open, setOpen] = useState(false);
  const openButton = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const returnFocus = useRef(false);

  // The thread and its turns are read the first time the panel opens.
  useEffect(() => {
    if (open) ensureLoaded();
  }, [open, ensureLoaded]);

  useEffect(() => {
    if (open) input.current?.focus();
    else if (returnFocus.current) openButton.current?.focus();
  }, [open]);

  // The message box grows with what is typed (up to about six lines) and shrinks back after a send.
  useEffect(() => {
    const box = input.current;
    if (!box) return;
    box.style.height = "auto";
    box.style.height = `${Math.min(box.scrollHeight, 152)}px`;
  }, [body, open]);

  // The newest reply scrolls into view; an open question stays visible while the assistant works.
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const box = list.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [open, turns.length, busy]);

  function close() {
    returnFocus.current = true;
    setOpen(false);
  }

  // Panel-wide lines follow the newest turn; each turn's own lines follow that turn.
  const text = PANEL[language];

  if (!open)
    return (
      <button
        ref={openButton}
        type="button"
        data-testid="assistant-open"
        aria-expanded={false}
        aria-controls={panelId}
        onClick={() => setOpen(true)}
        className={`${action} fixed ${corner} z-40 rounded-full shadow-[var(--fs-shadow-popover)]`}
      >
        <MessageSquare size={18} aria-hidden />
        {text.dock_title}
      </button>
    );

  return (
    <section
      id={panelId}
      data-testid="assistant-panel"
      aria-labelledby={`${panelId}-title`}
      onKeyDown={(event) => {
        if (event.key === "Escape") close();
      }}
      className={`fixed ${corner} z-40 flex max-h-[min(80dvh,40rem)] w-[min(28rem,calc(100vw-2.5rem))] flex-col rounded-[var(--fs-radius-tile)] border border-[color:var(--fs-border)] bg-[var(--fs-surface-raised)] text-[color:var(--fs-text)] shadow-[var(--fs-shadow-popover)]`}
    >
      <header className="flex flex-wrap items-center gap-x-2 gap-y-1.5 border-b border-[color:var(--fs-border)] px-3.5 py-3 sm:flex-nowrap">
        <AgentAvatar agentId="assistant" size={32} state="rest" label="" />
        <h2 id={`${panelId}-title`} className="text-base font-semibold">
          {text.dock_title}
        </h2>
        <span
          data-testid="assistant-tab"
          className="min-w-0 truncate rounded-full bg-[var(--fs-surface-blue)] px-2 py-0.5 text-xs text-[color:var(--fs-text)]"
        >
          {context
            ? panelText(language, "on_tab", { tab: tabLabel(context.tab, me, packages) })
            : text.no_tab_context}
        </span>
        <button
          type="button"
          className={`ml-auto shrink-0 ${headerButton}`}
          disabled={busy}
          onClick={newConversation}
        >
          {text.new_conversation}
        </button>
        <button
          type="button"
          aria-label={text.close_assistant}
          onClick={close}
          className="inline-grid size-9 shrink-0 place-items-center rounded-[var(--fs-radius-round)] text-[color:var(--fs-text-secondary)] transition-colors duration-150 hover:bg-[var(--fs-surface-blue)] hover:text-[color:var(--fs-text)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] pointer-coarse:size-11"
        >
          <X size={18} aria-hidden />
        </button>
      </header>
      <AssistantTurns conversation={conversation} listRef={list} />
      <AssistantComposer conversation={conversation} inputId={`${panelId}-input`} inputRef={input} />
    </section>
  );
}
