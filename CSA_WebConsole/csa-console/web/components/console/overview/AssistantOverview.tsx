"use client";

import { useEffect, useId, useRef } from "react";
import { PANEL } from "@/lib/assistant-copy";
import type { AssistantConversation } from "@/lib/assistant-conversation";
import { ideasFor, type Idea } from "@/lib/assistant-ideas";
import shipped from "@/lib/assistant-ideas.json";
import { starterLook } from "../chat/ChatEmptyState";
import { AssistantComposer } from "../assistant/AssistantComposer";
import { AssistantTurns } from "../assistant/AssistantTurns";
import { headerButton } from "../assistant/looks";
import AgentAvatar from "../fs/AgentAvatar";

const IDEAS = shipped as Idea[];

/**
 * Overview's body: the page view of the shared assistant conversation (the dock is the other
 * view, and hides here). It fills the page's height in one centred column: a header with "new
 * conversation", the turns (which scroll inside), the starting ideas while the thread is empty,
 * and the composer. The thread is read at mount.
 *
 * The ideas are the "any" ideas (Overview has none of its own) this person's role and the
 * conversation's packages allow: the same packages the conversation navigates with, so an idea
 * never offers a page the conversation cannot open. A chip only fills the composer.
 */
export function AssistantOverview({ conversation }: { conversation: AssistantConversation }) {
  const { me, packages, ensureLoaded, loaded, turns, busy, language, setBody, newConversation } =
    conversation;
  const titleId = useId();
  const inputId = useId();
  const input = useRef<HTMLTextAreaElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const text = PANEL[language];

  useEffect(() => {
    ensureLoaded();
  }, [ensureLoaded]);

  // The newest reply scrolls into view; an open question stays visible while the assistant works.
  useEffect(() => {
    const box = list.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [loaded, turns.length, busy]);

  const ideas =
    loaded && turns.length === 0
      ? ideasFor(IDEAS, { tab: "overview", role: me.role, packages, language })
      : [];

  return (
    <section
      data-testid="overview-assistant"
      aria-labelledby={titleId}
      className="mx-auto flex h-full min-h-0 w-full max-w-3xl flex-col"
    >
      <header className="flex shrink-0 items-center gap-2 px-4 pt-4 pb-1">
        <AgentAvatar agentId="assistant" size={32} state="rest" label="" />
        <h2
          id={titleId}
          className="min-w-0 font-(family-name:--fs-font-heading) text-lg font-semibold text-[color:var(--fs-text)]"
        >
          {text.overview_headline}
        </h2>
        <button
          type="button"
          className={`ml-auto shrink-0 ${headerButton}`}
          disabled={busy}
          onClick={newConversation}
        >
          {text.new_conversation}
        </button>
      </header>
      <AssistantTurns conversation={conversation} listRef={list} />
      {ideas.length > 0 && (
        <ul
          data-testid="overview-ideas"
          aria-label={text.ideas_label}
          className="flex shrink-0 flex-wrap gap-2 px-4 pb-3"
        >
          {ideas.map((idea) => (
            <li key={idea.id} className="min-w-0 max-w-full">
              <button
                type="button"
                className={`${starterLook} max-w-full break-words text-left`}
                onClick={() => {
                  setBody(idea.text);
                  input.current?.focus();
                }}
              >
                {idea.text}
              </button>
            </li>
          ))}
        </ul>
      )}
      <AssistantComposer conversation={conversation} inputId={inputId} inputRef={input} />
    </section>
  );
}
