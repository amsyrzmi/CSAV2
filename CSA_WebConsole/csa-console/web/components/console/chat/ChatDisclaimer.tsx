"use client";

import { useId, useState } from "react";
import { SANDBOX_COMPACT_NOTICE } from "@/lib/sandbox-chat";
import { Notice } from "../ui";

/** What the private chat cannot do, in plain words: shown in full while a chat is empty. */
export function SandboxLimits() {
  return <Notice testId="sandbox-disclaimer">This chat only answers questions about your knowledge base and products.
    It cannot look up customer records, orders, bookings, tickets, invoices or payments.
    It cannot read or send email, message your customers or pass a question to your staff.
    It cannot confirm business actions or take attachments.
    Dictation fills in your message for you to check before sending, and read aloud uses your browser&apos;s voice.
    To add documents, use Knowledge base.
    Archiving a chat hides it but does not delete it.</Notice>;
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
