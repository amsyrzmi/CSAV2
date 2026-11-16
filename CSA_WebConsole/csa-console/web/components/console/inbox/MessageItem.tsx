"use client";

import { useState } from "react";
import { Users } from "lucide-react";
import AgentAvatar from "@/components/console/fs/AgentAvatar";
import CustomerAvatar from "@/components/console/fs/CustomerAvatar";
import { deliveryLabel, deliveryNotice } from "@/lib/channels-api";
import { messagePictureId, type Message } from "@/lib/inbox-api";
import { authorLabel, clockTime, type ThreadItem } from "@/lib/inbox-thread";
import { pictureUrl } from "@/lib/pictures";

const BUBBLE = {
  customer: "border border-[color:var(--fs-bubble-customer-border)] bg-[var(--fs-bubble-customer-bg)] text-ink-900",
  assistant: "border border-[color:var(--fs-brand-200)] border-l-[3px] border-l-[var(--fs-primary)] bg-[var(--fs-surface)] text-ink-900",
  staff: "border border-[color:var(--fs-bubble-staff-bg)] bg-[var(--fs-bubble-staff-bg)] text-[color:var(--fs-bubble-staff-text)]",
} as const;

/**
 * Staff replies carry only `author = 'human_support'`, not who wrote them, so they show a
 * dark "Team" avatar instead of a person's initials (spec 9).
 */
function TeamAvatar() {
  return <span aria-hidden
    className="inline-flex size-8 shrink-0 items-center justify-center rounded-full bg-[var(--fs-team-bg)] text-[color:var(--fs-team-text)]">
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
 * The picture of an image reply: a fixed-size thumbnail, loaded when it nears the screen. A
 * picture this viewer may not read (404) or that is gone hides itself and leaves the caption.
 */
function Thumbnail({ tenantId, assetId, caption }: { tenantId: string | null; assetId: string; caption: string }) {
  const [broken, setBroken] = useState(false);
  if (broken) return null;
  // eslint-disable-next-line @next/next/no-img-element -- an authenticated API route, not an optimisable asset
  return <img src={pictureUrl(tenantId, assetId)} alt={caption || "Picture"} width={160} height={160} loading="lazy"
    data-testid="inbox-message-picture" onError={() => setBroken(true)}
    className="mb-1 block size-40 rounded-[14px] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] object-cover" />;
}

/**
 * One bubble. Customers on the left; the assistant (always `support`, spec 9) and staff on
 * the right. Within a group only the last bubble shows the avatar; the who-line shows on
 * the first, and on every reply that has a delivery state.
 */
export function MessageItem({ item, contactId, contactName, tenantId, motion, arrived }: {
  item: Extract<ThreadItem<Message>, { kind: "message" }>; contactId: string; contactName: string | null;
  /** The company a platform admin is looking at (null for a company's own staff): names the picture's owner. */
  tenantId: string | null;
  motion: boolean;
  /** Arrived after the thread first loaded: eases in. */
  arrived: boolean;
}) {
  const { message, sender, first, last } = item;
  const label = deliveryLabel(message);
  const notice = deliveryNotice(message);
  const pictureId = messagePictureId(message);
  const outbound = sender !== "customer";
  const avatar = !last ? <span aria-hidden className="w-8 shrink-0" />
    : sender === "customer" ? <CustomerAvatar id={contactId} name={contactName} size={32} />
    : sender === "assistant" ? <AgentAvatar agentId="support" size={32} state="rest" />
    : <TeamAvatar />;
  const corner = outbound ? (last ? "rounded-br-[4px]" : "") : (last ? "rounded-bl-[4px]" : "");
  return <li className={`flex min-w-0 items-end gap-2 ${first ? "mt-3" : "mt-1"} ${outbound ? "flex-row-reverse" : ""} ${arrived && motion ? "fs-message-in" : ""}`}>
    {avatar}
    <div className={`flex min-w-0 max-w-[82%] flex-col ${outbound ? "items-end" : "items-start"}`}>
      {(first || label) && <div className={`mx-1 mb-0.5 flex items-baseline gap-2 text-[11px] text-ink-700 ${outbound ? "flex-row-reverse" : ""}`}>
        <p>{authorLabel(sender)}<DeliveryText label={label} motion={motion} /></p>
        <time dateTime={message.created_at}>{clockTime(message.created_at)}</time>
      </div>}
      {pictureId && <Thumbnail tenantId={tenantId} assetId={pictureId} caption={message.body} />}
      {(message.body || !pictureId) && <p className={`max-w-full rounded-[14px] px-3 py-2 text-sm leading-relaxed whitespace-pre-wrap [overflow-wrap:anywhere] ${BUBBLE[sender]} ${corner}`}>
        {message.body}</p>}
      {notice && <p className="mx-1 mt-1 max-w-full text-xs text-[var(--fs-danger)] [overflow-wrap:anywhere]"
        data-testid="inbox-delivery-notice">{notice}</p>}
    </div>
  </li>;
}
