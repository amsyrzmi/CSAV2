"use client";

import { memo } from "react";
import { BellRing, ChevronUp, ChevronsUp, MessageCircle, Send } from "lucide-react";
import CustomerAvatar from "@/components/console/fs/CustomerAvatar";
import StatusLabel from "@/components/console/fs/StatusLabel";
import { rowTitle, senderLabel } from "@/lib/customer-identity";
import { listTime, type Contact, type Conversation } from "@/lib/inbox-api";
import { dueLabel, isUnread, rowClock, rowMeta, type QueueTabId } from "@/lib/inbox-queue";
import { cleanContact } from "@/lib/inbox-thread";

export const CHANNEL_NAME = { telegram: "Telegram", whatsapp: "WhatsApp" } as const;
export const NO_CONTACT: Contact = {
  display_name: null, telegram_username: null, phone_masked: null, phone: null, kind: "customer", known: false,
};

/** A contact the identity helpers can trust: present, and with a single-@ username. */
export function contactOf(conversation: Pick<Conversation, "contact">): Contact {
  return cleanContact(conversation.contact ?? NO_CONTACT);
}

/** Customer (blue) or New sender (neutral with a dashed edge); the words carry the meaning. */
export function SenderLabel({ known }: { known: boolean }) {
  const text = senderLabel(known);
  return known ? <StatusLabel tone="customer">{text}</StatusLabel>
    : <span className="inline-flex rounded-full outline-1 -outline-offset-1 outline-[color:var(--fs-border-control)] outline-dashed">
      <StatusLabel tone="neutral">{text}</StatusLabel></span>;
}

/** Small marker for a row with a held reply: an icon and the words, never colour alone. */
function ReviewMarker() {
  return <span data-testid="inbox-row-review" data-row-part="review"
    className="inline-flex items-center gap-1 whitespace-nowrap rounded-full bg-[var(--fs-warning-bg)] px-2 py-0.5 text-xs font-semibold text-[color:var(--fs-warning)]">
    <BellRing aria-hidden size={12} />Needs review</span>;
}

/** The channel as a small icon; the name is its accessible name, its tooltip and hidden text. */
function ChannelIcon({ channel }: { channel: Conversation["channel"] }) {
  const name = CHANNEL_NAME[channel];
  const Icon = channel === "whatsapp" ? MessageCircle : Send;
  return <span data-testid="inbox-channel-badge" data-row-part="channel" role="img" aria-label={name} title={name}
    className="inline-flex shrink-0 items-center text-ink-600">
    <Icon aria-hidden size={14} strokeWidth={1.75} /><span className="sr-only">{name}</span></span>;
}

export type ConversationRowProps = {
  item: Conversation;
  selected: boolean;
  disabled: boolean;
  /** Epoch milliseconds from the page's ticker; 0 before the first tick (no clock is shown then). */
  now: number;
  tab: QueueTabId;
  /** The signed-in person's real user id, so "You" is right for every role. */
  me: { userId: string | null };
  /** Touch "Select" mode: the checkbox always shows. */
  selectMode?: boolean;
  checked?: boolean;
  /** Without it there is no checkbox at all. */
  onCheck?: (id: string, checked: boolean) => void;
  onSelect: (id: string) => void;
};

const META_TEXT = "text-xs text-ink-700";

/**
 * One conversation in three lines: the name and the clock, the last message, and a meta line with
 * only what applies (state, due, High or Urgent, the reason, who has it, New sender, the channel).
 * The row opens with one button; the selection checkbox is its sibling, never nested inside it.
 */
function Row({ item, selected, disabled, now, tab, me, selectMode = false, checked = false, onCheck, onSelect }:
  ConversationRowProps) {
  const contact = contactOf(item);
  const title = rowTitle(contact);
  const preview = item.last_message?.preview ?? "";
  const clock = now ? rowClock(item, tab, now) : null;
  const time = !clock ? "" : clock.kind === "wait" ? clock.text : listTime(clock.at, now);
  const meta = rowMeta(item, now, me);
  const due = now ? dueLabel(meta.due) : null;
  const review = (item.pending_approvals ?? 0) > 0;
  // The review marker already says a held reply waits; the reason would say it twice.
  const reason = review && item.queue_reason === "held_reply" ? null : meta.reason;
  const PriorityIcon = meta.priority?.text === "Urgent" ? ChevronsUp : ChevronUp;
  const surface = selected ? "bg-[var(--fs-surface-blue)]" : "bg-[var(--fs-surface)] group-hover/row:bg-[var(--fs-tile)]";
  return <li className="group/row relative">
    {onCheck && <label className={`absolute left-3 top-2 z-10 grid size-10 place-items-center rounded-full ${surface} ${
      checked || selectMode ? "" : "invisible group-hover/row:visible group-focus-within/row:visible"}`}>
      <input type="checkbox" aria-label={`Select ${title}`} checked={checked} disabled={disabled}
        onChange={(event) => onCheck(item.id, event.target.checked)}
        className="size-4 shrink-0 accent-[var(--fs-primary)] max-md:scroll-mt-14 pointer-coarse:size-6 focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]" />
    </label>}
    <button type="button" data-conversation-id={item.id} aria-current={selected ? "true" : undefined}
      disabled={disabled} onClick={() => onSelect(item.id)}
      className={`flex w-full max-md:scroll-mt-14 min-w-0 items-start gap-3 border-b border-[color:var(--fs-ink-100)] px-3 py-2 text-left focus-visible:outline-3 focus-visible:-outline-offset-3 focus-visible:outline-[color:var(--fs-focus)] disabled:cursor-not-allowed disabled:opacity-60 ${selected
        ? "bg-[var(--fs-surface-blue)] shadow-[inset_3px_0_0_var(--fs-primary)]" : "bg-[var(--fs-surface)] group-hover/row:bg-[var(--fs-tile)]"}`}>
      <CustomerAvatar id={item.id} name={contact.display_name} size={40} />
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="flex min-w-0 items-baseline gap-2">
          {/* Heavier only while the customer's newest message is unanswered: no dot, no colour. */}
          <span className={`min-w-0 flex-1 truncate text-sm ${isUnread(item) ? "font-bold" : "font-semibold"}`}>{title}</span>
          {clock && time && <span className="shrink-0 text-[11px] text-ink-600">
            {clock.kind === "wait" && <span className="sr-only">Waiting </span>}
            <span data-testid="inbox-row-time" title={clock.title || undefined} className="tabular-nums">{time}</span>
          </span>}
        </span>
        {preview && <span data-testid="inbox-row-preview" className="block truncate text-xs text-ink-900">{preview}</span>}
        <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          {review && <ReviewMarker />}
          {meta.state && <span data-row-part="state" className="inline-flex">
            <StatusLabel tone={meta.state.tone}>{meta.state.text}</StatusLabel></span>}
          {due && <span data-row-part="due" className="inline-flex">
            <StatusLabel tone={due.tone}>{due.text}</StatusLabel></span>}
          {meta.priority && <span data-row-part="priority"
            className="inline-flex items-center gap-0.5 whitespace-nowrap text-xs font-semibold text-ink-900">
            <PriorityIcon aria-hidden size={14} strokeWidth={1.75} />{meta.priority.text}</span>}
          {reason && <span data-row-part="reason" className={`whitespace-nowrap ${META_TEXT}`}>{reason}</span>}
          {meta.assignee && <span data-row-part="assignee" className={`inline-flex min-w-0 items-center gap-1 ${META_TEXT}`}>
            <span aria-hidden
              className="inline-grid size-5 shrink-0 place-items-center rounded-full bg-[var(--fs-team-bg)] text-[10px] font-semibold leading-none text-[color:var(--fs-team-text)]">
              {meta.assignee.initials}</span>
            <span className="sr-only">Assigned to </span><span className="min-w-0 truncate">{meta.assignee.text}</span>
          </span>}
          {!contact.known && <span data-row-part="sender" className="inline-flex"><SenderLabel known={false} /></span>}
          <ChannelIcon channel={item.channel} />
        </span>
      </span>
    </button>
  </li>;
}

/**
 * Customer messages and escalations do not raise `inbox_version` (docs/CONSOLE_API.md), so the row
 * re-renders when its data differs, not only its version. Callbacks are not compared: the list
 * passes ones that stay the same.
 */
function sameRow(a: ConversationRowProps, b: ConversationRowProps): boolean {
  return a.now === b.now && a.selected === b.selected && a.checked === b.checked && a.disabled === b.disabled
    && a.tab === b.tab && a.me.userId === b.me.userId && a.selectMode === b.selectMode
    && (a.onCheck === undefined) === (b.onCheck === undefined)
    && (a.item === b.item || (a.item.id === b.item.id && a.item.inbox_version === b.item.inbox_version
      && JSON.stringify(a.item) === JSON.stringify(b.item)));
}

export const ConversationRow = memo(Row, sameRow);
