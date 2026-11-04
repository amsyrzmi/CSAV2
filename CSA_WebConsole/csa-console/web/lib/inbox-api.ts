/** Inbox wire types and pure helpers. No imports, so node tests can load this file directly. */

export type Contact = {
  display_name: string | null;
  telegram_username: string | null;
  phone_masked: string | null;
  /** Set only by the single-conversation read; list rows carry the mask. */
  phone: string | null;
  kind: string;
  known: boolean;
};

export type LastMessage = { preview: string; at: string };

export type Conversation = {
  id: string;
  channel: "telegram" | "whatsapp";
  status: string;
  owned_by: string | null;
  ai_muted_until: string | null;
  human_muted: boolean;
  inbox_version: number;
  opened_at: string;
  contact: Contact | null;
  last_message: LastMessage | null;
  pending_approvals: number;
};

/** The four delivery fields, restated structurally (same shape as DeliveryState in channels-api). */
export type Message = {
  id: string;
  direction: string;
  author: string;
  body: string;
  created_at: string;
  delivery_status: string | null;
  delivery_error: string | null;
  provider_status: "sent" | "delivered" | "read" | "failed" | null;
  provider_error: string | null;
};

export type Approval = {
  id: number;
  conversation_id: string;
  draft: string;
  reason: string | null;
  status: string;
  expires_at: string;
};

export type Page<T> = { items: T[]; next_cursor: string | number | null };
export type ConversationPage = Page<Conversation> & { needs_review_count: number };

export type ChannelFilter = "" | "whatsapp" | "telegram";
export type SenderFilter = "" | "known" | "new";

const INBOX = "/console/v1/inbox";

/** Full path and query for the conversation list. Only set filters are sent. */
export function inboxListQuery(o: {
  tenantQuery: string;
  channel: ChannelFilter;
  needsReview: boolean;
  sender: SenderFilter;
  q: string;
}): string {
  const parts: string[] = [];
  if (o.tenantQuery) parts.push(o.tenantQuery);
  if (o.channel) parts.push(`channel=${encodeURIComponent(o.channel)}`);
  if (o.needsReview) parts.push("needs_review=true");
  if (o.sender) parts.push(`sender=${encodeURIComponent(o.sender)}`);
  // The server answers 422 for a one-character search, so it is never sent.
  const q = o.q.trim();
  if ([...q].length >= 2) parts.push(`q=${encodeURIComponent(q)}`);
  return parts.length ? `${INBOX}/conversations?${parts.join("&")}` : `${INBOX}/conversations`;
}

/** Approvals for one conversation, used by the customer card. */
export function approvalsPath(o: {
  tenantQuery: string;
  conversationId: string;
  includeHandled: boolean;
}): string {
  const parts: string[] = [];
  if (o.tenantQuery) parts.push(o.tenantQuery);
  parts.push(`conversation_id=${encodeURIComponent(o.conversationId)}`);
  parts.push(`include_handled=${o.includeHandled}`);
  return `${INBOX}/approvals?${parts.join("&")}`;
}

/** Same rule as ServicesWorkspace's canManage. */
export function canConnectWhatsApp(role: string | null): boolean {
  return role === "platform_admin" || role === "tenant_admin";
}

/** First occurrence wins, order preserved. Pages can repeat a row when activity reorders the list. */
export function mergeById<T extends { id: string | number }>(items: T[]): T[] {
  const seen = new Set<string | number>();
  const merged: T[] = [];
  for (const item of items) {
    if (seen.has(item.id)) continue;
    seen.add(item.id);
    merged.push(item);
  }
  return merged;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Relative time for the conversation list. `now` is epoch milliseconds. */
export function listTime(at: string, now: number): string {
  const time = new Date(at);
  if (Number.isNaN(time.getTime())) return "";
  const elapsed = Math.max(0, now - time.getTime());
  const minutes = Math.floor(elapsed / 60_000);
  if (minutes < 1) return "Just now";
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h`;
  const today = new Date(now);
  const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
  if (
    time.getFullYear() === yesterday.getFullYear() &&
    time.getMonth() === yesterday.getMonth() &&
    time.getDate() === yesterday.getDate()
  ) return "Yesterday";
  const label = `${time.getDate()} ${MONTHS[time.getMonth()]}`;
  return time.getFullYear() === today.getFullYear() ? label : `${label} ${time.getFullYear()}`;
}
