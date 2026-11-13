import { api } from "./api";

/**
 * Mirrors of the Credit tab's client response shapes in csa/console/billing.py, field for field.
 * Money is a fixed-point decimal string: 2 places for the bill figures, 4 for the per-agent and
 * per-day charges. Format it with lib/credit-view.ts, never add it up here. Dates are
 * "YYYY-MM-DD" (Kuala Lumpur calendar days); `cycle_end` and `billed_to` are exclusive. There is
 * no per-execution list (owner decision 2026-11-13): only the execution count.
 */
export type UsageStatus = "ok" | "pending_prices" | "not_set_up";

export interface AgentUsageOut {
  agent_id: string;
  title: string;
  executions: number;
  price: string;
}

export interface DailyUsageOut {
  day: string;
  price: string;
}

export interface UsageOut {
  cycle_start: string;
  cycle_end: string;
  billed_from: string | null;
  billed_to: string | null;
  executions: number;
  ai_usage_price: string;
  platform_fee: string;
  total: string;
  final: boolean;
  status: UsageStatus;
  by_agent: AgentUsageOut[];
  daily: DailyUsageOut[];
}

export interface CyclePickOut {
  start: string;
  end: string;
  /** Null when billing was not set up for the cycle. */
  total: string | null;
  final: boolean;
}

const BASE = "/console/v1/billing/usage";

function path(rest: string, params: Record<string, string | null | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value) query.set(key, value);
  return `${BASE}${rest}${query.size ? `?${query}` : ""}`;
}

/** One cycle's usage and bill; the open cycle when `cycle` (its start date) is left out. */
export function fetchUsage(cycle?: string | null, signal?: AbortSignal): Promise<UsageOut> {
  return api(path("", { cycle }), { signal });
}

/** The cycles the picker offers, each with its bill total. */
export function fetchCycles(signal?: AbortSignal): Promise<CyclePickOut[]> {
  return api(path("/cycles", {}), { signal });
}
