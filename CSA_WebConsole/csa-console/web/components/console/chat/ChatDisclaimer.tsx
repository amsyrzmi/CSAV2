"use client";

import { useId, useState } from "react";
import { SANDBOX_COMPACT_NOTICE } from "@/lib/sandbox-chat";
import { Notice } from "../ui";

/** The sandbox limits, word for word as the page has always shown them. */
export function SandboxLimits() {
  return <Notice testId="sandbox-disclaimer">No customer records, orders, bookings, tickets, invoices, payments, email or staff escalation.
    No external customer sends. Business confirmations and attachments are unavailable.
    Browser dictation fills a draft for review; read aloud uses a local browser voice.
    Use Knowledge base for document uploads. Archive hides history; it does not delete it.</Notice>;
}

/** Once a conversation has a message: one line, and a disclosure holding the full limits. */
export function CompactLimits() {
  const [open, setOpen] = useState(false);
  const id = useId();
  return <div className="text-center text-xs text-[color:var(--fs-text-secondary)]">
    <p className="inline">{SANDBOX_COMPACT_NOTICE}</p>{" "}
    <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((value) => !value)}
      className="inline-flex min-h-6 items-center rounded-[var(--fs-radius-control)] px-1 font-medium text-[color:var(--fs-primary)] underline underline-offset-2 hover:text-[color:var(--fs-primary-hover)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]">
      What it cannot do</button>
    <div id={id} hidden={!open} className="mt-2 text-left"><SandboxLimits /></div>
  </div>;
}
