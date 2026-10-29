import { api, ApiError } from "./api";

export interface Mailbox {
  id: string;
  address: string | null;
  status: "pending" | "connected" | "revoked";
  scopes: string[];
  last_sync_at: string | null;
}
export interface EmailContext {
  enabled: boolean;
  can_manage: boolean;
  can_connect: boolean;
  provider_configured: boolean;
  model_configured: boolean;
  mailbox: Mailbox | null;
  disclosure: string;
  /** The last completed sync, or null before the first one. */
  last_synced_at: string | null;
  /** False once load more has reached the end of the 90-day window. */
  more_available: boolean;
}
export interface Receipt {
  operation: string;
  resource_id: string;
  status: string;
  duplicate: boolean;
  detail: Record<string, unknown>;
}
export type Category =
  | "customer_request" | "order_or_delivery" | "complaint" | "billing" | "vendor_or_partner"
  | "internal" | "newsletter_or_promotion" | "spam_or_phishing" | "other";
export interface Thread {
  thread_id: string;
  subject: string;
  sender: string;
  snippet: string;
  received_at: string | null;
  labels: string[];
  unread: boolean;
  category: Category | null;
  priority: "low" | "normal" | "high" | null;
  summary: string | null;
  action_items: string[];
  analysed_at: string | null;
  has_body: boolean;
}
export interface ThreadDetail extends Thread { body: string | null }
export interface ThreadPage { items: Thread[]; last_sync_at: string | null }
export interface Draft {
  id: string;
  thread_id: string | null;
  /** The first To address; kept for older callers. */
  recipient: string;
  to: string[];
  cc: string[];
  bcc: string[];
  /** Every address in To, CC then BCC order, flagged when outside the company. */
  recipients: { address: string; outside: boolean }[];
  subject: string;
  body: string;
  status: "draft" | "exported" | "sending" | "sent";
  source: "model" | "human";
  gmail_draft_id: string | null;
  sent_at: string | null;
  needs_staff_input: boolean;
  reply_to_message_id: string | null;
  created_at: string;
  updated_at: string;
  /** The server's `draft_version`: send exactly this; any edit changes it. */
  version: string;
}
export interface DraftPage { items: Draft[] }
export interface DraftEdit { to: string[]; cc: string[]; bcc: string[]; subject: string; body: string }
export interface SendResult {
  outcome: "sent" | "unknown";
  sent_at: string | null;
  gmail_message_id: string | null;
  remaining_this_hour: number;
}
export interface AttentionItem {
  thread_id: string;
  sender: string;
  subject: string;
  priority: "low" | "normal" | "high";
  summary: string;
  next_steps: string[];
}
export interface AttentionPage { items: AttentionItem[]; as_of: string | null; skipped?: number }

/**
 * A send whose answer is lost or unknown keeps its key, so a retake replays the stored outcome
 * and Gmail is never called twice. A clear refusal frees the key for a fresh attempt.
 */
const UNCERTAIN_SEND = new Set(["send_outcome_unknown", "draft_already_sending"]);
const sendSettled = (error: unknown) =>
  error instanceof ApiError && error.body !== undefined && !UNCERTAIN_SEND.has(error.code);

/** Same idempotency discipline as HR: a key stays with one exact operation until it succeeds. */
export function emailClient(tenant: string) {
  const suffix = tenant ? `?client_id=${encodeURIComponent(tenant)}` : "";
  const keys = new Map<string, string>();
  function write<T>(
    path: string, body: unknown, method = "POST", settled: (error: unknown) => boolean = () => false,
  ): Promise<T> {
    const serialized = body === undefined ? "" : JSON.stringify(body);
    const fingerprint = `${method}:${path}:${serialized}`;
    let key = keys.get(fingerprint);
    if (!key) { key = crypto.randomUUID(); keys.set(fingerprint, key); }
    const init: RequestInit & { headers: Record<string, string> } = {
      method, headers: { "Idempotency-Key": key },
    };
    if (serialized) init.body = serialized;
    return api<T>(`/console/v1/email${path}${suffix}`, init).then(result => {
      keys.delete(fingerprint);
      return result;
    }, (error: unknown) => {
      if (settled(error)) keys.delete(fingerprint);
      throw error;
    });
  }
  /** After a release the draft may be sent again: never reuse the old attempt's key. */
  function forgetSends(id: string) {
    for (const fingerprint of keys.keys())
      if (fingerprint.startsWith(`POST:/drafts/${id}/send:`)) keys.delete(fingerprint);
  }
  return {
    context: () => api<EmailContext>(`/console/v1/email/context${suffix}`),
    setEnabled: (enabled: boolean) => write<Receipt>("/settings", { enabled }, "PUT"),
    connect: () => write<{ authorization_url: string }>("/mailbox/connect", undefined),
    disconnect: () => write<Receipt>("/mailbox/disconnect", undefined),
    sync: () => write<Receipt>("/sync", undefined),
    /** The next older page of the 90-day window; `detail.done` is true at the end. */
    syncMore: () => write<Receipt>("/sync/more", undefined),
    attention: () => api<AttentionPage>(`/console/v1/email/attention${suffix}`),
    threads: () => api<ThreadPage>(`/console/v1/email/threads${suffix}`),
    thread: (id: string) => api<ThreadDetail>(`/console/v1/email/threads/${encodeURIComponent(id)}${suffix}`),
    analyse: (id: string) => write<ThreadDetail>(`/threads/${encodeURIComponent(id)}/analyse`, undefined),
    draft: (id: string, instructions: string, language: "auto" | "en" | "ms") =>
      write<Draft>(`/threads/${encodeURIComponent(id)}/draft`, { instructions, language }),
    drafts: () => api<DraftPage>(`/console/v1/email/drafts${suffix}`),
    getDraft: (id: string) => api<Draft>(`/console/v1/email/drafts/${encodeURIComponent(id)}${suffix}`),
    updateDraft: (id: string, body: DraftEdit) =>
      write<Draft>(`/drafts/${encodeURIComponent(id)}`, body, "PUT"),
    deleteDraft: (id: string) => write<Receipt>(`/drafts/${encodeURIComponent(id)}/delete`, undefined),
    /** `version` is the `version` of exactly the draft the person confirmed, as the server gave it. */
    send: (id: string, version: string) =>
      write<SendResult>(`/drafts/${encodeURIComponent(id)}/send`, { version }, "POST", sendSettled),
    /** The owner checked Gmail Sent and the email is not there: make the draft editable again. */
    release: (id: string) => write<Draft>(`/drafts/${encodeURIComponent(id)}/release`, undefined)
      .then(draft => { forgetSends(encodeURIComponent(id)); return draft; }),
  };
}
