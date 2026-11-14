import type {
  AdminCompanyOut, AdminOverviewOut, AdminWarning, BillingPlanIn, Bundle, LangfuseCheckOut, LangfuseWarning, Stage,
} from "./billing-admin-api";
import { countText, cycleLabel, formatRm } from "./credit-view";

/**
 * Pure display helpers for the platform-admin Billing page. Money is shown digit for digit from
 * the API's exact strings (formatRm from the Credit tab's helpers): never added up or put through
 * a float. Billing days are Kuala Lumpur calendar days; date arithmetic here is whole numbers on
 * "YYYY-MM-DD" strings, and only "today" reads the clock (through Intl, in Asia/Kuala_Lumpur).
 */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const BILLING_ZONE = "Asia/Kuala_Lumpur";
const KL_OFFSET = "+08:00";
const CYCLE_DAY = 5;
const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;
const TIME_ONLY = /^([01]\d|2[0-3]):([0-5]\d)$/;
const DECIMAL4 = /^(-?)(\d+)(?:\.(\d{1,4}))?$/;
// What BillingPriceIn and BillingFxIn accept: positive, at most 8 decimal places, below 100000.
const USD_AMOUNT = /^\d{1,5}(?:\.\d{1,8})?$/;
// UsdToMyr's ceiling: a rate must be below 1,000 MYR for 1 USD.
const FX_CEILING = BigInt(1000);

/** The plan rule codes, as PlanRuleCode in csa/billing/plans.py lists them. */
export const PLAN_RULE_CODES = [
  "after_termination",
  "before_current_cycle",
  "before_join_date",
  "first_row_must_be_pilot_or_collaborator",
  "first_row_outside_current_cycle",
  "change_not_on_a_fifth",
  "bundle_change_same_stage_only",
] as const;

function plural(count: number, one: string, many: string): string {
  return `${countText(count)} ${count === 1 ? one : many}`;
}

// ---- Warnings ----

function warningSentence(warning: AdminWarning | LangfuseWarning | string, company: AdminCompanyOut): string {
  switch (warning) {
    case "pilot_ended":
      return "Pilot period has ended: convert to collaborator or terminate";
    case "usage_after_termination":
      return `Usage recorded after termination: ${plural(company.usage_after_termination, "execution", "executions")}, not billed`;
    case "not_set_up_with_usage":
      return `Has usage but no billing plan yet: ${plural(company.usage_without_plan, "execution", "executions")}, not billed`;
    case "usage_before_plan":
      return `Usage before the join date: ${plural(company.usage_without_plan, "execution", "executions")}, not billed`;
    case "pending_prices":
      return `Some usage has no price yet: ${plural(company.unpriced_calls, "call", "calls")} in ${plural(company.unpriced_executions, "execution", "executions")}`;
    case "langfuse_drift":
      return "Langfuse shows different token totals from our records for this cycle. The bill uses our records; check that usage is reaching Langfuse";
    default:
      return "Something about this company's billing needs a look";
  }
}

/**
 * The company's warnings as plain sentences, in the server's order, then those of a Langfuse
 * check of the same company and cycle when one has been run.
 */
export function warningSentences(company: AdminCompanyOut, check: Pick<LangfuseCheckOut, "warnings"> | null = null): string[] {
  const warnings: (AdminWarning | LangfuseWarning)[] = [...company.warnings, ...(check?.warnings ?? [])];
  return warnings.map((warning) => warningSentence(warning, company));
}

// ---- The Langfuse check ----

const CHECK_LABELS: Record<LangfuseCheckOut["result"], string> = {
  match: "Matches Langfuse",
  drift: "Does not match Langfuse",
  unavailable: "Langfuse not reachable",
};

/** The check's outcome in three words or so. */
export function langfuseCheckLabel(check: LangfuseCheckOut): string {
  return CHECK_LABELS[check.result] ?? "Langfuse not reachable";
}

/**
 * The check in a sentence or two: both sides' token totals when they differ. While the cycle is
 * open the last few minutes are left out on both sides, and the sentence says so.
 */
export function langfuseCheckDetail(check: LangfuseCheckOut, final: boolean): string {
  const recent = final ? "" : " Usage from the last few minutes is left out, as it can take a moment to reach Langfuse.";
  if (check.result === "match") return `Langfuse has the same token totals as our records, within 1%.${recent}`;
  if (check.result === "drift" && check.langfuse_input_tokens !== null && check.langfuse_output_tokens !== null) {
    return `Our records: ${countText(check.ledger_input_tokens)} tokens in, ${countText(check.ledger_output_tokens)} out. `
      + `Langfuse: ${countText(check.langfuse_input_tokens)} in, ${countText(check.langfuse_output_tokens)} out. `
      + `The bill always uses our records.${recent}`;
  }
  return "Nothing was compared, and the bill is not affected. Try again later.";
}

// ---- Errors ----

interface Refusal { status: number; code: string }

function refusal(cause: unknown): Refusal | null {
  if (typeof cause !== "object" || cause === null) return null;
  const { status, code } = cause as { status?: unknown; code?: unknown };
  return typeof status === "number" && typeof code === "string" ? { status, code } : null;
}

const SHARED: Record<string, string> = {
  platform_admin_only: "Only the platform team can change billing. Sign in with a platform account.",
  csrf_required: "Your session needs a refresh. Reload the page and try again.",
};

const PLAN_MESSAGES: Record<(typeof PLAN_RULE_CODES)[number] | string, string> = {
  first_row_outside_current_cycle:
    "A company's first plan must start inside the current billing cycle. Pick a date from the current cycle's start up to the day before the next 5th.",
  change_not_on_a_fifth: "Plan changes take effect on the 5th. Pick a 5th.",
  before_current_cycle:
    "That date falls in a billing cycle that has already closed. Pick a date on or after the current cycle's start.",
  after_termination:
    "This company has been terminated, and termination is final. No further plan changes can be saved.",
  first_row_must_be_pilot_or_collaborator:
    "This company has no plan yet, so there is nothing to terminate. Start it on Pilot or Collaborator first.",
  // Two causes: Pilot dated after a Collaborator plan, or Collaborator dated before a Pilot plan
  // already scheduled for a later 5th.
  bundle_change_same_stage_only:
    "A Pilot plan can never come after a Collaborator plan. This change would do that: either it starts Pilot after a Collaborator plan, or it starts Collaborator before a Pilot plan already scheduled for a later 5th. Pick a date after any scheduled Pilot plan, or a stage that keeps Pilot before Collaborator.",
  before_join_date:
    "That date is before this company's join date, when its billing started. Pick a date on or after the join date.",
  "tenant not found": "That company no longer exists. Reload the page.",
};

const PRICE_MESSAGES: Record<string, string> = {
  shadow_rule:
    "A price can only be back-dated before the earliest existing price for that model, to fill unpriced calls; otherwise it must start today or tomorrow. Pick another start.",
  unknown_provider: "Usage is only recorded for Groq or Gemini. Choose Groq or Gemini.",
  price_exists:
    "A price for this model already starts at exactly that time. Pick a different start time, or keep the existing price.",
};

const FX_MESSAGES: Record<string, string> = {
  shadow_rule:
    "A rate can only be back-dated before the earliest existing rate, to fill unpriced calls; otherwise it must start today or tomorrow. Pick another start.",
  fx_rate_exists:
    "A rate already starts at exactly that time. Pick a different start time, or keep the existing rate.",
};

const VALIDATION = {
  plan: "Check the form: choose a stage, a bundle (not needed to terminate) and a date, and write a short reason.",
  price:
    "Check the form: choose a provider, type the model name exactly as the provider writes it, enter both prices above zero with at most 8 decimal places, and pick a start date and time.",
  fx: "Check the form: enter a rate above zero and below 1,000, with at most 8 decimal places, and pick a start date and time.",
};

function explain(cause: unknown, own: Record<string, string>, validation: string): string {
  const found = refusal(cause);
  if (!found) return "We could not reach the server. Check your connection and try again.";
  const { status, code } = found;
  if (Object.hasOwn(own, code)) return own[code];
  if (Object.hasOwn(SHARED, code)) return SHARED[code];
  if (status === 401) return "Your session has ended. Sign in again, then try again.";
  if (status === 422) return validation;
  if (status >= 500) return "Something went wrong on our side, so this may not have been saved. Reload the page to check, then try again.";
  return "This could not be saved. Reload the page and try again.";
}

/** The plan form's plain-English sentence for a refusal. */
export function planErrorMessage(cause: unknown): string {
  return explain(cause, PLAN_MESSAGES, VALIDATION.plan);
}

/** The price form's plain-English sentence for a refusal. */
export function priceErrorMessage(cause: unknown): string {
  return explain(cause, PRICE_MESSAGES, VALIDATION.price);
}

/** The FX form's plain-English sentence for a refusal. */
export function fxErrorMessage(cause: unknown): string {
  return explain(cause, FX_MESSAGES, VALIDATION.fx);
}

const OVERVIEW_MESSAGES: Record<string, string> = {
  cycle_must_start_on_a_fifth: "That is not a billing cycle. Choose a cycle from the list.",
  cycle_not_found: "That billing cycle has not started yet. Choose the current cycle or an earlier one.",
  platform_admin_only: SHARED.platform_admin_only,
};

/** The Billing page's plain-English sentence when the overview cannot be read; never a raw code. */
export function overviewErrorMessage(cause: unknown): string {
  const found = refusal(cause);
  if (!found) return "We could not reach the server to load billing. Check your connection and try again.";
  const { status, code } = found;
  if (Object.hasOwn(OVERVIEW_MESSAGES, code)) return OVERVIEW_MESSAGES[code];
  if (status === 401) return "Your session has ended. Sign in again, then try again.";
  if (status >= 500) return "Something went wrong on our side while loading billing. Try again.";
  return "We could not load billing for that cycle. Reload the page or choose another cycle.";
}

const CHECK_MESSAGES: Record<string, string> = {
  platform_admin_only: "Only the platform team can run this check. Sign in with a platform account.",
  "tenant not found": "That company no longer exists. Reload the page.",
  cycle_must_start_on_a_fifth: OVERVIEW_MESSAGES.cycle_must_start_on_a_fifth,
  cycle_not_found: OVERVIEW_MESSAGES.cycle_not_found,
};

/** The sentence when our own server could not run the Langfuse check (not Langfuse being down). */
export function langfuseCheckErrorMessage(cause: unknown): string {
  const found = refusal(cause);
  if (!found) return "We could not reach the server to run the check. Check your connection and try again.";
  const { status, code } = found;
  if (Object.hasOwn(CHECK_MESSAGES, code)) return CHECK_MESSAGES[code];
  if (status === 401) return "Your session has ended. Sign in again, then try again.";
  return "The check could not be run this time. Try again in a moment.";
}

// ---- Money and counts ----

/** Raw provider cost, every digit the API gives (RM0.0001). */
export function rawCostText(company: AdminCompanyOut): string {
  return formatRm(company.raw_cost_rm, 4);
}

/** AI margin (AI charge less raw cost), RM0.01; a loss shows its minus sign. */
export function marginText(company: AdminCompanyOut): string {
  return formatRm(company.ai_margin_rm, 2);
}

/** Raw cost per priced execution (RM0.0001), or words when nothing is priced. */
export function costPerExecutionText(company: AdminCompanyOut): string {
  return company.cost_per_execution_rm === null ? "No priced executions" : formatRm(company.cost_per_execution_rm, 4);
}

/** A 4-place decimal as a whole number of RM0.0001 units, or null. */
function units(value: string): bigint | null {
  const match = DECIMAL4.exec(value);
  if (!match) return null;
  const [, sign, whole, fraction = ""] = match;
  const scaled = BigInt(whole + fraction.padEnd(4, "0"));
  return sign ? -scaled : scaled;
}

/** The cost per execution against the workbook's assumed cost per message, in words. */
export function workbookNote(company: AdminCompanyOut): string {
  const workbook = formatRm(company.workbook_cost_per_message_rm, 4);
  const cost = company.cost_per_execution_rm === null ? null : units(company.cost_per_execution_rm);
  const assumed = units(company.workbook_cost_per_message_rm);
  if (cost === null || assumed === null) return `Workbook assumes ${workbook}`;
  if (cost < assumed) return `Less than the workbook's ${workbook}`;
  if (cost > assumed) return `More than the workbook's ${workbook}`;
  return `Same as the workbook's ${workbook}`;
}

export function tokenText(count: number): string {
  return countText(count);
}

// ---- Labels ----

const STAGES: Record<Stage, string> = { pilot: "Pilot", collaborator: "Collaborator", terminated: "Terminated" };
const BUNDLES: Record<Bundle, string> = { individual: "Individual", curated: "Curated", complete: "Complete" };

export function stageLabel(stage: Stage | null): string {
  return stage === null ? "No plan" : STAGES[stage];
}

export function bundleLabel(bundle: Bundle | null): string {
  return bundle === null ? "—" : BUNDLES[bundle];
}

/** "5 Dec 2026"; "—" for anything that is not a date. */
export function fullDay(day: string): string {
  const match = DATE_ONLY.exec(day);
  if (!match) return "—";
  return `${Number(match[3])} ${MONTHS[Number(match[2]) - 1]} ${match[1]}`;
}

/**
 * The company's plan in words: "Pilot, Individual", "Not set up", "Terminated on 10 Nov 2026",
 * or a plan with its termination still ahead ("...; terminates on 1 Dec 2026").
 */
export function planStatus(company: CurrentPlan, now: Date = new Date()): string {
  const plan = company.stage === null ? "Not set up" : `${stageLabel(company.stage)}, ${bundleLabel(company.bundle)}`;
  if (company.terminated_on === null) return plan;
  if (company.terminated_on <= klToday(now)) return `Terminated on ${fullDay(company.terminated_on)}`;
  return `${plan}; terminates on ${fullDay(company.terminated_on)}`;
}

// ---- Kuala Lumpur days and the 5ths ----

function pad(value: number, width: number): string {
  return String(value).padStart(width, "0");
}

function parts(day: string): [number, number, number] {
  const match = DATE_ONLY.exec(day);
  if (!match) throw new Error(`not a date: ${day}`);
  return [Number(match[1]), Number(match[2]), Number(match[3])];
}

/** Today's date in Kuala Lumpur, "YYYY-MM-DD". */
export function klToday(now: Date = new Date()): string {
  const found = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", { timeZone: BILLING_ZONE, year: "numeric", month: "numeric", day: "numeric" })
      .formatToParts(now).map((part) => [part.type, part.value]),
  );
  return `${pad(Number(found.year), 4)}-${pad(Number(found.month), 2)}-${pad(Number(found.day), 2)}`;
}

/** The 5th `months` months after (or before, when negative) the 5th `fifth`'s month. */
export function addMonths(fifth: string, months: number): string {
  const [year, month] = parts(fifth);
  const index = year * 12 + (month - 1) + months;
  return `${pad(Math.floor(index / 12), 4)}-${pad((index % 12) + 1, 2)}-${pad(CYCLE_DAY, 2)}`;
}

/** The start of the billing cycle containing `day`: the latest 5th on or before it. */
export function cycleStartOf(day: string): string {
  const [year, month, date] = parts(day);
  const fifth = `${pad(year, 4)}-${pad(month, 2)}-${pad(CYCLE_DAY, 2)}`;
  return date >= CYCLE_DAY ? fifth : addMonths(fifth, -1);
}

export interface CycleOption { start: string; end: string; open: boolean; label: string }

/** The current cycle and the `count - 1` before it, newest first, for the cycle picker. */
export function cycleOptions(now: Date = new Date(), count = 12): CycleOption[] {
  const current = cycleStartOf(klToday(now));
  return Array.from({ length: count }, (_, back) => {
    const start = addMonths(current, -back);
    const end = addMonths(start, 1);
    const open = back === 0;
    return { start, end, open, label: `${cycleLabel(start, end, now)}${open ? " (open)" : ""}` };
  });
}

export interface DateSuggestion { date: string; label: string }

/**
 * Dates the plan form offers: the current cycle's start and the next three 5ths; a termination
 * (any day is allowed) also offers today first. The join-date rule is the server's to check.
 */
export function suggestedDates(now: Date, stage: Stage): DateSuggestion[] {
  const today = klToday(now);
  const start = cycleStartOf(today);
  const fifths = [0, 1, 2, 3].map((ahead) => addMonths(start, ahead));
  const found: DateSuggestion[] = fifths.map((date, index) => ({
    date,
    label: index === 0 ? `${fullDay(date)} (current cycle start)` : fullDay(date),
  }));
  if (stage === "terminated" && today !== start) found.unshift({ date: today, label: `${fullDay(today)} (today)` });
  return found;
}

/** A Kuala Lumpur date and time as an ISO date-time with its offset, or null if either is unset or malformed. */
export function klDateTime(date: string, time: string): string | null {
  if (!DATE_ONLY.test(date) || !TIME_ONLY.test(time)) return null;
  return `${date}T${time}:00${KL_OFFSET}`;
}

/** A USD amount the server accepts: above zero, at most 8 decimal places, below 100,000. */
export function isUsdAmount(text: string): boolean {
  return USD_AMOUNT.test(text) && /[1-9]/.test(text);
}

/** A USD to MYR rate the server accepts: a USD amount below 1,000, compared as whole digits. */
export function isUsdToMyr(text: string): boolean {
  if (!isUsdAmount(text)) return false;
  return BigInt(text.split(".")[0]) < FX_CEILING;
}

// ---- The plan form ----

/** What the plan form knows about a company's plan; null when it does not know it. */
export type CurrentPlan = Pick<AdminCompanyOut, "stage" | "bundle" | "terminated_on">;

/** The stage and bundle the form shows; "" is the empty "Choose…" option. */
export interface PlanChoice { stage: Stage | ""; bundle: Bundle | "" }

/**
 * Where the form starts. A known plan starts on itself (a company not set up yet starts on Pilot,
 * Individual, the usual first plan). An unknown plan starts on nothing chosen: guessing would let a
 * wrong plan be saved that the server accepts, such as a bundle change or a same-date correction.
 */
export function initialPlanChoice(current: CurrentPlan | null): PlanChoice {
  if (current === null) return { stage: "", bundle: "" };
  const stage = current.stage === "pilot" || current.stage === "collaborator" ? current.stage : "pilot";
  return { stage, bundle: current.bundle ?? "individual" };
}

/** Whether the choice can be sent: a stage, and a bundle unless terminating (which keeps the bundle in force). */
export function planChoiceReady(choice: PlanChoice): boolean {
  if (choice.stage === "") return false;
  return choice.stage === "terminated" || choice.bundle !== "";
}

/** One company's plan from the overview, or null when there is no overview or it is not listed. */
export function currentPlanOf(overview: AdminOverviewOut | null, clientId: string): CurrentPlan | null {
  const found = overview?.companies.find((company) => company.client_id === clientId);
  return found ? { stage: found.stage, bundle: found.bundle, terminated_on: found.terminated_on } : null;
}

export interface PlanForm extends PlanChoice { effective_from: string; reason: string }

/**
 * The plan request, or null until a stage (and a bundle unless terminating) is chosen. A
 * termination leaves the bundle out, so the server keeps the one in force.
 */
export function planBody(form: PlanForm): BillingPlanIn | null {
  if (!planChoiceReady(form) || form.stage === "") return null;
  const reason = form.reason.trim();
  if (form.stage === "terminated") return { stage: form.stage, effective_from: form.effective_from, reason };
  if (form.bundle === "") return null;
  return { stage: form.stage, bundle: form.bundle, effective_from: form.effective_from, reason };
}
