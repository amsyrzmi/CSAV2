export type PollStatus = "transcribing" | "processing" | "held" | "done" | "failed";

export type PollResult = {
  status: PollStatus;
  transcript: string | null;
  reply: string | null;
  error_code?: string | null;
  error?: { code?: string; message?: string } | null;
};

export type Message = {
  role: "user" | "assistant" | "system";
  text: string;
  files?: Array<{ name: string; size?: number }>;
  status?: PollStatus | "error" | "sending";
  traceId?: string;
  idempotencyKey?: string;
};

export type ChatHistoryConversation = {
  id: string;
  summary: string;
  opened_at: string;
};

export type ChatHistoryMessage = {
  id: string;
  direction: "inbound" | "outbound";
  author: string;
  text: string;
  files?: Array<{ name: string; size?: number }> | null;
  idempotency_key: string | null;
  trace_id: string | null;
  created_at: string;
};

