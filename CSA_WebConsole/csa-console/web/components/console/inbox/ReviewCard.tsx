"use client";

import { useState } from "react";
import StatusLabel from "@/components/console/fs/StatusLabel";
import { action, field, secondary } from "@/components/console/ui";
import type { Approval } from "@/lib/inbox-api";
import { approvalLabel, dateTime, isExpired } from "@/lib/inbox-thread";

/**
 * One held reply. Approve and Reject open the shared proposal (confirmed on its own card);
 * Edit queues a replacement after a browser confirmation. A reply that is still pending on
 * the server but past its expiry, and any decided reply, is read-only (UI guide 6).
 */
export function ReviewCard({ approval, now, blocked, onPropose, onEdit, onView }: {
  approval: Approval; now: number; blocked: boolean;
  onPropose: (decision: "approve" | "reject") => Promise<void>;
  onEdit: (text: string) => Promise<void>;
  /** Only for a card shown outside its thread. */
  onView?: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState("");
  const expired = isExpired(approval, now);
  const open = approval.status === "pending" && !expired;
  const label = approvalLabel(approval.status, expired);
  return <article aria-label={`Held reply, ${label.text.toLowerCase()}`}
    className={`min-w-0 rounded-[14px] border border-l-4 bg-white p-3 text-sm ${open
      ? "border-[#F0D98C] border-l-[#E0A82E]" : "border-[#D8E1F0] border-l-[#7B8CA5]"}`}>
    <div className="flex flex-wrap items-center gap-2">
      <StatusLabel tone={label.tone}>{label.text}</StatusLabel>
      {approval.status === "pending" && <span className="text-xs text-ink-600">
        {expired ? "Expired" : "Expires"} {dateTime(approval.expires_at)}</span>}
    </div>
    <p className="my-2 whitespace-pre-wrap [overflow-wrap:anywhere]">{approval.draft}</p>
    {approval.reason && <p className="text-xs text-ink-600 [overflow-wrap:anywhere]">Why it is held: {approval.reason}</p>}
    {expired && <p className="mt-1 text-xs text-ink-600">Nobody decided in time, so this reply will not be sent.</p>}
    {(onView || open) && <div className="mt-3 flex flex-wrap gap-2">
      {onView && <button type="button" className={secondary} disabled={blocked} onClick={onView}>View conversation</button>}
      {open && <>
        <button type="button" className={action} disabled={blocked} onClick={() => void onPropose("approve")}>Approve</button>
        <button type="button" className={secondary} disabled={blocked}
          onClick={() => { setEditing(!editing); setText(approval.draft); }}>Edit</button>
        <button type="button" className={secondary} disabled={blocked} onClick={() => void onPropose("reject")}>Reject</button>
      </>}
    </div>}
    {editing && open && <form className="mt-3 space-y-2" onSubmit={(event) => {
      event.preventDefault();
      if (window.confirm("Queue this edited reply for delivery?")) void onEdit(text);
    }}>
      <label className="block text-sm font-medium">Replacement reply
        <textarea className={`${field} mt-1 h-28 py-2`} value={text} maxLength={2000} disabled={blocked}
          onChange={(event) => setText(event.target.value)} required />
      </label>
      <button className={action} disabled={blocked || !text.trim()}>Queue edited reply</button>
    </form>}
  </article>;
}
