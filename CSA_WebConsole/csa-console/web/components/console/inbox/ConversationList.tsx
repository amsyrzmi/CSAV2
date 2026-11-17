"use client";

import { useId } from "react";
import { BellRing, SearchX } from "lucide-react";
import CustomerAvatar from "@/components/console/fs/CustomerAvatar";
import { ListState } from "@/components/console/fs/ListStates";
import StatusLabel from "@/components/console/fs/StatusLabel";
import { secondary } from "@/components/console/ui";
import { rowTitle, secondaryLine, senderLabel } from "@/lib/customer-identity";
import { listTime, type Contact, type Conversation } from "@/lib/inbox-api";
import { cleanContact, rowSubtitle } from "@/lib/inbox-thread";

export const CHANNEL_NAME = { telegram: "Telegram", whatsapp: "WhatsApp" } as const;
export const NO_CONTACT: Contact = {
  display_name: null, telegram_username: null, phone_masked: null, phone: null, kind: "customer", known: false,
};

/** A contact the identity helpers can trust: present, and with a single-@ username. */
export function contactOf(conversation: Pick<Conversation, "contact">): Contact {
  return cleanContact(conversation.contact ?? NO_CONTACT);
}

/** The channel pill. Sky tint from the approved mockup; the --fs-channel-* pair is contrast-tested in every theme. */
export function ChannelBadge({ channel, testId }: { channel: Conversation["channel"]; testId: string }) {
  return <span data-testid={testId}
    className="inline-flex items-center whitespace-nowrap rounded-full bg-[var(--fs-channel-bg)] px-2 py-0.5 text-xs font-semibold text-[color:var(--fs-channel-text)]">
    {CHANNEL_NAME[channel]}</span>;
}

/** Customer (blue) or New sender (neutral with a dashed edge); the words carry the meaning. */
export function SenderLabel({ known }: { known: boolean }) {
  const text = senderLabel(known);
  return known ? <StatusLabel tone="customer">{text}</StatusLabel>
    : <span className="inline-flex rounded-full outline-1 -outline-offset-1 outline-[color:var(--fs-border-control)] outline-dashed">
      <StatusLabel tone="neutral">{text}</StatusLabel></span>;
}

export function OwnerLabel({ humanMuted }: { humanMuted: boolean }) {
  return humanMuted ? <StatusLabel tone="team">Staff has taken over</StatusLabel>
    : <StatusLabel tone="neutral">Assistant</StatusLabel>;
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
  items, status, selected, blocked, now, filtered, searching, connectHref, panelId, labelledBy,
  onSelect, onRetry, onLoadOlder, onClearFilters, onClearSearch,
}: {
  items: Conversation[]; status: ListStatus; selected: string; blocked: boolean; now: number;
  filtered: boolean; searching: boolean; connectHref: string | null;
  /** The id the tabs point at, and the selected tab's id that names this panel. */
  panelId: string; labelledBy: string;
  onSelect: (id: string) => void; onRetry: () => void; onLoadOlder: () => void;
  onClearFilters: () => void; onClearSearch: () => void;
}) {
  const headingId = useId();
  const showRows = !status.forbidden && items.length > 0;
  return <section id={panelId} role="tabpanel" aria-labelledby={labelledBy}
    className="flex min-h-0 min-w-0 flex-1 flex-col">
    <h2 id={headingId} tabIndex={-1} data-conversation-list-heading className="sr-only">Conversations</h2>
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
      {showRows && <ul aria-label="Conversations">{items.map((item) => <Row key={item.id} item={item}
        selected={item.id === selected} disabled={blocked} now={now} onSelect={onSelect} />)}</ul>}
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

/** Small marker for a row with a held reply: an icon and the words, never colour alone. */
function ReviewMarker() {
  return <span data-testid="inbox-row-review"
    className="inline-flex items-center gap-1 whitespace-nowrap rounded-full bg-[var(--fs-warning-bg)] px-2 py-0.5 text-xs font-semibold text-[color:var(--fs-warning)]">
    <BellRing aria-hidden size={12} />Needs review</span>;
}

function Row({ item, selected, disabled, now, onSelect }: {
  item: Conversation; selected: boolean; disabled: boolean; now: number; onSelect: (id: string) => void;
}) {
  const contact = contactOf(item);
  const title = rowTitle(contact);
  const subtitle = rowSubtitle(title, secondaryLine(contact, item.channel), CHANNEL_NAME[item.channel]);
  const preview = item.last_message?.preview ?? "";
  const time = now ? listTime(item.last_message?.at ?? item.opened_at, now) : "";
  const review = (item.pending_approvals ?? 0) > 0;
  return <li>
    <button type="button" data-conversation-id={item.id} aria-current={selected ? "true" : undefined}
      disabled={disabled} onClick={() => onSelect(item.id)}
      className={`flex w-full min-w-0 items-start gap-3 border-b border-[color:var(--fs-ink-100)] px-3 py-3.5 text-left focus-visible:outline-3 focus-visible:-outline-offset-3 focus-visible:outline-[color:var(--fs-focus)] disabled:cursor-not-allowed disabled:opacity-60 ${selected
        ? "bg-[var(--fs-surface-blue)] shadow-[inset_3px_0_0_var(--fs-primary)]" : "bg-[var(--fs-surface)] hover:bg-[var(--fs-tile)]"}`}>
      <CustomerAvatar id={item.id} name={contact.display_name} size={40} />
      <span className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="flex min-w-0 items-baseline gap-2">
          <span className="min-w-0 flex-1 truncate text-sm font-semibold">{title}</span>
          {time && <span data-testid="inbox-row-time" className="shrink-0 text-[11px] text-ink-600">{time}</span>}
        </span>
        {preview && <span data-testid="inbox-row-preview" className="block truncate text-xs text-ink-900">{preview}</span>}
        {subtitle && <span className="truncate text-xs text-ink-600">{subtitle}</span>}
        <span className="mt-0.5 flex min-w-0 flex-wrap items-center gap-1">
          {review && <ReviewMarker />}
          <SenderLabel known={contact.known} />
          <OwnerLabel humanMuted={item.human_muted} />
          <ChannelBadge channel={item.channel} testId="inbox-channel-badge" />
        </span>
      </span>
    </button>
  </li>;
}
