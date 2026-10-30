import type { Me } from "./identity-contracts";

export type SandboxThread = {
  id: string;
  sequence: number;
  archived_at: string | null;
  created_at: string;
};
export type SandboxTurn = {
  id: number;
  trace_id: string;
  request_key: string;
  body: string;
  created_at: string;
  status: "queued" | "processing" | "held" | "awaiting_delivery" | "available" | "failed" | "no_reply";
  replies: { id: string; body: string }[];
};
export type Page<T> = { items: T[]; next_cursor: number | null };
export type PendingSandboxTurn = {
  source: "text" | "voice";
  request_key: string;
  body: string;
};

export const sandboxStatus: Record<SandboxTurn["status"], string> = {
  queued: "Queued - not delivered",
  processing: "Processing",
  awaiting_delivery: "Reply queued - not available yet",
  held: "Held by tenant policy - not delivered; release is unavailable in this sandbox",
  available: "Available privately in this sandbox",
  failed: "Failed - do not assume a reply or business action completed",
  no_reply: "Completed without a reply",
};

export function sandboxDelayed(turn: SandboxTurn, now: number): boolean {
  return ["queued", "processing", "awaiting_delivery"].includes(turn.status)
    && now - Date.parse(turn.created_at) > 90_000;
}

/**
 * Prompt chips on the empty chat. Each one only fills the composer (nothing is sent by a click)
 * and stays inside what the sandbox answers: knowledge and product-information questions.
 */
export const CHIP_KNOWLEDGE = "What does our knowledge base say about ";
export const CHIP_PRODUCTS = "Which of our products ";
export const CHIP_CUSTOMER_ASKS = "How should we answer a customer who asks about ";
export const SANDBOX_CHIPS: readonly string[] = [CHIP_KNOWLEDGE, CHIP_PRODUCTS, CHIP_CUSTOMER_ASKS];

/** The label on a chip: the starter without its trailing space, then an ellipsis to show the user finishes it. */
export function chipLabel(chip: string): string {
  return `${chip.trimEnd()}…`;
}

/** The one line the full limits notice collapses to once a conversation has a message. */
export const SANDBOX_COMPACT_NOTICE = "Private chat. It only answers questions about your knowledge base and products.";

/** The same three chips for every role (a read-only member sees them disabled). A fresh copy. */
// eslint-disable-next-line @typescript-eslint/no-unused-vars -- the role is the seam for per-role chips
export function chipsFor(role: Me["role"]): string[] {
  return [...SANDBOX_CHIPS];
}

/** The full limits notice while the conversation is empty, one line once it has a message. */
export function disclaimerMode(turnCount: number): "full" | "compact" {
  return turnCount > 0 ? "compact" : "full";
}

/** What the message log shows, as far as scrolling cares: which conversation, its range. */
export type ScrollSnapshot = { thread: string; firstId: number | null; lastKey: string | null };

/**
 * How the log scrolls after its turns change. "bottom": the first load of a conversation, the
 * reader's own new question, or anything new while the reader is already at the end. "keep-offset":
 * older turns were added above ("Older messages"), so keep what the reader sees. "stay": anything
 * else, so a poll never pulls a reader away from earlier messages.
 */
export function scrollPlan(before: ScrollSnapshot, after: ScrollSnapshot, nearBottom: boolean,
  ownKeys: ReadonlySet<string>): "bottom" | "keep-offset" | "stay" {
  if (after.firstId === null) return "stay";
  if (before.thread !== after.thread || before.firstId === null) return "bottom";
  if (after.firstId < before.firstId) return "keep-offset";
  if (after.lastKey !== before.lastKey && after.lastKey !== null && ownKeys.has(after.lastKey)) return "bottom";
  return nearBottom ? "bottom" : "stay";
}
