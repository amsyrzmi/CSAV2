"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import SlideOver from "@/components/console/fs/SlideOver";
import { useMotionAllowed } from "@/components/console/fs/useMotionAllowed";
import { action, Notice } from "@/components/console/ui";
import type { Me } from "@/lib/identity-contracts";
import {
  canConnectWhatsApp, type Conversation, type ConversationPage, type QueueCounts,
} from "@/lib/inbox-api";
import {
  activeFilterChips, priorityOrderAllowed, queueListQuery, tabFilters, tabOrder, withoutChip,
  type QueueFilters, type QueueOrder, type QueueTabId,
} from "@/lib/inbox-queue";
import { shortcutFor, type KeyLike } from "@/lib/inbox-shortcuts";
import { neighbourId } from "@/lib/inbox-thread";
import { ProposalCard } from "../ProposalCard";
import { ConversationList } from "./ConversationList";
import { PHONE_QUERY, useMediaQuery, useTicker, WIDE_QUERY } from "./hooks";
import { OpenConversation } from "./OpenConversation";
import { QueueBar } from "./QueueBar";
import { useInboxActions, type InboxActions } from "./useInboxActions";
import { usePages } from "./usePages";

const FIRST_TAB: QueueTabId = "needs_us";
const PANE = "card min-h-0 min-w-0 flex-col overflow-hidden";

/** Outcome notices, the shared proposal card and the explicit retry, next to the work. */
function Feedback({ actions, framed }: { actions: InboxActions; framed: boolean }) {
  const { error, notice, proposal, pending, busy } = actions;
  if (!error && !notice && !proposal && !(pending && !busy)) return null;
  return <div className={`space-y-2 ${framed ? "max-h-[45%] overflow-y-auto border-t border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3" : ""}`}>
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

  const panelId = useId();
  const [tab, setTab] = useState<QueueTabId>(FIRST_TAB);
  const [filters, setFilters] = useState<QueueFilters>(() => tabFilters(FIRST_TAB));
  const [order, setOrder] = useState<QueueOrder>(() => tabOrder(FIRST_TAB));
  const [search, setSearch] = useState("");
  const [q, setQ] = useState("");
  const [pages, setPages] = useState(1);
  const [selected, setSelected] = useState("");
  const [threadOpen, setThreadOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [cardHost, setCardHost] = useState<HTMLElement | null>(null);
  const detailsRef = useRef<HTMLButtonElement>(null);
  const listScroll = useRef(0);
  const focusAfterBack = useRef(false);

  // Typing settles for 300 ms before the server is asked; queueListQuery drops one character.
  useEffect(() => {
    const timer = setTimeout(() => { setQ(search); setPages(1); }, 300);
    return () => clearTimeout(timer);
  }, [search]);

  const conversations = usePages<Conversation, ConversationPage>(
    queueListQuery({ tenantQuery: query, filters, order, q }), pages, actions.revision,
  );
  // A 403 never leaves stale rows: a role refusal and an inactive package both clear them.
  const refused = conversations.forbidden || conversations.packageInactive;
  const items = refused ? [] : conversations.items;
  const searching = [...q.trim()].length >= 2;
  const chips = activeFilterChips(filters, tab);
  const filtered = chips.length > 0;

  // The counts are the server's, for the whole tenant, and arrive with each list answer. Only an
  // answer changes them: a failed refresh or a new tab loading keeps the last ones. Null (before
  // the first answer, or when the server could not read them, or an older server without them)
  // shows a dash, never 0. Needs us nudges, and is announced, only when it changes after that.
  const extra = conversations.extra;
  const serverCounts: QueueCounts | null = extra ? (extra.counts ?? null) : null;
  const countsKey = extra ? JSON.stringify(serverCounts) : null;
  const [counts, setCounts] = useState<{ key: string; value: QueueCounts | null } | null>(null);
  const [nudge, setNudge] = useState(0);
  const [announcement, setAnnouncement] = useState("");
  if (countsKey !== null && countsKey !== counts?.key) {
    const before = counts?.value?.needs_us;
    const after = serverCounts?.needs_us;
    if (typeof before === "number" && typeof after === "number" && after !== before) {
      if (after > before && motion) setNudge(nudge + 1);
      setAnnouncement(`Needs us: ${after}`);
    }
    setCounts({ key: countsKey, value: serverCounts });
  }

  // A tab is a filter set and an order; choosing one drops the filters added on top of it.
  function chooseTab(next: QueueTabId) {
    setTab(next);
    setFilters(tabFilters(next));
    setOrder(tabOrder(next));
    setPages(1);
  }
  function changeFilters(next: QueueFilters) {
    setFilters(next);
    // Priority order is only allowed while the state is exactly "needs us".
    if (order === "priority" && !priorityOrderAllowed(next)) setOrder(tabOrder(tab));
    setPages(1);
  }
  function clearFilters() {
    changeFilters(tabFilters(tab));
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
    focusAfterBack.current = true;
    setThreadOpen(false);
    setDetailsOpen(false);
    requestAnimationFrame(() => window.scrollTo({ top: listScroll.current }));
  }, []);
  // Back hides the thread and shows the list in one commit. The Back button vanishes with the
  // thread, so focus goes to the row that was open, or to the list heading when that row is gone
  // (filtered out, or disabled while an action is pending).
  useEffect(() => {
    if (threadOpen || !focusAfterBack.current) return;
    focusAfterBack.current = false;
    const target = [...document.querySelectorAll<HTMLElement>("[data-conversation-id]")]
      .find((el) => el.dataset.conversationId === selected && !(el instanceof HTMLButtonElement && el.disabled))
      ?? document.querySelector<HTMLElement>("[data-conversation-list-heading]");
    target?.focus({ preventScroll: true });
  }, [threadOpen, selected]);

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
    {/* Sticky on a phone, where the page itself scrolls the list; hidden while a thread fills it. */}
    <div className={`${selected && threadOpen ? "hidden md:block" : "block"} min-w-0 bg-[var(--fs-bg)] max-md:sticky max-md:top-0 max-md:z-20 max-md:pt-1 max-md:pb-2`}>
      <QueueBar tab={tab} onTab={chooseTab} counts={counts?.value ?? null} nudge={nudge} announcement={announcement}
        panelId={panelId} search={search} onSearch={setSearch}
        onClearSearch={() => { setSearch(""); setQ(""); setPages(1); }}
        filters={filters} onFilters={changeFilters} order={order}
        onOrder={(next) => { setOrder(next); setPages(1); }}
        chips={chips} onRemoveChip={(key) => changeFilters(withoutChip(filters, tab, key))} onClear={clearFilters}
        query={query} meId={me.user.id} />
    </div>
    {!selected && <Feedback actions={actions} framed={false} />}
    {/* Breakpoints in rem like `md`: Tailwind cannot order a px variant against a rem one. */}
    <div className="grid min-w-0 gap-3 md:h-[min(46rem,calc(100dvh-5rem))] md:grid-cols-[minmax(15rem,17.5rem)_minmax(0,1fr)] min-[68.75rem]:grid-cols-[18rem_minmax(0,1fr)_12rem]">
      <div className={`${PANE} ${selected && threadOpen ? "hidden md:flex" : "flex"}`}>
        <ConversationList items={items} selected={selected} blocked={actions.blocked} now={now}
          status={{
            loaded: conversations.loaded, error: conversations.error, forbidden: refused,
            refusal: conversations.packageInactive ? conversations.error : null,
            partial: !!conversations.error && pages > 1 && items.length > 0 && conversations.next_cursor !== null,
            nextCursor: conversations.next_cursor !== null,
          }}
          filtered={filtered} searching={searching} connectHref={connectHref}
          panelId={panelId} labelledBy={`${panelId}-tab-${tab}`}
          onSelect={select} onRetry={actions.refresh} onLoadOlder={() => setPages((value) => value + 1)}
          onClearFilters={clearFilters}
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
