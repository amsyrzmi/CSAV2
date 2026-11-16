"use client";

import { useEffect, useId, useRef, useState } from "react";
import { usePathname } from "next/navigation";
import { X } from "lucide-react";
import { panelText, PANEL } from "@/lib/assistant-copy";
import { useAssistantConversation, type AssistantConversation } from "@/lib/assistant-conversation";
import {
  bubbleMayShow,
  DISMISSED_KEY,
  parseDismissed,
  pickBubbleIdea,
  serializeDismissed,
} from "@/lib/assistant-bubble";
import { ideasFor, type Idea } from "@/lib/assistant-ideas";
import shipped from "@/lib/assistant-ideas.json";
import { dockHidden, openProposals, tabForPath, tabLabel } from "@/lib/assistant-view";
import AgentAvatar from "./fs/AgentAvatar";
import { AssistantComposer } from "./assistant/AssistantComposer";
import { AssistantTurns } from "./assistant/AssistantTurns";
import { AssistantLauncher } from "./assistant/AssistantLauncher";
import { corner, headerButton } from "./assistant/looks";
import { useIdleTimer } from "./assistant/useIdleTimer";

const IDEAS = shipped as Idea[];

/** The ids dismissed in this browser session; a browser that refuses storage has none. */
function readDismissed(): string[] {
  try {
    return parseDismissed(window.sessionStorage.getItem(DISMISSED_KEY));
  } catch {
    return [];
  }
}

/** A refused write still hides the idea for this visit (the caller drops it from the page). */
function writeDismissed(ids: string[]) {
  try {
    window.sessionStorage.setItem(DISMISSED_KEY, serializeDismissed(ids));
  } catch {
    // Storage is blocked: the idea may come back on a later visit, which is acceptable.
  }
}

/**
 * The floating view of the shared conversation; it renders nothing where none is offered, while
 * the conversation is not ready, and on Overview, where the page itself is the assistant.
 */
export function AssistantDock() {
  const conversation = useAssistantConversation();
  const pathname = usePathname();
  if (!conversation) return null;
  if (dockHidden(pathname, conversation.me, conversation.packages)) return null;
  // Keyed by company, so a change of company also starts the panel closed.
  return <Dock key={conversation.tenant} conversation={conversation} />;
}

function Dock({ conversation }: { conversation: AssistantConversation }) {
  const {
    me,
    packages,
    ensureLoaded,
    loaded,
    turns,
    outcomes,
    busy,
    context,
    language,
    setBody,
    newConversation,
  } = conversation;
  const pathname = usePathname();
  const panelId = useId();
  // Closed on a page load, but open when it mounts with a resumed request on its way: a navigate
  // from Overview (where the dock is hidden) lands here, and its answer must not arrive unseen.
  // Read once at mount, so closing the panel is never undone by the resume.
  const [open, setOpen] = useState(() => conversation.resumeTab !== null);
  const openButton = useRef<HTMLButtonElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const returnFocus = useRef(false);

  // The thread and its turns are read the first time the panel opens.
  useEffect(() => {
    if (open) ensureLoaded();
  }, [open, ensureLoaded]);

  // The idea bubble: after a quiet moment on the closed dock, one idea per page visit. `shown` is
  // the idea offered on one page (`gone` once it was dismissed or taken up); the dock stays
  // mounted across navigation, so a different `path` is a new visit with nothing shown yet.
  const idleMs = useIdleTimer();
  const [shown, setShown] = useState<{ path: string; idea: { id: string; text: string }; gone: boolean } | null>(null);
  const may = bubbleMayShow({
    panelOpen: open,
    cardOpen: openProposals(turns, outcomes).length > 0,
    onOverview: dockHidden(pathname, me, packages),
    busy,
    shownThisVisit: shown?.path === pathname,
    idleMs,
    hidden: document.visibilityState === "hidden",
  });
  // The closed dock has not read the thread, so it knows neither the language nor an open card.
  // Only once every other condition holds does it load them, and only if some idea is left.
  const ideaArgs = { tab: tabForPath(pathname, me, packages), role: me.role, packages };
  const dismissed = may ? readDismissed() : [];
  const pending =
    may &&
    !loaded &&
    pickBubbleIdea(ideasFor(IDEAS, { ...ideaArgs, language: "en" }), dismissed, false) !== null;
  useEffect(() => {
    if (pending) ensureLoaded();
  }, [pending, ensureLoaded]);
  if (may && loaded) {
    const picked = pickBubbleIdea(ideasFor(IDEAS, { ...ideaArgs, language }), dismissed, false);
    // Latched during render (React's adjust-state-while-rendering pattern): `may` is false from
    // the next render on, because this page's visit now has its idea.
    if (picked) setShown({ path: pathname, idea: picked, gone: false });
  }
  const idea = shown && shown.path === pathname && !shown.gone ? shown.idea : null;

  function retireIdea() {
    setShown((current) => (current ? { ...current, gone: true } : current));
  }

  function dismissIdea(id: string) {
    writeDismissed([...readDismissed(), id]);
    retireIdea();
  }

  useEffect(() => {
    if (open) input.current?.focus();
    else if (returnFocus.current) openButton.current?.focus();
  }, [open]);

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
      <AssistantLauncher
        label={text.dock_title}
        busy={busy}
        panelId={panelId}
        onOpen={() => {
          retireIdea();
          setOpen(true);
        }}
        buttonRef={openButton}
        idea={idea}
        dismissLabel={text.dock_idea_dismiss}
        onIdea={(picked) => {
          // Only proposed: the idea goes into the composer (the panel focuses it) and nothing is sent.
          retireIdea();
          setBody(picked.text);
          setOpen(true);
        }}
        onDismissIdea={dismissIdea}
      />
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
