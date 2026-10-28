"use client";

import { useId, useState } from "react";
import CustomerAvatar from "@/components/console/fs/CustomerAvatar";
import StatusLabel from "@/components/console/fs/StatusLabel";
import { secondary } from "@/components/console/ui";
import { rowTitle } from "@/lib/customer-identity";
import { approvalsPath, type Approval, type Conversation } from "@/lib/inbox-api";
import { dateTime } from "@/lib/inbox-thread";
import { CHANNEL_NAME, contactOf, SenderLabel } from "./ConversationList";
import { ReviewCard } from "./ReviewCard";
import type { InboxActions } from "./useInboxActions";
import { usePages } from "./usePages";

/**
 * Who the customer is, who owns the conversation, and its reply history. The full phone
 * number comes only from the single-conversation read. Pending replies are decided in the
 * thread; handled ones stay reachable here behind "Include handled approvals".
 */
export function CustomerCard({
  id, row, current, query, revision, now, actions, inDialog, onView, includeHandled, onIncludeHandled,
}: {
  id: string; row: Conversation | undefined; current: Conversation | null; query: string; revision: number;
  now: number; actions: InboxActions; inDialog: boolean; onView: () => void;
  includeHandled: boolean; onIncludeHandled: (value: boolean) => void;
}) {
  const titleId = useId();
  const shown = current ?? row ?? null;
  const contact = contactOf(shown ?? { contact: null });
  const title = rowTitle(contact);
  const Title = inDialog ? "h3" : "h2";
  const waiting = shown?.pending_approvals ?? 0;
  const term = "text-xs text-ink-700";
  const value = "mt-0.5 text-sm font-medium [overflow-wrap:anywhere]";
  return <section aria-labelledby={titleId} data-testid="inbox-customer-card" className="min-w-0 p-3">
    <div className="flex flex-col items-center text-center">
      <CustomerAvatar id={id} name={contact.display_name} size={64} />
      <Title id={titleId} className="mt-2 text-base font-semibold [overflow-wrap:anywhere]">{title}</Title>
      {contact.telegram_username && <p className="text-xs text-ink-700 [overflow-wrap:anywhere]">@{contact.telegram_username}</p>}
      <div className="mt-1.5"><SenderLabel known={contact.known} /></div>
    </div>
    <dl className="mt-4 space-y-2.5">
      <div><dt className={term}>Phone</dt>
        <dd className={value}>{current ? (current.contact?.phone ?? <span className="text-ink-600">Not shared yet</span>)
          : <span className="text-ink-600">Loading…</span>}</dd></div>
      {shown && <div><dt className={term}>Channel</dt><dd className={value}>{CHANNEL_NAME[shown.channel]}</dd></div>}
      {shown && <div><dt className={term}>Conversation owner</dt>
        <dd className={value}>{shown.human_muted ? "Staff" : "Assistant"}</dd></div>}
      {shown && <div><dt className={term}>First message</dt>
        <dd className={value}><time dateTime={shown.opened_at}>{dateTime(shown.opened_at)}</time></dd></div>}
      <div><dt className={term}>Waiting for review</dt>
        <dd className={value}>{waiting > 0
          ? <StatusLabel tone="attention">{waiting === 1 ? "1 reply" : `${waiting} replies`}</StatusLabel> : "None"}</dd></div>
    </dl>
    <div className="mt-5 border-t border-[#E8EEF8] pt-3">
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" className="size-4" checked={includeHandled}
          onChange={(event) => onIncludeHandled(event.target.checked)} />
        Include handled approvals
      </label>
      {includeHandled ? <History id={id} query={query} revision={revision} now={now} actions={actions} onView={onView} />
        : <p className="mt-2 text-xs text-ink-700">Replies waiting for review appear in the conversation.</p>}
    </div>
  </section>;
}

/** Decided and expired replies for this conversation, read-only. Polls only while shown. */
function History({ id, query, revision, now, actions, onView }: {
  id: string; query: string; revision: number; now: number; actions: InboxActions; onView: () => void;
}) {
  const [pages, setPages] = useState(1);
  const history = usePages<Approval>(
    approvalsPath({ tenantQuery: query, conversationId: id, includeHandled: true }), pages, revision);
  const handled = history.items.filter((approval) => approval.status !== "pending");
  return <div className="mt-3 space-y-3" aria-live="polite">
    {history.error && <p className="text-xs text-[var(--fs-danger)]">We could not load the reply history. We will keep trying.</p>}
    {!history.loaded && !history.error && <p className="text-xs text-ink-700">Loading reply history…</p>}
    {history.loaded && handled.length === 0 && <p className="text-xs text-ink-700">No handled replies yet.</p>}
    {handled.map((approval) => <ReviewCard key={approval.id} approval={approval} now={now} blocked={actions.blocked}
      onPropose={(decision) => actions.propose(approval, decision)} onEdit={(text) => actions.edit(approval, text)}
      onView={onView} />)}
    {history.next_cursor !== null && <button type="button" className={`${secondary} w-full`}
      onClick={() => setPages((value) => value + 1)}>Load older approvals</button>}
  </div>;
}
