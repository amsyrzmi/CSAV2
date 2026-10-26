import { api } from "./api";

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
  recipient: string;
  subject: string;
  body: string;
  status: "draft" | "exported";
  source: "model" | "human";
  gmail_draft_id: string | null;
  created_at: string;
  updated_at: string;
}
export interface DraftPage { items: Draft[] }

/** Same idempotency discipline as HR: a key stays with one exact operation until it succeeds. */
export function emailClient(tenant: string) {
  const suffix = tenant ? `?client_id=${encodeURIComponent(tenant)}` : "";
  const keys = new Map<string, string>();
  function write<T>(path: string, body: unknown, method = "POST"): Promise<T> {
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
    });
  }
  return {
    context: () => api<EmailContext>(`/console/v1/email/context${suffix}`),
    setEnabled: (enabled: boolean) => write<Receipt>("/settings", { enabled }, "PUT"),
    connect: () => write<{ authorization_url: string }>("/mailbox/connect", undefined),
    disconnect: () => write<Receipt>("/mailbox/disconnect", undefined),
    sync: () => write<Receipt>("/sync", undefined),
    threads: () => api<ThreadPage>(`/console/v1/email/threads${suffix}`),
    thread: (id: string) => api<ThreadDetail>(`/console/v1/email/threads/${encodeURIComponent(id)}${suffix}`),
    analyse: (id: string) => write<ThreadDetail>(`/threads/${encodeURIComponent(id)}/analyse`, undefined),
    draft: (id: string, instructions: string, language: "auto" | "en" | "ms") =>
      write<Draft>(`/threads/${encodeURIComponent(id)}/draft`, { instructions, language }),
    drafts: () => api<DraftPage>(`/console/v1/email/drafts${suffix}`),
    updateDraft: (id: string, body: { recipient: string; subject: string; body: string }) =>
      write<Draft>(`/drafts/${id}`, body, "PUT"),
    deleteDraft: (id: string) => write<Receipt>(`/drafts/${id}/delete`, undefined),
  };
}
