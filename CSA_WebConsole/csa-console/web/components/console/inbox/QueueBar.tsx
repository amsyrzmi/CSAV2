"use client";

import {
  Fragment, useCallback, useEffect, useId, useLayoutEffect, useRef, useState,
  type KeyboardEvent, type ReactNode, type RefObject,
} from "react";
import { Keyboard, Search, SlidersHorizontal, X } from "lucide-react";
import SlideOver from "@/components/console/fs/SlideOver";
import { useDismiss } from "@/components/console/shell/useDismiss";
import { field, secondary } from "@/components/console/ui";
import type { QueueCounts } from "@/lib/inbox-api";
import {
  priorityOrderAllowed, TABS, type QueueFilters, type QueueOrder, type QueueTabId,
} from "@/lib/inbox-queue";
import { FiltersBody } from "./FiltersPanel";
import { PHONE_QUERY, useMediaQuery } from "./hooks";
import { useAssignees } from "./useQueueData";

const ORDERS: { value: QueueOrder; label: string }[] = [
  { value: "waiting", label: "Oldest waiting" },
  { value: "due", label: "Due soonest" },
  { value: "activity", label: "Newest activity" },
  { value: "priority", label: "Priority" },
];
/** The first four tabs are the work tabs; a thin divider comes before the rest. */
const WORK_TABS = 4;
const ring = "focus-visible:outline-3 focus-visible:outline-[color:var(--fs-focus)]";

/** A member id in the assignee filter, as opposed to anyone, me or unassigned. */
export function isPersonId(assignee: string): boolean {
  return assignee !== "" && assignee !== "me" && assignee !== "none";
}

/**
 * The tab strip: a `tablist` with one tab in the tab order, arrow keys, Home and End to move,
 * Enter or Space (the button's own click) to choose. Each tab is named with its count when the
 * count is known ("Needs us, 12"); an unknown count shows a dash, never 0. The strip scrolls
 * sideways inside its own labelled region when it does not fit.
 */
function QueueTabs({ tab, onTab, counts, nudge, panelId }: {
  tab: QueueTabId; onTab: (tab: QueueTabId) => void; counts: QueueCounts | null; nudge: number; panelId: string;
}) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);
  function onKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    const last = TABS.length - 1;
    const next = event.key === "ArrowRight" ? (index === last ? 0 : index + 1)
      : event.key === "ArrowLeft" ? (index === 0 ? last : index - 1)
        : event.key === "Home" ? 0
          : event.key === "End" ? last
            : null;
    if (next === null) return;
    event.preventDefault();
    buttons.current[next]?.focus();
  }
  // Below md this region is the only sticky part of the bar (its parent box is `contents` there).
  return <div role="region" aria-label="Conversation queues"
    className="min-w-0 overflow-x-auto overscroll-x-contain max-md:sticky max-md:top-0 max-md:z-20 max-md:bg-[var(--fs-bg)]">
    <div role="tablist" aria-label="Conversation queues"
      className="flex w-max min-w-full items-stretch border-b border-[color:var(--fs-border)]">
      {TABS.map((item, index) => {
        const on = item.id === tab;
        const value = item.count && counts ? counts[item.count] : null;
        const known = typeof value === "number";
        const attention = item.id === "needs_us" && known && value > 0;
        return <Fragment key={item.id}>
          {index === WORK_TABS && <span aria-hidden className="mx-1 my-2.5 w-px shrink-0 bg-[var(--fs-border)]" />}
          <button ref={(element) => { buttons.current[index] = element; }} type="button" role="tab"
            id={`${panelId}-tab-${item.id}`} aria-controls={panelId} aria-selected={on} tabIndex={on ? 0 : -1}
            aria-label={known ? `${item.label}, ${value}` : item.label} data-testid={item.testId}
            onClick={() => onTab(item.id)} onKeyDown={(event) => onKeyDown(event, index)}
            className={`-mb-px inline-flex min-h-10 shrink-0 items-center gap-1.5 whitespace-nowrap border-b-2 px-3 pointer-coarse:min-h-11 transition-[border-color,color] duration-150 ease-(--fs-ease-out) motion-reduce:transition-none focus-visible:-outline-offset-3 ${ring} ${on
              ? "border-[color:var(--fs-primary)] text-[color:var(--fs-text)]"
              : "border-transparent text-[color:var(--fs-text-secondary)] hover:border-[color:var(--fs-border-strong)] hover:text-[color:var(--fs-text)]"}`}>
            {/* Sized on a span: the tokens' unlayered `font: inherit` on buttons beats utilities. */}
            <span className={`text-[length:var(--fs-text-sm)] ${on ? "font-semibold" : "font-medium"}`}>{item.label}</span>
            {item.count !== null && <span key={item.id === "needs_us" ? `nudge-${nudge}` : item.id} aria-hidden
              data-testid={item.id === "needs_us" ? "inbox-needs-us-count" : `inbox-count-${item.id}`}
              className={`num inline-flex min-w-5 justify-center rounded-full px-1.5 text-xs font-semibold ${attention
                ? "bg-[var(--fs-warning-bg)] text-[color:var(--fs-warning)]"
                : "bg-[var(--fs-neutral-bg)] text-[color:var(--fs-neutral-text)]"} ${item.id === "needs_us" && nudge > 0 ? "fs-nudge" : ""}`}>
              {known ? value : "–"}</span>}
          </button>
        </Fragment>;
      })}
    </div>
  </div>;
}

const POPOVER_WIDTH = 416; // 26rem
const EDGE = 16;

/**
 * The popover body: focus moves into it when it opens. It hangs from the Filters button, moved
 * left as far as needed (and narrowed on a small screen) so it never passes the viewport's edges.
 */
function Popover({ titleId, anchorRef, children }: {
  titleId: string; anchorRef: RefObject<HTMLElement | null>; children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const [place, setPlace] = useState<{ left: number; width: number } | null>(null);
  useLayoutEffect(() => {
    function measure() {
      const anchor = anchorRef.current;
      if (!anchor) return;
      const viewport = document.documentElement.clientWidth;
      const width = Math.min(POPOVER_WIDTH, viewport - 2 * EDGE);
      const start = anchor.getBoundingClientRect().left;
      // Offsets are from the button's left edge: shift left to fit, but never past the left margin.
      const left = Math.max(EDGE - start, Math.min(0, viewport - EDGE - width - start));
      setPlace({ left, width });
    }
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [anchorRef]);
  useEffect(() => {
    ref.current?.querySelector<HTMLElement>("input:not(:disabled), button:not(:disabled)")?.focus();
  }, []);
  return <div ref={ref} role="dialog" aria-labelledby={titleId} data-testid="inbox-queue-filters-panel"
    style={place ?? { left: 0, width: POPOVER_WIDTH }}
    className="absolute top-full z-30 mt-2 max-h-[min(36rem,calc(100dvh-10rem))] overflow-y-auto overscroll-contain rounded-[var(--fs-radius-tile)] border border-[color:var(--fs-border)] bg-[var(--fs-surface-raised)] p-4 text-[color:var(--fs-text)] shadow-[var(--fs-shadow-popover)]">
    <h2 id={titleId} className="mb-3 font-(family-name:--fs-font-heading) text-base font-semibold">Filters</h2>
    {children}
  </div>;
}

/**
 * The queue bar above the panes: the tabs with their counts, then search, Filters, Views (the
 * saved views menu), Response times (the company's response targets), Select and Sort, then the
 * removable filter chips. While conversations are selected, `bulk` (the bulk bar) takes the second line's place.
 */
export function QueueBar({
  tab, onTab, counts, nudge, announcement, panelId, search, onSearch, onClearSearch, filters, onFilters,
  order, onOrder, chips, onRemoveChip, onClear, query, meId, viewsSlot, timesSlot, selectSlot, bulk,
  onShortcuts,
}: {
  tab: QueueTabId; onTab: (tab: QueueTabId) => void;
  /** The server's counts, or null before the first answer and when it could not read them. */
  counts: QueueCounts | null; nudge: number; announcement: string; panelId: string;
  search: string; onSearch: (value: string) => void; onClearSearch: () => void;
  filters: QueueFilters; onFilters: (next: QueueFilters) => void;
  order: QueueOrder; onOrder: (order: QueueOrder) => void;
  chips: { key: string; label: string }[]; onRemoveChip: (key: string) => void; onClear: () => void;
  query: string; meId: string; viewsSlot?: ReactNode; timesSlot?: ReactNode;
  /** The Select mode toggle (and Select all loaded while it is on), just before Sort. */
  selectSlot?: ReactNode;
  /** The bulk bar, shown in place of search, Filters and Sort while anything is selected. */
  bulk?: ReactNode;
  /** Opens the keyboard shortcuts help (the same as "?"). */
  onShortcuts?: () => void;
}) {
  const searchId = useId();
  const hintId = useId();
  const sortId = useId();
  const titleId = useId();
  const searchRef = useRef<HTMLInputElement>(null);
  const filtersRoot = useRef<HTMLDivElement>(null);
  const filtersButton = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  // The team list is read the first time the panel opens (or when a chip names a person).
  const [asked, setAsked] = useState(false);
  const team = useAssignees(query, asked || isPersonId(filters.assignee));
  const sheet = useMediaQuery(PHONE_QUERY);
  const close = useCallback(() => setOpen(false), []);
  const dismiss = useDismiss({ open: open && !sheet, rootRef: filtersRoot, triggerRef: filtersButton, onClose: close });

  const short = [...search.trim()].length === 1;
  const tabLabel = TABS.find((item) => item.id === tab)?.label ?? "";
  const named = chips.map((chip) => {
    const id = chip.key.startsWith("assignee:") ? chip.key.slice("assignee:".length) : "";
    const person = isPersonId(id) ? team.people?.find((p) => p.id === id) : undefined;
    return person ? { ...chip, label: `Assigned to ${person.display_name}` } : chip;
  });
  const body = <FiltersBody tab={tab} tabLabel={tabLabel} filters={filters} onChange={onFilters} team={team}
    meId={meId} chipCount={chips.length} onClear={onClear} onClose={sheet ? undefined : () => {
      close();
      filtersButton.current?.focus();
    }} />;

  return <div className="min-w-0 space-y-2 max-md:contents">
    <QueueTabs tab={tab} onTab={onTab} counts={counts} nudge={nudge} panelId={panelId} />
    {/* Spoken once per burst of changes to the Needs us count, never on every poll. */}
    <p role="status" data-testid="inbox-count-announcement" className="sr-only">{announcement}</p>
    {bulk ?? <div className="flex min-w-0 flex-wrap items-center gap-2">
      <div className="relative min-w-0 flex-[1_1_14rem] md:max-w-xs">
        <Search aria-hidden size={16} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-500" />
        <input ref={searchRef} id={searchId} type="search" data-testid="inbox-search" className={`${field} pr-11 pl-9`}
          value={search} maxLength={64} autoComplete="off" placeholder="Search name or phone"
          aria-label="Search customers" aria-describedby={short ? hintId : undefined}
          onChange={(event) => onSearch(event.target.value)} />
        {search !== "" && <button type="button" data-testid="inbox-search-clear" aria-label="Clear the search box"
          onClick={() => { onClearSearch(); searchRef.current?.focus(); }}
          className={`absolute top-1/2 right-1 inline-flex size-8 -translate-y-1/2 items-center justify-center rounded-[var(--fs-radius-control)] text-ink-700 hover:bg-[var(--fs-tile)] hover:text-ink-900 focus-visible:outline-offset-0 ${ring} pointer-coarse:size-10`}>
          <X aria-hidden size={16} /></button>}
      </div>
      {/* An Escape the popover takes is marked handled, so the Inbox's own Escape leaves it alone. */}
      <div ref={filtersRoot} className="relative" onKeyDown={(event) => {
        if (event.key === "Escape" && open && !sheet) event.preventDefault();
        dismiss.onKeyDown(event);
      }}>
        <button ref={filtersButton} type="button" className={secondary} data-testid="inbox-queue-filters"
          aria-haspopup="dialog" aria-expanded={open}
          aria-label={chips.length ? `Filters, ${chips.length} applied` : "Filters"}
          onClick={() => { setAsked(true); setOpen((value) => !value); }}>
          <SlidersHorizontal aria-hidden size={16} />Filters
          {chips.length > 0 && <span aria-hidden
            className="num inline-flex min-w-5 justify-center rounded-full bg-[var(--fs-surface-blue)] px-1.5 text-xs font-semibold text-[color:var(--fs-primary)]">
            {chips.length}</span>}
        </button>
        {open && !sheet && <Popover titleId={titleId} anchorRef={filtersButton}>{body}</Popover>}
      </div>
      <div data-testid="inbox-views-slot" className="contents">{viewsSlot}</div>
      <div data-testid="inbox-times-slot" className="contents">{timesSlot}</div>
      {selectSlot}
      {onShortcuts && <button type="button" className={`${secondary} w-10 px-0 pointer-coarse:w-11`}
        data-testid="inbox-shortcuts-button" aria-label="Keyboard shortcuts" title="Keyboard shortcuts"
        aria-haspopup="dialog" onClick={onShortcuts}><Keyboard aria-hidden size={16} className="shrink-0" /></button>}
      <div className="ml-auto flex min-w-0 items-center gap-2">
        <label htmlFor={sortId} className="shrink-0 text-sm text-ink-700">Sort</label>
        <select id={sortId} data-testid="inbox-queue-sort" className={`${field.replace("w-full", "w-auto")} min-w-0`}
          value={order} onChange={(event) => onOrder(event.target.value as QueueOrder)}>
          {ORDERS.filter((choice) => choice.value !== "priority" || priorityOrderAllowed(filters)).map((choice) =>
            <option key={choice.value} value={choice.value}>{choice.label}</option>)}
        </select>
      </div>
      {short && <p id={hintId} className="w-full text-xs text-ink-600">Type at least two characters to search.</p>}
    </div>}
    {named.length > 0 && <div data-testid="inbox-queue-chips" className="flex min-w-0 flex-wrap items-center gap-2">
      {named.map((chip) => <button key={chip.key} type="button" aria-label={`Remove filter: ${chip.label}`}
        onClick={() => onRemoveChip(chip.key)}
        className={`inline-flex min-h-10 min-w-0 max-w-full items-center gap-1.5 rounded-[var(--fs-radius-round)] border border-[color:var(--fs-border-control)] bg-[var(--fs-surface)] py-1 pr-2.5 pl-3.5 text-[color:var(--fs-text)] hover:border-[color:var(--fs-primary)] pointer-coarse:min-h-11 focus-visible:outline-offset-2 ${ring}`}>
        <span className="min-w-0 text-[length:var(--fs-text-sm)] font-medium [overflow-wrap:anywhere]">{chip.label}</span>
        <X aria-hidden size={14} className="shrink-0" />
      </button>)}
      <button type="button" onClick={onClear}
        className={`inline-flex min-h-10 items-center rounded-[var(--fs-radius-control)] px-2 text-[color:var(--fs-primary)] underline-offset-2 hover:underline pointer-coarse:min-h-11 focus-visible:outline-offset-2 ${ring}`}>
        <span className="text-[length:var(--fs-text-sm)] font-medium">Clear filters</span>
      </button>
    </div>}
    {sheet && <SlideOver open={open} onClose={close} title="Filters" returnFocusTo={filtersButton}
      widthClass="max-w-none" testId="inbox-queue-filters-panel">{body}</SlideOver>}
  </div>;
}
