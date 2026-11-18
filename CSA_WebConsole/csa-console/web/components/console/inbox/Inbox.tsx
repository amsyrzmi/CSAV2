"use client";

import { useCallback, useEffect, useId, useRef, useState, type RefObject } from "react";
import SlideOver from "@/components/console/fs/SlideOver";
import { useMotionAllowed } from "@/components/console/fs/useMotionAllowed";
import { action, Notice } from "@/components/console/ui";
import type { Me } from "@/lib/identity-contracts";
import {
  bulkPath, canConnectWhatsApp, type BulkResult, type Conversation, type ConversationPage, type QueueActionName,
  type QueueCounts, type QueueFields, type SavedView,
} from "@/lib/inbox-api";
import {
  activeFilterChips, BULK_LIMIT, BULK_LIMIT_TEXT, bulkFailureLines, bulkSummary, countsShowWork,
  priorityOrderAllowed, queueListQuery, selectAllText, tabFilters, tabOrder, withoutChip,
  type QueueFilters, type QueueOrder, type QueueTabId,
} from "@/lib/inbox-queue";
import { OPEN_OVERLAY_SELECTOR, shortcutFor, type KeyLike, type Shortcut } from "@/lib/inbox-shortcuts";
import { neighbourId } from "@/lib/inbox-thread";
import { ProposalCard } from "../ProposalCard";
import { BulkBar, SelectToggle } from "./BulkBar";
import { ConversationList } from "./ConversationList";
import { conversationName } from "./ConversationRow";
import { PHONE_QUERY, useMediaQuery, useTicker, WIDE_QUERY } from "./hooks";
import { OpenConversation } from "./OpenConversation";
import { QueueBar } from "./QueueBar";
import { ResponseTimes } from "./QueueSettings";
import { ShortcutHelp } from "./ShortcutHelp";
import { useInboxActions, type InboxActions } from "./useInboxActions";
import { usePages } from "./usePages";
import { useQueueActions } from "./useQueueActions";
import { useHasConversations, useInboxSettings } from "./useQueueData";
import { ViewsMenu, type ActiveView, type ChosenView } from "./ViewsMenu";

const FIRST_TAB: QueueTabId = "needs_us";
const PANE = "card min-h-0 min-w-0 flex-col overflow-hidden";
const NONE: ReadonlySet<string> = new Set();
/** A burst of count changes settles this long before one polite announcement says where it ended. */
const ANNOUNCE_SETTLE_MS = 1500;
/**
 * Which primary button (its `data-action`) each action key presses. C takes only an unclaimed item:
 * "Take it from {name}" (an administrator taking a colleague's claim) stays a deliberate click.
 */
const PRIMARY_FOR: Partial<Record<Shortcut, readonly string[]>> = {
  claim: ["take_it"],
  resolve: ["resolve"],
};

/** Laid out (not `display: none` itself or inside a hidden pane), so a key never acts on what is not shown. */
function shown(element: HTMLElement | null): element is HTMLElement {
  return element !== null && element.getClientRects().length > 0;
}

/**
 * A bulk action's answer: its summary (the notice it set) and what did not apply, one line per
 * conversation, with those conversations' ids (`kept`). Shown only while the notice is still that summary.
 */
type BulkOutcomeView = { summary: string; lines: string[]; kept: readonly string[] };

/**
 * Outcome notices, the shared proposal card and the explicit retry, next to the work. After a bulk
 * action the summary and the list of what did not apply sit in one focusable "Bulk result" region.
 */
function Feedback({ actions, framed, bulk, bulkRef }: {
  actions: InboxActions; framed: boolean;
  bulk?: BulkOutcomeView | null; bulkRef?: RefObject<HTMLElement | null>;
}) {
  const { error, notice, proposal, pending, busy } = actions;
  if (!error && !notice && !proposal && !(pending && !busy)) return null;
  return <div className={`space-y-2 ${framed ? "max-h-[45%] overflow-y-auto border-t border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3" : ""}`}>
    {error && <Notice error>{error}</Notice>}
    {notice && (bulk && notice === bulk.summary ? <section ref={bulkRef} tabIndex={-1} aria-label="Bulk result" data-testid="inbox-bulk-result"
      className="min-w-0 space-y-2 rounded-[var(--fs-radius-control)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]">
      <Notice>{notice}</Notice>
      {bulk.lines.length > 0 && <ul aria-label="Not done"
        className="list-disc space-y-1 pl-5 text-sm text-[color:var(--fs-text)] [overflow-wrap:anywhere]">
        {bulk.lines.map((line, index) => <li key={index}>{line}</li>)}
      </ul>}
    </section> : <Notice>{notice}</Notice>)}
    {proposal && <ProposalCard key={proposal.id} proposal={proposal} tenantId={actions.tenantId}
      onSettled={actions.refresh} />}
    {pending && !busy && <button type="button" className={action}
      onClick={() => void actions.submit(pending)}>Retry the same action</button>}
  </div>;
}

export function Inbox({ me, query, tenantId }: { me: Me; query: string; tenantId: string | null }) {
  const actions = useInboxActions(query, tenantId);
  const queue = useQueueActions(query, actions);
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
  // The saved view the screen was set to; the Views button names it while the filters still match.
  const [activeView, setActiveView] = useState<ActiveView | null>(null);
  const [threadOpen, setThreadOpen] = useState(false);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [cardHost, setCardHost] = useState<HTMLElement | null>(null);
  const detailsRef = useRef<HTMLButtonElement>(null);
  const listScroll = useRef(0);
  const focusAfterBack = useRef(false);

  // Bulk selection (design 14.6): the ids picked, Select mode for touch, what the bar says about
  // the selection, and the last bulk answer. While a bulk action is the latest outcome, the
  // feedback sits under the bar (not in an open thread, which a phone hides behind the list).
  const canSelect = me.role !== "read_only";
  const canAssign = me.role === "tenant_admin" || me.role === "platform_admin";
  // The company's response times: read the first time their panel opens, and kept here so the
  // answer survives the bulk bar taking the queue bar's second line.
  const [timesAsked, setTimesAsked] = useState(false);
  const times = useInboxSettings(query, timesAsked);
  const [picked, setPicked] = useState<ReadonlySet<string>>(NONE);
  const [selectMode, setSelectMode] = useState(false);
  const [selectMessage, setSelectMessage] = useState("");
  const [bulkFeedback, setBulkFeedback] = useState(false);
  const [bulkResult, setBulkResult] = useState<BulkOutcomeView | null>(null);
  const bulkResultRef = useRef<HTMLElement>(null);
  const bulkBarRef = useRef<HTMLDivElement>(null);
  const selectToggleRef = useRef<HTMLButtonElement>(null);
  const focusSelectToggle = useRef(false);
  const bulkCountRef = useRef<HTMLParagraphElement>(null);
  const focusBulkCount = useRef(false);

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
  // The selection as it applies: the picked rows that are loaded, in list order. An id whose row
  // has left the list (resolved elsewhere, say) is neither counted nor sent.
  const chosen = items.filter((item) => picked.has(item.id)).map((item) => item.id);
  // Each list answer drops a picked row it no longer has, so the row does not come back checked
  // later; a row the bulk result names as not done stays picked while that result stands.
  const listAnswer = conversations.extra;
  const [prunedFor, setPrunedFor] = useState(listAnswer);
  if (listAnswer !== prunedFor) {
    setPrunedFor(listAnswer);
    if (listAnswer !== null) {
      const keep = (id: string) => items.some((item) => item.id === id) || !!bulkResult?.kept.includes(id);
      if ([...picked].some((id) => !keep(id))) setPicked(new Set([...picked].filter(keep)));
    }
  }

  // The read time a bulk request sends as `seen_at`: the list's `as_of` when the selection started,
  // kept (the earliest) while more rows are added, so a customer who wrote after the person looked
  // is refused even when the list has polled since. After a bulk answer it moves to the next list
  // answer's `as_of` (the person has seen the refreshed list), so an item refused because the
  // customer wrote can be acted on again once that message is on screen, and not before. An answer
  // without a read time (or none yet for a new list) never replaces it: it waits for one that has.
  const listAsOf = conversations.extra?.as_of ?? null;
  const [selectionAsOf, setSelectionAsOf] = useState<string | null>(null);
  const [renewAsOf, setRenewAsOf] = useState<{ from: string | null } | null>(null);
  if (renewAsOf && listAsOf !== null && listAsOf !== renewAsOf.from) {
    setRenewAsOf(null);
    setSelectionAsOf(listAsOf);
  }
  /** A selection starts (from nothing): it is made against the list as it reads now. */
  function startSelection() {
    setSelectionAsOf(listAsOf);
    setRenewAsOf(null);
  }

  // Where an outcome shows: a request that starts decides it. Only a bulk one (first try or its
  // Retry) puts its outcome under the bar; any other (a thread action, a reply, an approval) keeps
  // it in the thread pane as before.
  const [wasBusy, setWasBusy] = useState(false);
  if (actions.busy !== wasBusy) {
    setWasBusy(actions.busy);
    if (actions.busy) setBulkFeedback(actions.pending?.path === bulkPath(query));
  }

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
    if (typeof before === "number" && typeof after === "number" && after > before && motion) setNudge(nudge + 1);
    setCounts({ key: countsKey, value: serverCounts });
  }
  // The Needs us count is spoken politely once per burst: each change restarts a short wait, and
  // only where it settled is said, and only when that differs from what was said (or first shown).
  const needsUs = counts?.value?.needs_us;
  const spoken = useRef<number | null>(null);
  useEffect(() => {
    if (typeof needsUs !== "number") return;
    if (spoken.current === null) { spoken.current = needsUs; return; }
    const timer = setTimeout(() => {
      if (needsUs === spoken.current) return;
      spoken.current = needsUs;
      setAnnouncement(`Needs us: ${needsUs}`);
    }, ANNOUNCE_SETTLE_MS);
    return () => clearTimeout(timer);
  }, [needsUs]);

  // "Connect WhatsApp" only for a company with no conversation at all. A count above zero proves
  // there are some; zero counts do not (the assistant's chats and resolved ones are not counted), so
  // an empty tab asks the All list for one row. The All tab with nothing filtered is that list.
  const listEmpty = conversations.loaded && !conversations.error && !refused && items.length === 0
    && !filtered && !searching;
  const counted = countsShowWork(counts?.value ?? null);
  const anyConversation = useHasConversations(query, listEmpty && tab !== "all" && !counted, listAsOf);
  const firstRun = listEmpty && !counted && (tab === "all" || anyConversation === false);
  const [helpOpen, setHelpOpen] = useState(false);

  /** Empties the selection and leaves Select mode; focus inside the vanishing bulk bar goes to Select. */
  function clearSelection() {
    if (bulkBarRef.current?.contains(document.activeElement)) focusSelectToggle.current = true;
    setPicked(NONE);
    setSelectMode(false);
    setSelectMessage("");
    setSelectionAsOf(null);
    setRenewAsOf(null);
  }
  useEffect(() => {
    if (focusBulkCount.current && bulkCountRef.current) {
      focusBulkCount.current = false;
      bulkCountRef.current.focus();
    }
    if (!focusSelectToggle.current || !selectToggleRef.current) return;
    focusSelectToggle.current = false;
    selectToggleRef.current.focus();
  });

  /** One box: the 51st is refused in words, and a box is only ever added to the loaded rows' selection. */
  function checkRow(id: string, on: boolean) {
    const next = new Set(chosen);
    if (on && !next.has(id) && next.size >= BULK_LIMIT) {
      setSelectMessage(BULK_LIMIT_TEXT);
      return;
    }
    if (on && chosen.length === 0) startSelection();
    if (on) next.add(id); else next.delete(id);
    setPicked(next);
    setSelectMessage("");
  }
  /** Its button goes away (the bar changes under it), so focus moves to the count it changed. */
  function selectAll() {
    if (chosen.length === 0) startSelection();
    setPicked(new Set(items.slice(0, BULK_LIMIT).map((item) => item.id)));
    setSelectMessage(selectAllText(items.length));
    focusBulkCount.current = true;
  }

  // Applies a bulk answer, first attempt or Retry alike (the kept request carries this callback):
  // what was done leaves the selection, what was not stays selected and is listed by name. Names
  // are the ones on screen when the request was sent; refreshed every render for the newest rows.
  const settleBulk = useRef<(results: BulkResult[], names: Map<string, string>) => void>(() => {});
  useEffect(() => {
    settleBulk.current = (results, names) => {
      const failed = new Set(bulkSummary(results).failedIds);
      setPicked((current) => {
        const next = new Set(current);
        for (const result of results) {
          if (failed.has(result.id)) next.add(result.id); else next.delete(result.id);
        }
        return next;
      });
      const nameOf = (id: string) => {
        const item = items.find((row) => row.id === id);
        return names.get(id) ?? (item ? conversationName(item) : undefined);
      };
      setBulkResult({
        summary: bulkSummary(results).text, lines: bulkFailureLines(results, nameOf), kept: [...failed],
      });
      setBulkFeedback(true);
      setRenewAsOf({ from: listAsOf });
    };
  });
  // Focus moves to the result, which names what is left to do.
  useEffect(() => {
    if (bulkResult) bulkResultRef.current?.focus();
  }, [bulkResult]);

  function runBulk(action: QueueActionName, fields: QueueFields = {}) {
    if (chosen.length === 0 || actions.blocked) return;
    const names = new Map(items.filter((item) => picked.has(item.id)).map((item) => [item.id, conversationName(item)]));
    // The read time of the list the selection was made on (see `selectionAsOf`), so resolve and
    // snooze refuse an item whose customer wrote after it. Only a list answer without `as_of` (an
    // older server) leaves it empty; then it is now, which cannot catch a message that arrived
    // between the read and the click.
    const seenAt = selectionAsOf ?? new Date().toISOString();
    setBulkFeedback(true);
    setBulkResult(null);
    setSelectMessage("");
    void queue.bulk(action, chosen, fields, seenAt, (results) => settleBulk.current(results, names));
  }

  // A tab is a filter set and an order; choosing one drops the filters added on top of it.
  function chooseTab(next: QueueTabId) {
    clearSelection();
    setActiveView(null);
    setTab(next);
    setFilters(tabFilters(next));
    setOrder(tabOrder(next));
    setPages(1);
  }
  /** Needs us is empty: the All tab, with focus on it (the button that was pressed goes away). */
  function seeAll() {
    chooseTab("all");
    document.getElementById(`${panelId}-tab-all`)?.focus();
  }
  function changeFilters(next: QueueFilters) {
    clearSelection();
    setActiveView(null);
    setFilters(next);
    // Priority order is only allowed while the state is exactly "needs us".
    if (order === "priority" && !priorityOrderAllowed(next)) setOrder(tabOrder(tab));
    setPages(1);
  }
  function clearFilters() {
    changeFilters(tabFilters(tab));
  }
  function changeOrder(next: QueueOrder) {
    setActiveView(null);
    setOrder(next);
    setPages(1);
  }
  /** A view-related outcome, said in the notice under the bar (where the Views button is). */
  function announce(text: string) {
    actions.announce(text);
    setBulkFeedback(true);
    setBulkResult(null);
  }
  // A saved view sets the tab, filters and order at once and starts afresh: no search, no selection.
  function applyView(view: SavedView, choice: ChosenView) {
    clearSelection();
    setTab(choice.tab);
    setFilters(choice.filters);
    setOrder(choice.order);
    setSearch("");
    setQ("");
    setPages(1);
    setActiveView({ id: view.id, name: view.name, key: choice.key });
    announce(`Showing the view “${view.name}”.`);
  }

  const servicesHref = tenantId ? `/services?client_id=${encodeURIComponent(tenantId)}` : "/portal/services";
  const connectHref = canConnectWhatsApp(me.role) ? servicesHref : null;

  function select(id: string) {
    if (id !== selected) actions.clearFeedback();
    setBulkFeedback(false);
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
  }, []);
  // Back hides the thread and shows the list in one commit. The Back button vanishes with the
  // thread, so focus goes to the row that was open, or to the list heading when that row is gone
  // (filtered out, or disabled while an action is pending). The list comes back where it was, then
  // the row is nudged clear of the sticky tab strip (rows carry a scroll margin for it).
  useEffect(() => {
    if (threadOpen || !focusAfterBack.current) return;
    focusAfterBack.current = false;
    window.scrollTo({ top: listScroll.current });
    const row = [...document.querySelectorAll<HTMLElement>("[data-conversation-id]")]
      .find((el) => el.dataset.conversationId === selected && !(el instanceof HTMLButtonElement && el.disabled));
    const target = row ?? document.querySelector<HTMLElement>("[data-conversation-list-heading]");
    target?.focus({ preventScroll: true });
    row?.scrollIntoView({ block: "nearest" });
  }, [threadOpen, selected]);

  // The bar's height, so the panes below it fill the rest of the screen at md and wider.
  const barRef = useRef<HTMLDivElement>(null);
  const [barHeight, setBarHeight] = useState<number | null>(null);
  useEffect(() => {
    const bar = barRef.current;
    if (!bar || typeof ResizeObserver !== "function") return;
    const observer = new ResizeObserver(() => setBarHeight(Math.ceil(bar.getBoundingClientRect().height)));
    observer.observe(bar);
    return () => observer.disconnect();
  }, []);

  // The card is a slide-over only below the three-pane width, and only for an open conversation.
  const slideOpen = detailsOpen && !wide && selected !== "";

  // The shortcuts at the document level; the handler is refreshed every render. X is the list's own
  // (it toggles the focused row). Nothing fires while an overlay (a dialog, the slide-over, the
  // Filters popover, a menu) is open anywhere, nor from inside one.
  const keyHandler = useRef<(event: KeyboardEvent) => void>(() => {});
  useEffect(() => {
    keyHandler.current = (event) => {
      // A control that handled the key itself (a menu, the reply question) marks it: leave it alone.
      if (event.defaultPrevented) return;
      const dialogOpen = slideOpen || document.querySelector(OPEN_OVERLAY_SELECTOR) !== null;
      // A selection box is an input, but Escape on it still clears the selection.
      const onBox = event.key === "Escape" && event.target instanceof HTMLInputElement && event.target.type === "checkbox";
      const shortcut = shortcutFor({
        key: event.key, ctrlKey: event.ctrlKey, metaKey: event.metaKey, altKey: event.altKey,
        repeat: event.repeat, target: onBox ? null : event.target as KeyLike["target"],
      }, { dialogOpen });
      if (!shortcut || shortcut === "toggle") return;
      if (shortcut === "help") {
        event.preventDefault();
        setHelpOpen(true);
        return;
      }
      if (shortcut === "close") {
        const phone = window.matchMedia?.(PHONE_QUERY).matches;
        if (threadOpen && phone) {
          if (actions.pending === null) back();
        } else if (chosen.length > 0 || selectMode) {
          event.preventDefault();
          clearSelection();
        }
        return;
      }
      // One action at a time: while a request is out (or kept for Retry) no key starts another.
      if (actions.blocked) return;
      if (shortcut === "search") {
        const box = document.querySelector<HTMLInputElement>("[data-testid='inbox-search']");
        if (!shown(box)) return;
        // Taken here, so the slash is not typed into the box focus moves to.
        event.preventDefault();
        box.focus();
        return;
      }
      if (shortcut === "claim" || shortcut === "resolve" || shortcut === "snooze") {
        // The open conversation's own buttons, pressed as a click would: the same request, the same
        // refusal sentences, and nothing when the button is not offered, disabled or not on screen.
        const thread = selected ? document.querySelector<HTMLElement>(
          `[data-testid='inbox-thread'][data-conversation="${CSS.escape(selected)}"]`) : null;
        const button = shortcut === "snooze"
          ? thread?.querySelector<HTMLButtonElement>("[data-testid='inbox-snooze-menu']") ?? null
          : thread?.querySelector<HTMLButtonElement>("[data-testid='inbox-primary-action']") ?? null;
        if (!shown(button) || button.disabled) return;
        if (shortcut === "snooze" ? button.getAttribute("aria-expanded") === "true"
          : !PRIMARY_FOR[shortcut]?.includes(button.dataset.action ?? "")) return;
        event.preventDefault();
        button.click();
        return;
      }
      const next = neighbourId(items.map((item) => item.id), selected, shortcut);
      if (!next || next === selected) return;
      event.preventDefault();
      actions.clearFeedback();
      setBulkFeedback(false);
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
  const feedbackTop = !selected || bulkFeedback;
  return <div ref={rootRef} data-motion={motion ? "on" : "off"} className="mt-6 min-w-0 space-y-4"
    style={barHeight === null ? undefined : { ["--inbox-bar-h" as string]: `${barHeight}px` }}>
    {/* At md and wider the whole bar is sticky and the panes fill the screen below it. On a phone
        the box dissolves (`contents`) so only its tab strip sticks, within this whole column, and
        the bar is hidden while a thread fills the screen. */}
    <div ref={barRef} data-testid="inbox-queue-bar"
      className={`${selected && threadOpen ? "hidden md:block" : "max-md:contents"} min-w-0 md:sticky md:top-0 md:z-20 md:bg-[var(--fs-bg)] md:pt-2`}>
      <QueueBar tab={tab} onTab={chooseTab} counts={counts?.value ?? null} nudge={nudge} announcement={announcement}
        panelId={panelId} search={search} onSearch={setSearch}
        onClearSearch={() => { setSearch(""); setQ(""); setPages(1); }}
        filters={filters} onFilters={changeFilters} order={order}
        onOrder={changeOrder}
        chips={chips} onRemoveChip={(key) => changeFilters(withoutChip(filters, tab, key))} onClear={clearFilters}
        query={query} meId={me.user.id}
        viewsSlot={<ViewsMenu query={query} filters={filters} order={order} active={activeView} onApply={applyView}
          onSaved={(view, name, key) => {
            if (view) setActiveView({ id: view.id, name: view.name, key });
            announce(`Saved the view “${name}”.`);
          }} />}
        timesSlot={<ResponseTimes settings={times} canEdit={canAssign} onOpen={() => setTimesAsked(true)} />}
        selectSlot={canSelect ? <SelectToggle on={selectMode} toggleRef={selectToggleRef} disabled={actions.blocked}
          onToggle={() => { setSelectMode((value) => !value); setSelectMessage(""); }}
          canSelectAll={items.length > 0} onSelectAll={selectAll} /> : undefined}
        onShortcuts={() => setHelpOpen(true)}
        bulk={canSelect && chosen.length > 0 ? <BulkBar barRef={bulkBarRef} countRef={bulkCountRef} count={chosen.length} message={selectMessage}
          canSelectAll={chosen.length < Math.min(BULK_LIMIT, items.length)} onSelectAll={selectAll}
          onClear={clearSelection} blocked={actions.blocked} canAssign={canAssign} query={query} meId={me.user.id}
          onRun={runBulk} /> : undefined} />
      {/* On a phone the bar's box is dissolved, so the column's 1rem under it is lost: its rows keep
          0.5rem below them and this keeps the rest. */}
      <div aria-hidden className="h-2 md:hidden" />
    </div>
    {feedbackTop && <Feedback actions={actions} framed={false} bulk={bulkResult} bulkRef={bulkResultRef} />}
    {/* Breakpoints in rem like `md`: Tailwind cannot order a px variant against a rem one. */}
    {/* Below the sticky bar: the screen less the bar, the gap above and the page's bottom padding. */}
    <div className="grid min-w-0 gap-3 md:h-[min(46rem,max(20rem,calc(100dvh_-_var(--inbox-bar-h,7rem)_-_3rem)))] md:grid-cols-[minmax(15rem,17.5rem)_minmax(0,1fr)] min-[68.75rem]:grid-cols-[18rem_minmax(0,1fr)_12rem]">
      <div className={`${PANE} ${selected && threadOpen ? "hidden md:flex" : "flex"}`}>
        <ConversationList items={items} selected={selected} blocked={actions.blocked} now={now}
          tab={tab} meId={me.user.id} checked={picked} selectMode={selectMode}
          onCheck={canSelect ? checkRow : undefined}
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
          onClearSearch={() => { setSearch(""); setQ(""); setPages(1); }}
          firstRun={firstRun} onSeeAll={seeAll} />
      </div>
      {selected ? <OpenConversation key={selected} id={selected} row={row} query={query} revision={actions.revision}
        actions={actions} now={now} motion={motion} wide={wide} cardHost={cardHost}
        threadClass={`${PANE} ${threadOpen ? "flex h-[calc(100dvh-1.5rem)] md:h-auto" : "hidden md:flex"}`}
        cardClass="card hidden min-h-0 min-w-0 overflow-y-auto min-[68.75rem]:block"
        feedback={feedbackTop ? null : <Feedback actions={actions} framed />} onBack={back}
        onDetails={() => setDetailsOpen(true)} onCloseDetails={() => setDetailsOpen(false)} detailsRef={detailsRef}
        me={{ userId: me.user.id, role: me.role }} />
        : <>
          <div className={`${PANE} hidden items-center justify-center p-6 text-center text-sm text-ink-700 md:flex`}>
            Select a conversation to read it.</div>
          <div className="card hidden p-4 text-center text-sm text-ink-700 min-[68.75rem]:block">
            Customer details appear here.</div>
        </>}
    </div>
    <ShortcutHelp open={helpOpen} onClose={() => setHelpOpen(false)} />
    <SlideOver open={slideOpen} onClose={() => setDetailsOpen(false)}
      title="Customer details" returnFocusTo={detailsRef}>
      <div ref={setCardHost} />
    </SlideOver>
  </div>;
}
