import { api, post } from "./api";

/**
 * Mirrors of the platform-admin billing models in csa/console/billing_admin.py, field for field.
 * Platform admins only: this is the one place raw cost, tokens and margin are read. No client
 * Credit file may import this module (tests/billing-admin-view.test.mjs checks).
 *
 * Money is a fixed-point decimal string (2 places for the bill and the margin, 4 for raw cost and
 * cost per execution). USD prices and the FX rate are sent as the exact strings typed, never as
 * floats. Dates are "YYYY-MM-DD" (Kuala Lumpur days); `cycle_end` is exclusive. A refusal throws
 * the ApiError from lib/api.ts, whose `code` is the server's `detail`.
 */
export type UsageStatus = "ok" | "pending_prices" | "not_set_up";
export type Stage = "pilot" | "collaborator" | "terminated";
export type Bundle = "individual" | "curated" | "complete";
export type AdminWarning = "pilot_ended" | "usage_after_termination" | "not_set_up_with_usage" | "pending_prices";

/** The providers the ledger records (LEDGER_PROVIDERS in csa/metering/meter.py). */
export const LEDGER_PROVIDERS = ["groq", "gemini"] as const;
export type Provider = (typeof LEDGER_PROVIDERS)[number];

export interface AdminCompanyOut {
  client_id: string;
  name: string;
  status: UsageStatus;
  stage: Stage | null;
  bundle: Bundle | null;
  executions: number;
  prompt_tokens: number;
  completion_tokens: number;
  raw_cost_rm: string;
  ai_charge: string;
  platform_fee: string;
  billed_total: string;
  ai_margin_rm: string;
  /** Null when no execution in the cycle is priced. */
  cost_per_execution_rm: string | null;
  workbook_cost_per_message_rm: string;
  unpriced_calls: number;
  unpriced_executions: number;
  usage_without_plan: number;
  usage_after_termination: number;
  terminated_on: string | null;
  warnings: AdminWarning[];
}

export interface AdminOverviewOut {
  cycle_start: string;
  cycle_end: string;
  final: boolean;
  companies: AdminCompanyOut[];
}

export interface BillingPlanIn {
  stage: Stage;
  /** Left out for a termination: the server keeps the bundle in force. */
  bundle?: Bundle | null;
  effective_from: string;
  reason: string;
}

export interface BillingPlanOut {
  client_id: string;
  stage: Stage;
  bundle: Bundle;
  effective_from: string;
  created_at: string;
}

export interface BillingPriceIn {
  provider: string;
  model: string;
  input_usd_per_mtok: string;
  output_usd_per_mtok: string;
  /** ISO date-time with its offset. */
  effective_from: string;
}

export interface BillingPriceOut {
  provider: string;
  model: string;
  input_usd_per_mtok: string;
  output_usd_per_mtok: string;
  effective_from: string;
  created_at: string;
}

export interface BillingFxIn {
  usd_to_myr: string;
  effective_from: string;
}

export interface BillingFxOut {
  usd_to_myr: string;
  effective_from: string;
  created_at: string;
}

const BASE = "/console/v1/billing/admin";

/** Every company's cycle; the current cycle when `cycle` (its start date, a 5th) is left out. */
export function fetchOverview(cycle?: string | null, signal?: AbortSignal): Promise<AdminOverviewOut> {
  const query = cycle ? `?${new URLSearchParams({ cycle })}` : "";
  return api(`${BASE}/overview${query}`, { signal });
}

/** A new plan row for one company (CSRF-protected, audited). */
export function postPlan(clientId: string, body: BillingPlanIn): Promise<BillingPlanOut> {
  return post<BillingPlanOut>(`${BASE}/clients/${encodeURIComponent(clientId)}/plan`, body);
}

/** A new model price row (CSRF-protected, audited). */
export function postPrice(body: BillingPriceIn): Promise<BillingPriceOut> {
  return post<BillingPriceOut>(`${BASE}/prices`, body);
}

/** A new USD to MYR rate row (CSRF-protected, audited). */
export function postFx(body: BillingFxIn): Promise<BillingFxOut> {
  return post<BillingFxOut>(`${BASE}/fx`, body);
}
