"use client";

import { useId } from "react";
import { Search } from "lucide-react";
import CustomerAvatar from "@/components/console/fs/CustomerAvatar";
import { ListState } from "@/components/console/fs/ListStates";
import StatusLabel from "@/components/console/fs/StatusLabel";
import { field, secondary } from "@/components/console/ui";
import { rowTitle, secondaryLine, senderLabel } from "@/lib/customer-identity";
import { listTime, type ChannelFilter, type Contact, type Conversation } from "@/lib/inbox-api";
import { cleanContact, rowSubtitle } from "@/lib/inbox-thread";

export const CHANNEL_NAME = { telegram: "Telegram", whatsapp: "WhatsApp" } as const;
export const NO_CONTACT: Contact = {
  display_name: null, telegram_username: null, phone_masked: null, phone: null, kind: "customer", known: false,
};
export type View = "all" | "review" | "known" | "new";

/** A contact the identity helpers can trust: present, and with a single-@ username. */
export function contactOf(conversation: Pick<Conversation, "contact">): Contact {
  return cleanContact(conversation.contact ?? NO_CONTACT);
}

/** The channel pill. Sky tint from the approved mockup (#0B5A8C on #D3EEFF is 6.4:1). */
export function ChannelBadge({ channel, testId }: { channel: Conversation["channel"]; testId: string }) {
  return <span data-testid={testId}
    className="inline-flex items-center whitespace-nowrap rounded-full bg-[#D3EEFF] px-2 py-0.5 text-xs font-semibold text-[#0B5A8C]">
    {CHANNEL_NAME[channel]}</span>;
}

/** Customer (blue) or New sender (neutral with a dashed edge); the words carry the meaning. */
export function SenderLabel({ known }: { known: boolean }) {
  const text = senderLabel(known);
  return known ? <StatusLabel tone="customer">{text}</StatusLabel>
    : <span className="inline-flex rounded-full outline-1 -outline-offset-1 outline-[#7B8CA5] outline-dashed">
      <StatusLabel tone="neutral">{text}</StatusLabel></span>;
}

export function OwnerLabel({ humanMuted }: { humanMuted: boolean }) {
  return humanMuted ? <StatusLabel tone="team">Staff has taken over</StatusLabel>
    : <StatusLabel tone="neutral">Assistant</StatusLabel>;
}

const CHIPS: { view: View; label: string; testId: string }[] = [
  { view: "all", label: "All", testId: "inbox-filter-all" },
  { view: "review", label: "Needs review", testId: "inbox-filter-needs-review" },
  { view: "known", label: "Customers", testId: "inbox-filter-customers" },
  { view: "new", label: "New senders", testId: "inbox-filter-new-senders" },
];

export type ListStatus = {
  loaded: boolean; error: string; forbidden: boolean; partial: boolean; nextCursor: boolean;
};

export function ConversationList({
  items, status, selected, blocked, now, search, onSearch, view, onView, channel, onChannel,
  reviewCount, nudge, filtered, searching, connectHref, onSelect, onRetry, onLoadOlder,
  onClearFilters, onClearSearch,
}: {
  items: Conversation[]; status: ListStatus; selected: string; blocked: boolean; now: number;
  search: string; onSearch: (value: string) => void; view: View; onView: (view: View) => void;
  channel: ChannelFilter; onChannel: (channel: ChannelFilter) => void;
  /** The server's count for the whole tenant, or null before the first answer. */
  reviewCount: number | null; nudge: number; filtered: boolean; searching: boolean;
  connectHref: string | null; onSelect: (id: string) => void; onRetry: () => void; onLoadOlder: () => void;
  onClearFilters: () => void; onClearSearch: () => void;
}) {
  const searchId = useId();
  const hintId = useId();
  const short = [...search.trim()].length === 1;
  const showRows = !status.forbidden && items.length > 0;
  return <section aria-labelledby={`${searchId}-title`} className="flex min-h-0 min-w-0 flex-1 flex-col">
    <div className="space-y-3 border-b border-[#E8EEF8] p-3">
      <h2 id={`${searchId}-title`} className="text-base font-semibold">Conversations</h2>
      <div>
        <label htmlFor={searchId} className="mb-1 block text-xs font-medium text-ink-700">Search customers</label>
        <div className="relative">
          <Search aria-hidden size={16} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-500" />
          <input id={searchId} type="search" data-testid="inbox-search" className={`${field} pl-9`}
            value={search} maxLength={64} autoComplete="off" placeholder="Name or phone"
            aria-describedby={short ? hintId : undefined} onChange={(event) => onSearch(event.target.value)} />
        </div>
        {short && <p id={hintId} className="mt-1 text-xs text-ink-600">Type at least two characters to search.</p>}
      </div>
      <div role="group" aria-label="Show conversations" className="flex flex-wrap gap-1.5">
        {CHIPS.map((chip) => {
          const pressed = view === chip.view;
          return <button key={chip.view} type="button" data-testid={chip.testId} aria-pressed={pressed}
            onClick={() => onView(chip.view)}
            className={`inline-flex min-h-8 items-center gap-1 rounded-full border px-2.5 pointer-coarse:min-h-11 ${pressed
              ? "border-[var(--fs-primary)] bg-[var(--fs-primary)] text-white"
              : "border-[#D8E1F0] bg-white text-ink-700 hover:border-[var(--fs-primary)]"}`}>
            {/* Sized on a span: the tokens' unlayered `font: inherit` on buttons beats utilities. */}
            <span className={`text-xs ${pressed ? "font-semibold" : "font-medium"}`}>{chip.label}</span>
            {chip.view === "review" && <span key={nudge} data-testid="inbox-needs-review-count"
              aria-label={reviewCount === null ? "count loading" : undefined}
              className={`inline-block rounded-full bg-[#FFF1CC] px-1.5 text-xs font-semibold text-[#854D0E] ${nudge > 0 ? "fs-nudge" : ""}`}>
              {reviewCount ?? "–"}</span>}
          </button>;
        })}
      </div>
      <label className="block"><span className="text-xs font-medium text-ink-700">Channel</span>
        <select className={`${field} mt-1`} value={channel} data-testid="inbox-channel-filter"
          onChange={(event) => onChannel(event.target.value as ChannelFilter)}>
          <option value="">All channels</option>
          <option value="whatsapp">WhatsApp</option>
          <option value="telegram">Telegram</option>
        </select>
      </label>
    </div>
    <div className="min-h-0 flex-1 md:overflow-y-auto" data-inbox-list-scroll>
      <div aria-live="polite">
        {status.forbidden ? <ListState kind="forbidden" />
          : !status.loaded && !status.error ? <ListState kind="loading" />
          : !status.loaded ? <ListState kind="unavailable" onRetry={onRetry} />
          : status.error ? <div className="p-3"><ListState kind={status.partial ? "partial" : "unavailable"} variant="banner"
            onRetry={onRetry} onLoadOlder={onRetry} /></div>
          : items.length === 0 ? (filtered || searching
            ? <div data-testid="inbox-no-match" className="flex flex-col items-center gap-3 px-4 py-8 text-center text-sm">
              <p>{searching ? "No conversations match your search." : "No conversations match these filters."}</p>
              <div className="flex flex-wrap justify-center gap-2">
                {searching && <button type="button" className={secondary} onClick={onClearSearch}>Clear search</button>}
                {filtered && <button type="button" className={secondary} onClick={onClearFilters}>Clear filters</button>}
              </div>
            </div>
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

function Row({ item, selected, disabled, now, onSelect }: {
  item: Conversation; selected: boolean; disabled: boolean; now: number; onSelect: (id: string) => void;
}) {
  const contact = contactOf(item);
  const title = rowTitle(contact);
  const subtitle = rowSubtitle(title, secondaryLine(contact, item.channel), CHANNEL_NAME[item.channel]);
  const preview = item.last_message?.preview ?? "";
  const time = now ? listTime(item.last_message?.at ?? item.opened_at, now) : "";
  return <li>
    <button type="button" data-conversation-id={item.id} aria-current={selected ? "true" : undefined}
      disabled={disabled} onClick={() => onSelect(item.id)}
      className={`flex w-full min-w-0 items-start gap-3 border-b border-[#E8EEF8] px-3 py-3 text-left focus-visible:outline-3 focus-visible:-outline-offset-3 focus-visible:outline-[color:var(--fs-focus)] disabled:cursor-not-allowed disabled:opacity-60 ${selected
        ? "bg-[#EDF2FF] shadow-[inset_3px_0_0_var(--fs-primary)]" : "bg-white hover:bg-[#F6F9FE]"}`}>
      <CustomerAvatar id={item.id} name={contact.display_name} size={40} />
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="flex min-w-0 items-baseline gap-2">
          <span className="min-w-0 flex-1 truncate text-sm font-semibold">{title}</span>
          {time && <span className="shrink-0 text-[11px] text-ink-600">{time}</span>}
        </span>
        {subtitle && <span className="truncate text-xs text-ink-600">{subtitle}</span>}
        {preview && <span className="truncate text-xs text-ink-900">{preview}</span>}
        <span className="mt-1 flex min-w-0 flex-wrap gap-1">
          <SenderLabel known={contact.known} />
          <OwnerLabel humanMuted={item.human_muted} />
          {(item.pending_approvals ?? 0) > 0 && <StatusLabel tone="attention">Needs review</StatusLabel>}
          <ChannelBadge channel={item.channel} testId="inbox-channel-badge" />
        </span>
      </span>
    </button>
  </li>;
}
