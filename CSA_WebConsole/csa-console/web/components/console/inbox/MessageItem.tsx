"use client";

import { useState } from "react";
import { Users } from "lucide-react";
import AgentAvatar from "@/components/console/fs/AgentAvatar";
import CustomerAvatar from "@/components/console/fs/CustomerAvatar";
import { deliveryLabel, deliveryNotice } from "@/lib/channels-api";
import type { Message } from "@/lib/inbox-api";
import { authorLabel, clockTime, type ThreadItem } from "@/lib/inbox-thread";

const BUBBLE = {
  customer: "border border-[#D8E1F0] bg-white text-ink-900",
  assistant: "border border-[#B9CDFA] border-l-[3px] border-l-[var(--fs-primary)] bg-white text-ink-900",
  staff: "border border-[#183DA5] bg-[#183DA5] text-white",
} as const;

/**
 * Staff replies carry only `author = 'human_support'`, not who wrote them, so they show a
 * dark "Team" avatar instead of a person's initials (spec 9).
 */
function TeamAvatar() {
  return <span aria-hidden
    className="inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-[#15203A] text-white">
    <Users size={16} /></span>;
}

/** The delivery word after the author; fades when the real delivery state changes. */
function DeliveryText({ label, motion }: { label: string | null; motion: boolean }) {
  const [shown, setShown] = useState(label);
  const [changed, setChanged] = useState(false);
  if (label !== shown) { setShown(label); setChanged(true); }
  if (!label) return null;
  return <span key={label} className={changed && motion ? "fs-fade" : undefined}>{` · ${label}`}</span>;
}

/**
 * One bubble. Customers on the left; the assistant (always `support`, spec 9) and staff on
 * the right. Within a group only the last bubble shows the avatar; the who-line shows on
 * the first, and on every reply that has a delivery state.
 */
export function MessageItem({ item, contactId, contactName, motion, arrived }: {
  item: Extract<ThreadItem<Message>, { kind: "message" }>; contactId: string; contactName: string | null;
  motion: boolean;
  /** Arrived after the thread first loaded: eases in. */
  arrived: boolean;
}) {
  const { message, sender, first, last } = item;
  const label = deliveryLabel(message);
  const notice = deliveryNotice(message);
  const outbound = sender !== "customer";
  const avatar = !last ? <span aria-hidden className="w-8 shrink-0" />
    : sender === "customer" ? <CustomerAvatar id={contactId} name={contactName} size={32} />
    : sender === "assistant" ? <AgentAvatar agentId="support" size={32} />
    : <TeamAvatar />;
  const corner = outbound ? (last ? "rounded-br-[4px]" : "") : (last ? "rounded-bl-[4px]" : "");
  return <li className={`flex min-w-0 items-end gap-2 ${first ? "mt-3" : "mt-1"} ${outbound ? "flex-row-reverse" : ""} ${arrived && motion ? "fs-message-in" : ""}`}>
    {avatar}
    <div className={`flex min-w-0 max-w-[82%] flex-col ${outbound ? "items-end" : "items-start"}`}>
      {(first || label) && <div className={`mx-1 mb-0.5 flex items-baseline gap-2 text-[11px] text-ink-700 ${outbound ? "flex-row-reverse" : ""}`}>
        <p>{authorLabel(sender)}<DeliveryText label={label} motion={motion} /></p>
        <time dateTime={message.created_at}>{clockTime(message.created_at)}</time>
      </div>}
      <p className={`max-w-full rounded-[14px] px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere] ${BUBBLE[sender]} ${corner}`}>
        {message.body}</p>
      {notice && <p className="mx-1 mt-1 max-w-full text-xs text-[var(--fs-danger)] [overflow-wrap:anywhere]"
        data-testid="inbox-delivery-notice">{notice}</p>}
    </div>
  </li>;
}
