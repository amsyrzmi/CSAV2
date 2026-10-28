"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import SlideOver from "@/components/console/fs/SlideOver";
import { useMotionAllowed } from "@/components/console/fs/useMotionAllowed";
import { action, Notice } from "@/components/console/ui";
import type { Me } from "@/lib/identity-contracts";
import {
  canConnectWhatsApp, inboxListQuery, type ChannelFilter, type Conversation, type ConversationPage,
} from "@/lib/inbox-api";
import { shortcutFor, type KeyLike } from "@/lib/inbox-shortcuts";
import { neighbourId } from "@/lib/inbox-thread";
import { ProposalCard } from "../ProposalCard";
import { ConversationList, type View } from "./ConversationList";
import { PHONE_QUERY, useMediaQuery, useTicker, WIDE_QUERY } from "./hooks";
import { OpenConversation } from "./OpenConversation";
import { useInboxActions, type InboxActions } from "./useInboxActions";
import { usePages } from "./usePages";

const SENDER = { all: "", review: "", known: "known", new: "new" } as const;
const PANE = "card min-h-0 min-w-0 flex-col overflow-hidden";

/** Outcome notices, the shared proposal card and the explicit retry, next to the work. */
function Feedback({ actions, framed }: { actions: InboxActions; framed: boolean }) {
  const { error, notice, proposal, pending, busy } = actions;
  if (!error && !notice && !proposal && !(pending && !busy)) return null;
  return <div className={`space-y-2 ${framed ? "max-h-[45%] overflow-y-auto border-t border-[#D8E1F0] bg-white p-3" : ""}`}>
    {error && <Notice error>{error}</Notice>}
    {notice && <Notice>{notice}</Notice>}
    {proposal && <ProposalCard key={proposal.id} proposal={proposal} tenantId={actions.tenantId}
      onSettled={actions.refresh} />}
    {pending && !busy && <button type="button" className={action}
      onClick={() => void actions.submit(pending)}>Retry the same action</button>}
  </div>;
}

export function Inbox({ me, query, tenantId }: { me: Me; query: string; tenantId: string | null }) {
  const actions = useInboxActions(query, tenantId);
  const rootRef = useRef<HTMLDivElement>(null);
  const motion = useMotionAllowed(rootRef);
  const now = useTicker(30_000);
  const wide = useMediaQuery(WIDE_QUERY);

  const [channel, setChannel] = useState<ChannelFilter>("");
  const [view, setView] = useState<View>("all");
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [pages, setPages] = useState(1);
  const [selected, setSelected] = useState("");
  const [threadOpen, setThreadOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [cardHost, setCardHost] = useState<HTMLElement | null>(null);
  const detailsRef = useRef<HTMLButtonElement>(null);
  const listScroll = useRef(0);

  // Typing settles for 300 ms before the server is asked; inboxListQuery drops one character.
  useEffect(() => {
    const timer = setTimeout(() => { setQ(search); setPages(1); }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const conversations = usePages<Conversation, ConversationPage>(inboxListQuery({
    tenantQuery: query, channel, needsReview: view === "review", sender: SENDER[view], q,
  }), pages, actions.revision);
  const items = conversations.forbidden ? [] : conversations.items;
  const searching = [...q.trim()].length >= 2;
  const filtered = channel !== "" || view !== "all";

  // The count is the server's, for the whole tenant; keep the last answer while a new
  // filter loads, and nudge once when it rises (never on the first answer).
  const serverCount = typeof conversations.extra?.needs_review_count === "number"
    ? conversations.extra.needs_review_count : null;
  const [count, setCount] = useState<number | null>(null);
  const [nudge, setNudge] = useState(0);
  if (serverCount !== null && serverCount !== count) {
    if (count !== null && serverCount > count && motion) setNudge(nudge + 1);
    setCount(serverCount);
  }

  const servicesHref = tenantId ? `/services?client_id=${encodeURIComponent(tenantId)}` : "/portal/services";
  const connectHref = canConnectWhatsApp(me.role) ? servicesHref : null;

  function select(id: string) {
    if (id !== selected) actions.clearFeedback();
    setSelected(id);
    setThreadOpen(true);
    if (window.matchMedia?.(PHONE_QUERY).matches) {
      listScroll.current = window.scrollY;
      requestAnimationFrame(() => document.querySelector("[data-testid='inbox-thread']")
        ?.scrollIntoView({ block: "start" }));
    }
  }
  const back = useCallback(() => {
    setThreadOpen(false);
    setDetailsOpen(false);
    requestAnimationFrame(() => window.scrollTo({ top: listScroll.current }));
  }, []);
  function changeFilters(next: { channel?: ChannelFilter; view?: View }) {
    if (next.channel !== undefined) setChannel(next.channel);
    if (next.view !== undefined) setView(next.view);
    setPages(1);
  }

  // The card is a slide-over only below the three-pane width, and only for an open conversation.
  const slideOpen = detailsOpen && !wide && selected !== "";

  // J/K and Escape at the document level; the handler is refreshed every render.
  const keyHandler = useRef<(event: KeyboardEvent) => void>(() => {});
  useEffect(() => {
    keyHandler.current = (event) => {
      const dialogOpen = slideOpen || document.querySelector("dialog[open]") !== null;
      const shortcut = shortcutFor({
        key: event.key, ctrlKey: event.ctrlKey, metaKey: event.metaKey, altKey: event.altKey,
        target: event.target as KeyLike["target"],
      }, { dialogOpen });
      if (!shortcut) return;
      if (shortcut === "close") {
        if (threadOpen && actions.pending === null && window.matchMedia?.(PHONE_QUERY).matches) back();
        return;
      }
      if (actions.blocked) return;
      const next = neighbourId(items.map((item) => item.id), selected, shortcut);
      if (!next || next === selected) return;
      event.preventDefault();
      actions.clearFeedback();
      setSelected(next);
      requestAnimationFrame(() => document.querySelector(`[data-conversation-id="${CSS.escape(next)}"]`)
        ?.scrollIntoView({ block: "nearest" }));
    };
  });
  useEffect(() => {
    const listener = (event: KeyboardEvent) => keyHandler.current(event);
    document.addEventListener("keydown", listener);
    return () => document.removeEventListener("keydown", listener);
  }, []);

  const row = items.find((item) => item.id === selected);
  return <div ref={rootRef} data-motion={motion ? "on" : "off"} className="mt-6 min-w-0 space-y-4">
    {!selected && <Feedback actions={actions} framed={false} />}
    {/* Breakpoints in rem like `md`: Tailwind cannot order a px variant against a rem one. */}
    <div className="grid min-w-0 gap-3 md:h-[min(46rem,calc(100dvh-5rem))] md:grid-cols-[minmax(13rem,15rem)_minmax(0,1fr)] min-[68.75rem]:grid-cols-[14.5rem_minmax(0,1fr)_12rem]">
      <div className={`${PANE} ${selected && threadOpen ? "hidden md:flex" : "flex"}`}>
        <ConversationList items={items} selected={selected} blocked={actions.blocked} now={now}
          status={{
            loaded: conversations.loaded, error: conversations.error, forbidden: conversations.forbidden,
            partial: !!conversations.error && pages > 1 && items.length > 0 && conversations.next_cursor !== null,
            nextCursor: conversations.next_cursor !== null,
          }}
          search={search} onSearch={setSearch} view={view} onView={(value) => changeFilters({ view: value })}
          channel={channel} onChannel={(value) => changeFilters({ channel: value })}
          reviewCount={count} nudge={nudge} filtered={filtered} searching={searching} connectHref={connectHref}
          onSelect={select} onRetry={actions.refresh} onLoadOlder={() => setPages((value) => value + 1)}
          onClearFilters={() => changeFilters({ channel: "", view: "all" })}
          onClearSearch={() => { setSearch(""); setQ(""); setPages(1); }} />
      </div>
      {selected ? <OpenConversation key={selected} id={selected} row={row} query={query} revision={actions.revision}
        actions={actions} now={now} motion={motion} wide={wide} cardHost={cardHost}
        threadClass={`${PANE} ${threadOpen ? "flex h-[calc(100dvh-1.5rem)] md:h-auto" : "hidden md:flex"}`}
        cardClass="card hidden min-h-0 min-w-0 overflow-y-auto min-[68.75rem]:block"
        feedback={<Feedback actions={actions} framed />} onBack={back}
        onDetails={() => setDetailsOpen(true)} onCloseDetails={() => setDetailsOpen(false)} detailsRef={detailsRef} />
        : <>
          <div className={`${PANE} hidden items-center justify-center p-6 text-center text-sm text-ink-700 md:flex`}>
            Select a conversation to read it.</div>
          <div className="card hidden p-4 text-center text-sm text-ink-700 min-[68.75rem]:block">
            Customer details appear here.</div>
        </>}
    </div>
    <SlideOver open={slideOpen} onClose={() => setDetailsOpen(false)}
      title="Customer details" returnFocusTo={detailsRef}>
      <div ref={setCardHost} />
    </SlideOver>
  </div>;
}
