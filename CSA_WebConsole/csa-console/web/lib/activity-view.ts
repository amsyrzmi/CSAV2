/**
 * Pure rules for the Assistant activity screen: which assistants ran last, how a run ended in
 * plain words, the paging stack, the assistant choices and the runs query string.
 */
import { AGENT_TITLES, avatarForAgent, type AgentId } from "@/lib/agent-avatars";
import type { AgentRun } from "@/lib/identity-contracts";

/**
 * `ok` is worked and `error` is needs attention. Anything else (the contract allows `partial`,
 * nothing produces it today) is `partial`: a neutral "Partly finished" badge that no status
 * chip selects, because the server filter accepts only `ok` and `error`.
 */
export type Outcome = "worked" | "needs_attention" | "partial";

export function outcomeOf(run: AgentRun): Outcome {
  if (run.status === "ok") return "worked";
  if (run.status === "error") return "needs_attention";
  return "partial";
}

/** The assistant's plain title; a capability with no avatar keeps its own name. */
export function titleOf(agent: string): string {
  const avatar = avatarForAgent(agent);
  return avatar ? AGENT_TITLES[avatar] : agent;
}

export type LastAssistant = {
  avatarId: AgentId;
  title: string;
  finishedAt: string;
  outcome: Outcome;
  runId: number;
};

function newer(a: AgentRun, b: AgentRun): number {
  const byTime = Date.parse(b.created_at) - Date.parse(a.created_at);
  return byTime !== 0 && !Number.isNaN(byTime) ? byTime : b.id - a.id;
}

/**
 * The assistants that ran most recently, newest first, one card per avatar (two capabilities
 * can share one), at most `limit`. A capability with no avatar is skipped. The list is never
 * padded: fewer assistants have run, fewer cards.
 */
export function lastAssistants(runs: AgentRun[], limit = 5): LastAssistant[] {
  const seen = new Set<AgentId>();
  const cards: LastAssistant[] = [];
  for (const run of [...runs].sort(newer)) {
    if (cards.length >= limit) break;
    const avatarId = avatarForAgent(run.agent);
    if (!avatarId || seen.has(avatarId)) continue;
    seen.add(avatarId);
    cards.push({
      avatarId,
      title: AGENT_TITLES[avatarId],
      finishedAt: run.created_at,
      outcome: outcomeOf(run),
      runId: run.id,
    });
  }
  return cards;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const two = (value: number) => String(value).padStart(2, "0");

/**
 * "just now", "3 minutes ago" under an hour, then "on 2 Nov 2026, 09:05" in the reader's own
 * time zone. A time slightly ahead of `now` counts as just now.
 */
export function whenText(iso: string, now: Date): string {
  const at = new Date(iso);
  const seconds = Math.floor((now.getTime() - at.getTime()) / 1000);
  if (Number.isNaN(seconds) || seconds < 60) return "just now";
  if (seconds < 3600) {
    const minutes = Math.floor(seconds / 60);
    return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  }
  return `on ${at.getDate()} ${MONTHS[at.getMonth()]} ${at.getFullYear()}, ${two(at.getHours())}:${two(at.getMinutes())}`;
}

/** "Customer support assistant finished 3 minutes ago". */
export function plainSummary(run: AgentRun, now: Date): string {
  return `${titleOf(run.agent)} finished ${whenText(run.created_at, now)}`;
}

/** Newer and Older keep the cursors of the pages already seen; the last one is the current page. */
export function pushCursor(stack: number[], cursor: number): number[] {
  return [...stack, cursor];
}

export function popCursor(stack: number[]): number[] {
  return stack.slice(0, -1);
}

/** The ten capabilities that record runs, with a short name where two share a title. */
const CAPABILITIES: { value: string; short: string }[] = [
  { value: "kb_agent", short: "" },
  { value: "ticket_agent", short: "tickets" },
  { value: "escalation_agent", short: "escalation" },
  { value: "order_agent", short: "orders" },
  { value: "order_approval", short: "approvals" },
  { value: "invoice_agent", short: "" },
  { value: "ecommerce_agent", short: "" },
  { value: "subscription_agent", short: "plans" },
  { value: "payment_agent", short: "payments" },
  { value: "booking_agent", short: "" },
];

export function agentOptions(): { value: string; label: string }[] {
  const counts = new Map<string, number>();
  for (const { value } of CAPABILITIES) {
    const title = titleOf(value);
    counts.set(title, (counts.get(title) ?? 0) + 1);
  }
  return CAPABILITIES.map(({ value, short }) => {
    const title = titleOf(value);
    return { value, label: (counts.get(title) ?? 0) > 1 && short ? `${title}: ${short}` : title };
  });
}

export type StatusFilter = "all" | "ok" | "error";

/**
 * The `/runs` query string: the tenant's own parameters first, then only what is chosen. `all`,
 * an empty assistant and null times are left out (an empty `status=` is a 422). `from` and `to`
 * are ISO instants from `toInstants`.
 */
export function runsQuery(
  base: string,
  options: {
    limit: number;
    beforeId?: number | null;
    agent?: string;
    status?: StatusFilter;
    from?: string | null;
    to?: string | null;
  },
): string {
  const params = new URLSearchParams(base);
  params.set("limit", String(options.limit));
  if (options.beforeId != null) params.set("before_id", String(options.beforeId));
  if (options.agent) params.set("agent", options.agent);
  if (options.status && options.status !== "all") params.set("status", options.status);
  if (options.from) params.set("from", options.from);
  if (options.to) params.set("to", options.to);
  return params.toString();
}
