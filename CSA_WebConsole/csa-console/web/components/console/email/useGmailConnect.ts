"use client";

import { useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import type { emailClient } from "@/lib/email-api";
import { emailErrorSentence } from "@/lib/email-recipients";

/** What Disconnect asks before it acts, on the Email page and on the Connections tab. */
export const GMAIL_DISCONNECT_QUESTION =
  "Disconnect Gmail? Emails already loaded stay readable. Connect Gmail again to sync or send.";

/** The plain sentence for a failed request; codes without one keep the generic form. */
export function errorFor(reason: unknown): string {
  const sentence = emailErrorSentence(reason);
  if (sentence) return sentence;
  const code = reason instanceof ApiError ? reason.code : reason instanceof Error ? reason.message : String(reason);
  return `Request failed: ${code}`;
}

/**
 * Connect Gmail and Disconnect for the signed-in member's own mailbox, shared by the Connections tab
 * and the Email page. Connect ends by leaving for Google. After a disconnect `reload` reads what the
 * caller shows again, and only then is the success sentence set. One request runs at a time.
 */
export function useGmailConnect(client: ReturnType<typeof emailClient>, reload: () => Promise<void>) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const inFlight = useRef(false);

  async function run<T>(work: () => Promise<T>, done: (value: T) => void | Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await done(await work());
    } catch (reason) {
      setError(errorFor(reason));
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  const connect = () => run(() => client.connect(), (result) => { window.location.assign(result.authorization_url); });
  const disconnect = () => run(() => client.disconnect(), async () => {
    await reload();
    setMessage("Gmail disconnected. Emails already loaded stay readable.");
  });

  /** Forget the last sentence, for a page whose other actions say their own. */
  const clear = () => { setError(""); setMessage(""); };

  return { busy, error, message, connect, disconnect, clear };
}
