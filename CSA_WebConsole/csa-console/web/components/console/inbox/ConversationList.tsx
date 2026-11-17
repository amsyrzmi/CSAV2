"use client";

import { useCallback, useId, useLayoutEffect, useRef } from "react";
import { SearchX } from "lucide-react";
import { ListState } from "@/components/console/fs/ListStates";
import { secondary } from "@/components/console/ui";
import type { Conversation } from "@/lib/inbox-api";
import type { QueueTabId } from "@/lib/inbox-queue";
import { CHANNEL_NAME, ConversationRow } from "./ConversationRow";
import { PHONE_QUERY } from "./hooks";

// The row's helpers, still imported from here by the thread and the customer card.
export { CHANNEL_NAME, contactOf, NO_CONTACT, SenderLabel } from "./ConversationRow";

/** The channel pill. Sky tint from the approved mockup; the --fs-channel-* pair is contrast-tested in every theme. */
export function ChannelBadge({ channel, testId }: { channel: Conversation["channel"]; testId: string }) {
  return <span data-testid={testId}
    className="inline-flex items-center whitespace-nowrap rounded-full bg-[var(--fs-channel-bg)] px-2 py-0.5 text-xs font-semibold text-[color:var(--fs-channel-text)]">
    {CHANNEL_NAME[channel]}</span>;
}

export type ListStatus = {
  loaded: boolean; error: string; forbidden: boolean; partial: boolean; nextCursor: boolean;
  /** The server's plain-words refusal to show instead of the default no-access copy. */
  refusal: string | null;
};

/**
 * The rows, their states and "Load older": the panel the queue tabs control. Search, filters and
 * sort live in the queue bar above; the heading is for screen readers and for focus to return to.
 */
export function ConversationList({
  items, status, selected, blocked, now, tab, meId, filtered, searching, connectHref, panelId, labelledBy,
  checked, selectMode, onCheck, onSelect, onRetry, onLoadOlder, onClearFilters, onClearSearch,
}: {
  items: Conversation[]; status: ListStatus; selected: string; blocked: boolean; now: number;
  /** The open tab (the queue tabs show the wait) and the signed-in person's real id (for "You"). */
  tab: QueueTabId; meId: string | null;
  filtered: boolean; searching: boolean; connectHref: string | null;
  /** The id the tabs point at, and the selected tab's id that names this panel. */
  panelId: string; labelledBy: string;
  /** The selected ids for bulk actions, and Select mode (every box shows and is a tab stop). */
  checked: ReadonlySet<string>; selectMode: boolean;
  /** Without it (a read-only role) rows have no selection box. */
  onCheck?: (id: string, checked: boolean) => void;
  onSelect: (id: string) => void; onRetry: () => void; onLoadOlder: () => void;
  onClearFilters: () => void; onClearSearch: () => void;
}) {
  const headingId = useId();
  // Rows are memoised and do not compare callbacks: they get one that always calls the newest.
  const latestSelect = useRef(onSelect);
  useLayoutEffect(() => { latestSelect.current = onSelect; });
  const selectRow = useCallback((id: string) => latestSelect.current(id), []);
  const latestCheck = useRef(onCheck);
  useLayoutEffect(() => { latestCheck.current = onCheck; });
  const checkRow = useCallback((id: string, on: boolean) => latestCheck.current?.(id, on), []);
  const hintId = useId();
  const me = { userId: meId };
  const showRows = !status.forbidden && items.length > 0;
  return <section id={panelId} role="tabpanel" aria-labelledby={labelledBy}
    className="flex min-h-0 min-w-0 flex-1 flex-col">
    <h2 id={headingId} tabIndex={-1} data-conversation-list-heading className="sr-only">Conversations</h2>
    {onCheck && <p id={hintId} className="sr-only">Press X on a conversation to select it or clear it.</p>}
    <div className="min-h-0 flex-1 md:overflow-y-auto" data-inbox-list-scroll>
      <div aria-live="polite">
        {status.forbidden ? <ListState kind="forbidden" message={status.refusal ?? undefined} />
          : !status.loaded && !status.error ? <ListState kind="loading" />
          : !status.loaded ? <ListState kind="unavailable" onRetry={onRetry} />
          : status.error ? <div className="p-3"><ListState kind={status.partial ? "partial" : "unavailable"} variant="banner"
            onRetry={onRetry} onLoadOlder={onRetry} /></div>
          : items.length === 0 ? (filtered || searching
            ? <NoMatch searching={searching} filtered={filtered} onClearSearch={onClearSearch}
              onClearFilters={onClearFilters} />
            : <ListState kind="empty" connectHref={connectHref} />)
          : null}
      </div>
      {/* On a phone the page scrolls the list under the sticky tab strip: a focused row is moved
          clear of it (rows carry a matching scroll margin, which J and K's scrolling uses too). */}
      {/* X on a focused row toggles its box: the keyboard route to selecting while the boxes are
          not tab stops (outside Select mode). Space and Enter stay the row button's own. */}
      {showRows && <ul aria-label="Conversations" aria-describedby={onCheck ? hintId : undefined} onFocus={(event) => {
        if (window.matchMedia?.(PHONE_QUERY).matches && event.target instanceof HTMLElement) {
          event.target.scrollIntoView({ block: "nearest" });
        }
      }} onKeyDown={(event) => {
        if (!onCheck || blocked || (event.key !== "x" && event.key !== "X")) return;
        if (event.ctrlKey || event.metaKey || event.altKey) return;
        const id = event.target instanceof HTMLElement ? event.target.dataset.conversationId : undefined;
        if (!id) return;
        event.preventDefault();
        onCheck(id, !checked.has(id));
      }}>{items.map((item) => <ConversationRow key={item.id} item={item} selected={item.id === selected}
        disabled={blocked} now={now} tab={tab} me={me} selectMode={selectMode} checked={checked.has(item.id)}
        onCheck={onCheck ? checkRow : undefined} onSelect={selectRow} />)}</ul>}
      {showRows && status.nextCursor && !status.error && <div className="p-3">
        <button type="button" className={`${secondary} w-full`} onClick={onLoadOlder}>Load older conversations</button>
      </div>}
    </div>
  </section>;
}

/** Nothing matched: the old sentence, a plain hint, and the one action that gets rows back. */
function NoMatch({ searching, filtered, onClearSearch, onClearFilters }: {
  searching: boolean; filtered: boolean; onClearSearch: () => void; onClearFilters: () => void;
}) {
  const both = searching && filtered;
  return <div data-testid="inbox-no-match" className="flex flex-col items-center gap-3 px-4 py-8 text-center text-sm">
    <SearchX aria-hidden size={28} className="text-ink-500" />
    <div className="space-y-1">
      <p className="font-medium">{searching ? "No conversations match your search." : "No conversations match these filters."}</p>
      <p className="text-xs text-ink-700">{searching
        ? "Check the spelling, or try part of the name or the phone number."
        : "Clear the filters to see everything in this tab again."}</p>
    </div>
    {both ? <button type="button" className={secondary} data-testid="inbox-no-match-action"
      onClick={() => { onClearSearch(); onClearFilters(); }}>Clear search and filters</button>
      : searching ? <button type="button" className={secondary} data-testid="inbox-no-match-action"
        onClick={onClearSearch}>Clear search</button>
        : <button type="button" className={secondary} data-testid="inbox-no-match-action"
          onClick={onClearFilters}>Clear filters</button>}
  </div>;
}
