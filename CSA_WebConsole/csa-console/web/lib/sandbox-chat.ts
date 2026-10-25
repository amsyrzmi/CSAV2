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
