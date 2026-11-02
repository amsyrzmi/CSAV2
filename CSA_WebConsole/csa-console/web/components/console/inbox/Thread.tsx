"use client";

import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { ArrowDown, ArrowLeft } from "lucide-react";
import CustomerAvatar from "@/components/console/fs/CustomerAvatar";
import { Skeleton } from "@/components/console/fs/Skeleton";
import { action, field, Notice, secondary } from "@/components/console/ui";
import { rowTitle } from "@/lib/customer-identity";
import type { Approval, Conversation, Message } from "@/lib/inbox-api";
import { dateTime, nearBottom, newMessagesText, threadItems, unseenCount } from "@/lib/inbox-thread";
import { ChannelBadge, contactOf } from "./ConversationList";
import { MessageItem } from "./MessageItem";
import { ReviewCard } from "./ReviewCard";
import type { InboxActions, Pending } from "./useInboxActions";

const timeOf = (at: string | undefined) => (at ? new Date(at).getTime() || 0 : 0);

export function Thread({
  id, row, current, detailError, messages, onOlder, pending, now, motion, query, actions,
  feedback, onBack, onDetails, detailsRef, logRef,
}: {
  id: string; row: Conversation | undefined; current: Conversation | null; detailError: string;
  messages: {
    items: Message[]; loaded: boolean; error: string; status: number | null; next_cursor: string | number | null;
  };
  onOlder: () => void; pending: Approval[]; now: number; motion: boolean; query: string;
  actions: InboxActions; feedback: ReactNode; onBack: () => void; onDetails: () => void;
  detailsRef: RefObject<HTMLButtonElement | null>; logRef: RefObject<HTMLDivElement | null>;
}) {
  const shown = current ?? row ?? null;
  const contact = contactOf(shown ?? { contact: null });
  const title = rowTitle(contact);
  const muted = current?.human_muted ?? null;
  const base = `/console/v1/inbox/conversations/${id}`;
  const [text, setText] = useState("");
  // The reply is queued only after a second, explicit step; Cancel is where focus starts.
  const [confirming, setConfirming] = useState(false);
  const refocus = useRef(false);
  const boxRef = useRef<HTMLTextAreaElement>(null);
  const queueRef = useRef<HTMLButtonElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const sending = useRef(false);

  // The room changes only when the server's read says so, and animates only on a change.
  const [room, setRoom] = useState<{ muted: boolean | null; changed: boolean }>({ muted: null, changed: false });
  if (muted !== null && muted !== room.muted) setRoom({ muted, changed: room.muted !== null });
  const roomMotion = room.changed && motion ? "fs-slide-up" : "";

  // Oldest first for reading; the API pages newest first.
  const ordered = [...messages.items].reverse();
  const newest = ordered[ordered.length - 1];
  const newestTime = timeOf(newest?.created_at);
  const [baseline, setBaseline] = useState<number | null>(null);
  const [seenAt, setSeenAt] = useState<number | null>(null);
  const [atBottom, setAtBottom] = useState(true);
  if (messages.loaded && baseline === null) { setBaseline(newestTime); setSeenAt(newestTime); }
  const atBottomRef = useRef(true);
  const unseen = atBottom ? 0 : unseenCount(ordered.map((message) => message.created_at), seenAt);

  function readPosition() {
    const box = logRef.current;
    if (!box) return;
    const bottom = nearBottom(box);
    atBottomRef.current = bottom;
    setAtBottom(bottom);
    if (bottom) setSeenAt(newestTime);
  }
  function toBottom(smooth: boolean) {
    const box = logRef.current;
    if (box) box.scrollTo({ top: box.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  }
  // New newest message: follow it only if the reader was already at the bottom.
  const newestKey = newest?.id ?? "";
  useEffect(() => {
    if (!newestKey || !atBottomRef.current) return;
    const box = logRef.current;
    if (box) box.scrollTop = box.scrollHeight;
    const frame = requestAnimationFrame(readPosition);
    return () => cancelAnimationFrame(frame);
    // readPosition reads the latest values through refs and state setters.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [newestKey, logRef]);

  function act(operation: "takeover" | "release" | "reply") {
    if (!current) return;
    return actions.submit({ path: `${base}/${operation}?${query}`,
      onSuccess: operation === "reply" ? () => setText("") : undefined, payload: {
        request_key: crypto.randomUUID(), expected_version: current.inbox_version,
        ...(operation === "reply" ? { body: text } : {}),
      } } satisfies Pending);
  }
  const blocked = actions.blocked;

  const formRef = useRef<HTMLFormElement>(null);
  useEffect(() => {
    if (!confirming) return;
    cancelRef.current?.focus({ preventScroll: true });
    // Show the whole question with the bottom padding under it, clear of the assistant's launcher.
    const form = formRef.current;
    if (form) form.scrollTop = form.scrollHeight;
  }, [confirming]);
  // After a send the box is free again: back to the box when the text went, else to the button.
  useEffect(() => {
    if (!refocus.current || blocked || confirming) return;
    refocus.current = false;
    (text.trim() ? queueRef : boxRef).current?.focus();
  }, [confirming, blocked, text]);

  function cancelConfirm() {
    // While the request is in flight the question stays up, so nothing can hide or move focus mid-send.
    if (sending.current) return;
    setConfirming(false);
    queueRef.current?.focus();
  }
  async function confirmSend() {
    if (sending.current) return;
    sending.current = true;
    try {
      const request = act("reply");
      if (request) await request;
    } finally {
      sending.current = false;
      setConfirming(false);
      refocus.current = true;
    }
  }

  return <section aria-labelledby={`thread-${id}-title`} data-testid="inbox-thread"
    className="flex h-full min-h-0 min-w-0 flex-col overflow-hidden">
    <header className={`flex flex-col gap-2 border-b border-[color:var(--fs-border)] px-3 py-2.5 ${motion
      ? "transition-[background-color,box-shadow] duration-200 ease-(--fs-ease-out)" : ""} ${muted
      ? "bg-[var(--fs-room-staff-bg)] shadow-[inset_0_4px_0_var(--fs-room-staff-bar)]" : "bg-[var(--fs-surface)]"}`} data-room={muted ? "staff" : "assistant"}>
      {/* Phone: Back and View details on one row, the customer on the next. */}
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <button type="button" className={`${secondary} shrink-0 px-2.5 md:hidden`} onClick={onBack}
          aria-label="Back to conversations" disabled={actions.pending !== null}>
          <ArrowLeft aria-hidden size={16} />Back</button>
        <button ref={detailsRef} type="button" data-testid="inbox-details-button" aria-haspopup="dialog"
          className={`${secondary} ml-auto shrink-0 md:order-last min-[68.75rem]:hidden`} onClick={onDetails}>View details</button>
        <div className="flex min-w-0 basis-full items-center gap-2 md:flex-1 md:basis-0">
          <CustomerAvatar id={id} name={contact.display_name} size={40} />
          <div className="min-w-0 flex-1">
            <h2 id={`thread-${id}-title`} className="truncate text-base font-semibold">{title}</h2>
            <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink-700">
              {shown && <ChannelBadge channel={shown.channel} testId="inbox-thread-channel" />}
              <span>{muted === null ? "Checking who is replying" : muted ? "Staff has taken over" : "The assistant is handling this"}</span>
            </p>
          </div>
        </div>
      </div>
      {current && <div className="flex flex-wrap items-center gap-2">
        {current.human_muted && current.ai_muted_until && <>
          <p className="w-full text-xs text-ink-700">The assistant is paused until {dateTime(current.ai_muted_until)}.
            Take over indefinitely to keep it paused until you release it.</p>
          <button type="button" className={secondary} disabled={blocked || !!detailError}
            onClick={() => void act("takeover")}>Take over indefinitely</button>
        </>}
        <button type="button" className={secondary} disabled={blocked || !!detailError}
          onClick={() => {
            if (!current.human_muted || window.confirm("Release this conversation to AI? Older paused turns stay suppressed."))
              void act(current.human_muted ? "release" : "takeover");
          }}>{current.human_muted ? "Release to AI" : "Take over"}</button>
      </div>}
    </header>
    {(detailError || messages.error) && <div className="border-b border-[color:var(--fs-border)] p-3">
      {/* A refusal (403) carries the server's plain words; retrying will not change it. */}
      <Notice error>{messages.status === 403 && messages.error ? messages.error
        : "We could not refresh this conversation. We will keep trying every few seconds."}</Notice></div>}
    <div className="relative flex min-h-0 flex-1 flex-col">
      <div ref={logRef} role="log" aria-label={`Messages with ${title}`} tabIndex={0} onScroll={readPosition}
        className="min-h-0 flex-1 overflow-y-auto bg-linear-to-b from-[var(--fs-log-from)] to-[var(--fs-log-to)] px-3 py-3 focus-visible:outline-3 focus-visible:-outline-offset-3 focus-visible:outline-[color:var(--fs-focus)]">
        {messages.next_cursor !== null && <div className="mb-3 text-center">
          <button type="button" className={secondary} onClick={onOlder}>Older messages</button></div>}
        {!messages.loaded && !messages.error && <div aria-hidden className="space-y-3">
          <Skeleton className="h-10 w-3/5" /><Skeleton className="ml-auto h-12 w-2/3" /><Skeleton className="h-8 w-1/2" />
        </div>}
        {messages.loaded && ordered.length === 0 && <p className="py-6 text-center text-sm text-ink-700">No messages yet.</p>}
        <ol className="flex flex-col">{threadItems(ordered).map((item) => item.kind === "day"
          ? <li key={item.key} className="my-3 flex justify-center">
            <span className="rounded-full border border-[color:var(--fs-border)] bg-[var(--fs-surface)] px-2.5 py-0.5 text-[11px] text-ink-700">{item.label}</span></li>
          : <MessageItem key={item.key} item={item} contactId={id} contactName={contact.display_name} motion={motion}
            arrived={baseline !== null && timeOf(item.message.created_at) > baseline} />)}</ol>
        {pending.length > 0 && <div className="mt-3 flex flex-col items-end gap-3">
          {pending.map((approval) => <div key={approval.id} className="w-full max-w-[92%] md:max-w-[84%]">
            <ReviewCard approval={approval} now={now} blocked={blocked}
              onPropose={(decision) => actions.propose(approval, decision)}
              onEdit={(value) => actions.edit(approval, value)} /></div>)}
        </div>}
      </div>
      <div aria-live="polite" className="pointer-events-none absolute inset-x-0 bottom-3 flex justify-center">
        {unseen > 0 && <button type="button" data-testid="inbox-new-messages-pill" onClick={() => toBottom(motion)}
          className={`pointer-events-auto inline-flex min-h-9 items-center gap-1.5 rounded-full bg-[var(--fs-primary)] px-3.5 text-xs font-semibold text-[color:var(--fs-on-primary)] shadow-[var(--fs-shadow-popover)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] ${motion ? "fs-fade" : ""}`}>
          <ArrowDown aria-hidden size={14} /><span className="text-xs font-semibold">{newMessagesText(unseen)}</span></button>}
      </div>
    </div>
    {feedback}
    {current?.human_muted
      ? <form key="reply" ref={formRef} data-testid="inbox-composer"
        className={`max-h-[60%] shrink-0 scroll-pb-16 overflow-y-auto border-t border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3 pb-16 md:scroll-pb-0 md:pb-3 ${roomMotion}`}
        onSubmit={(event) => { event.preventDefault(); if (text.trim() && !blocked) setConfirming(true); }}
        onKeyDown={(event) => {
          // Escape closes the question first; the inbox's own Escape (back to the list) must not also fire.
          if (event.key === "Escape" && confirming) { event.stopPropagation(); event.preventDefault(); cancelConfirm(); }
        }}>
        <label className="block"><span className="text-sm font-medium">Human reply</span>
          <textarea ref={boxRef} className={`${field} mt-1 h-20 py-2 md:h-24`} value={text} maxLength={2000}
            disabled={blocked} readOnly={confirming} onChange={(event) => setText(event.target.value)} required />
        </label>
        <div className="mt-2 flex justify-start md:justify-end">
          <button ref={queueRef} className={action} aria-expanded={confirming} aria-controls={`reply-confirm-${id}`}
            disabled={blocked || !!detailError || !text.trim()}>Queue reply</button></div>
        {confirming && <div id={`reply-confirm-${id}`} role="group" aria-label="Confirm reply" data-testid="inbox-reply-confirm"
          className="mt-2 rounded-[var(--fs-radius-control)] border border-[color:var(--fs-primary)] bg-[var(--fs-surface-blue)] p-3">
          <p className="text-sm font-medium">Queue this reply for delivery to the customer?</p>
          <p className="mt-1 text-xs text-ink-700">Once it is delivered you cannot take it back.</p>
          <div className="mt-3 flex flex-wrap justify-start gap-2 md:justify-end">
            <button ref={cancelRef} type="button" data-testid="inbox-reply-confirm-cancel" className={secondary}
              disabled={blocked} onClick={cancelConfirm}>Cancel</button>
            <button type="button" data-testid="inbox-reply-confirm-send" className={action}
              disabled={blocked || !!detailError} onClick={() => void confirmSend()}>
              {blocked ? "Queueing reply" : "Confirm and queue"}</button>
          </div>
        </div>}
      </form>
      : <p key="hint" className={`border-t border-[color:var(--fs-border)] bg-[var(--fs-surface)] px-3 py-3 text-sm text-ink-700 ${roomMotion}`}>
        Take over to reply as a team member.</p>}
  </section>;
}
