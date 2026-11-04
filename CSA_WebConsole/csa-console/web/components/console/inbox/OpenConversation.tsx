"use client";

import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import { approvalsPath, type Approval, type Conversation, type Message } from "@/lib/inbox-api";
import { usePublishContext } from "@/lib/page-context";
import { CustomerCard } from "./CustomerCard";
import { Thread } from "./Thread";
import type { InboxActions } from "./useInboxActions";
import { usePages } from "./usePages";

/** Polls the single-conversation read every 3 s: the version for writes and the full phone. */
function useConversationDetail(base: string, query: string, revision: number) {
  const [current, setCurrent] = useState<Conversation | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const value = await api<Conversation>(`${base}?${query}`, {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]),
        });
        if (!controller.signal.aborted) { setCurrent(value); setError(""); }
      } catch (cause) { if (!controller.signal.aborted) setError((cause as Error).message); }
      finally { if (!controller.signal.aborted) timer = setTimeout(refresh, 3000); }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [base, query, revision]);
  return { current, error };
}

/**
 * Everything about the open conversation. Mounted once per conversation (keyed by id), it
 * renders the thread pane and the customer card: in its own pane when the screen is wide,
 * otherwise portalled into the always-mounted slide-over owned by the inbox.
 */
export function OpenConversation({
  id, row, query, revision, actions, now, motion, wide, cardHost, threadClass, cardClass, feedback,
  onBack, onDetails, onCloseDetails, detailsRef,
}: {
  id: string; row: Conversation | undefined; query: string; revision: number; actions: InboxActions;
  now: number; motion: boolean; wide: boolean; cardHost: HTMLElement | null;
  threadClass: string; cardClass: string; feedback: ReactNode;
  onBack: () => void; onDetails: () => void; onCloseDetails: () => void;
  detailsRef: RefObject<HTMLButtonElement | null>;
}) {
  const base = `/console/v1/inbox/conversations/${id}`;
  const { current, error } = useConversationDetail(base, query, revision);
  const [pages, setPages] = useState(1);
  const [includeHandled, setIncludeHandled] = useState(false);
  const logRef = useRef<HTMLDivElement>(null);
  usePublishContext({ tab: "inbox", record: { kind: "conversation", id, version: current?.inbox_version ?? null } });
  const messages = usePages<Message>(`${base}/messages?${query}`, pages, revision);
  // Pending only: the thread decides them. Handled history lives in the card.
  const held = usePages<Approval>(
    approvalsPath({ tenantQuery: query, conversationId: id, includeHandled: false }), 1, revision);
  const pending = held.items.filter((approval) => approval.status === "pending");

  function viewConversation() {
    if (wide) { logRef.current?.focus(); return; }
    // Closing returns focus to "View details" in the thread header.
    onCloseDetails();
  }

  const card = <CustomerCard id={id} row={row} current={current} query={query} revision={revision} now={now}
    actions={actions} inDialog={!wide} onView={viewConversation}
    includeHandled={includeHandled} onIncludeHandled={setIncludeHandled} />;
  return <>
    <div className={threadClass}>
      <Thread id={id} row={row} current={current} detailError={error} messages={messages}
        onOlder={() => setPages((value) => value + 1)} pending={pending} now={now} motion={motion} query={query}
        actions={actions} feedback={feedback} onBack={onBack} onDetails={onDetails} detailsRef={detailsRef}
        logRef={logRef} />
    </div>
    {wide ? <aside aria-label="Customer details" className={cardClass}>{card}</aside>
      : cardHost ? createPortal(card, cardHost) : null}
  </>;
}
