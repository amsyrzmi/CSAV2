import type { CyclePickOut, UsageOut, UsageStatus } from "./credit-api";

/**
 * Pure display helpers for the Credit tab. Money arrives as exact decimal strings and is shown
 * digit for digit: it is never added up or formatted through a float here. A chart row's
 * `value` is a number only so the chart can size its bar; every figure a reader sees comes from
 * the string. Billing days are Kuala Lumpur calendar days, written as the server sends them.
 */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const BILLING_ZONE = "Asia/Kuala_Lumpur";
const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/;
const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/;
const ZERO = BigInt(0);
const ONE = BigInt(1);
const TEN = BigInt(10);
/** Chart rows hold prices in units of RM 0.0001, the finest price the server sends. */
const UNIT_PLACES = 4;
const UNITS_PER_RM = 10_000;
const MAX_TICK_STEPS = 4;

/** The decimal as a whole number of 10^-places units, rounding half up past `places`; null if not a decimal. */
function scaled(value: string, places: number): bigint | null {
  const match = DECIMAL.exec(value);
  if (!match) return null;
  const [, sign, whole, fraction = ""] = match;
  let units = BigInt(whole + fraction.slice(0, places).padEnd(places, "0"));
  if (fraction.length > places && fraction[places] >= "5") units += ONE;
  return sign ? -units : units;
}

function grouped(digits: string): string {
  return digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** A whole number of 10^-places units back to a plain decimal string ("12.3400"). */
function unitsText(units: bigint, places: number): string {
  const negative = units < ZERO;
  const magnitude = negative ? -units : units;
  const scale = TEN ** BigInt(places);
  const fraction = (magnitude % scale).toString().padStart(places, "0");
  return `${negative ? "-" : ""}${(magnitude / scale).toString()}.${fraction}`;
}

/** "RM 1,234.50": every digit kept, thousands grouped; "—" for anything that is not a decimal. */
export function formatRm(value: string, places: 2 | 4): string {
  const units = scaled(value, places);
  if (units === null) return "—";
  const negative = units < ZERO;
  const [whole, fraction] = unitsText(negative ? -units : units, places).split(".");
  return `${negative ? "-" : ""}RM ${grouped(whole)}.${fraction}`;
}

/** A whole count with thousands grouped ("1,234"). */
export function countText(count: number): string {
  return grouped(String(count));
}

function currentYear(now: Date): string {
  return new Intl.DateTimeFormat("en-GB", { timeZone: BILLING_ZONE, year: "numeric" }).format(now);
}

function dayParts(day: string): { year: string; text: string } | null {
  const match = DATE_ONLY.exec(day);
  if (!match) return null;
  return { year: match[1], text: `${Number(match[3])} ${MONTHS[Number(match[2]) - 1]}` };
}

/** "5 Oct", or "5 Oct 2025" outside the current year. */
export function dayLabel(day: string, now: Date = new Date()): string {
  const parts = dayParts(day);
  if (!parts) return "—";
  return parts.year === currentYear(now) ? parts.text : `${parts.text} ${parts.year}`;
}

/** "5 Sep to 5 Oct"; both years are written when either is not the current year. */
export function cycleLabel(start: string, end: string, now: Date = new Date()): string {
  const from = dayParts(start);
  const to = dayParts(end);
  if (!from || !to) return "—";
  const year = currentYear(now);
  if (from.year === year && to.year === year) return `${from.text} to ${to.text}`;
  return `${from.text} ${from.year} to ${to.text} ${to.year}`;
}

/** The open cycle's notice that its bill is not final yet; none for a closed cycle or no bill. */
export function banner(usage: UsageOut, now: Date = new Date()): string | null {
  if (usage.final || usage.status === "not_set_up") return null;
  return `Bill so far, final on ${dayLabel(usage.cycle_end, now)}`;
}

/** When only part of the cycle is billed: "Billed from 15 Aug", "Billed to 20 Sep" or both. */
export function proratedNote(usage: UsageOut, now: Date = new Date()): string | null {
  const from = usage.billed_from && usage.billed_from > usage.cycle_start ? dayLabel(usage.billed_from, now) : null;
  const to = usage.billed_to && usage.billed_to < usage.cycle_end ? dayLabel(usage.billed_to, now) : null;
  if (from && to) return `Billed from ${from} to ${to}`;
  if (from) return `Billed from ${from}`;
  if (to) return `Billed to ${to}`;
  return null;
}

const STATE_MESSAGES: Record<UsageStatus, string | null> = {
  ok: null,
  pending_prices: "Some usage is still being priced",
  not_set_up: "Billing not set up yet",
};

export function stateMessage(status: UsageStatus): string | null {
  return STATE_MESSAGES[status] ?? null;
}

/** One bar: `value` sizes it, `text` is what a reader sees (from the exact `price`). */
export interface ChartRow {
  key: string;
  label: string;
  value: number;
  price: string;
  text: string;
}

export interface AgentRow extends ChartRow {
  executions: number;
}

function chartValue(price: string): number {
  return Number(scaled(price, UNIT_PLACES) ?? ZERO) / UNITS_PER_RM;
}

/** Every day of the cycle in the server's order, days with no usage included. */
export function dailySeries(usage: UsageOut): ChartRow[] {
  return usage.daily.map(({ day, price }) => ({
    key: day,
    label: dayParts(day)?.text ?? day,
    value: chartValue(price),
    price,
    text: formatRm(price, 4),
  }));
}

/** Every AI agent in the order the server gave. */
export function agentSeries(usage: UsageOut): AgentRow[] {
  return usage.by_agent.map(({ agent_id, title, executions, price }) => ({
    key: agent_id,
    label: title,
    value: chartValue(price),
    price,
    text: formatRm(price, 4),
    executions,
  }));
}

/** The row with the highest price (the first of equals), or null when every price is zero. */
export function peakRow<T extends ChartRow>(rows: T[]): T | null {
  let best: T | null = null;
  let bestUnits = ZERO;
  for (const row of rows) {
    const units = scaled(row.price, UNIT_PLACES) ?? ZERO;
    if (units > bestUnits) {
      best = row;
      bestUnits = units;
    }
  }
  return best;
}

function ceilDiv(a: bigint, b: bigint): bigint {
  return (a + b - ONE) / b;
}

/** The smallest 1, 2 or 5 times a power of ten that reaches `max` in at most four steps. */
function tickStep(max: bigint): bigint {
  for (let power = ONE; ; power *= TEN) {
    for (const factor of [1, 2, 5]) {
      const step = BigInt(factor) * power;
      if (ceilDiv(max, step) <= BigInt(MAX_TICK_STEPS)) return step;
    }
  }
}

/**
 * Value-axis ticks from zero to just past the highest price, in at most four clean steps of
 * 1, 2 or 5 times a power of ten. Each label is an exact amount: 2 places when the step is whole
 * sen, else 4.
 */
export function priceTicks(prices: string[]): { ticks: number[]; labels: string[] } {
  let max = ZERO;
  for (const price of prices) {
    const units = scaled(price, UNIT_PLACES);
    if (units !== null && units > max) max = units;
  }
  if (max === ZERO) return { ticks: [0], labels: [formatRm("0", 2)] };
  const step = tickStep(max);
  const places = step % BigInt(100) === ZERO ? 2 : 4;
  const ticks: number[] = [];
  const labels: string[] = [];
  for (let units = ZERO; units < max + step; units += step) {
    ticks.push(Number(units) / UNITS_PER_RM);
    labels.push(formatRm(unitsText(units, UNIT_PLACES), places));
    if (units >= max) break;
  }
  return { ticks, labels };
}

/** The picker's words for a cycle: its dates, whether it is open, and its bill at 2 places. */
export function cycleOption(pick: CyclePickOut, now: Date = new Date()): string {
  const dates = `${cycleLabel(pick.start, pick.end, now)}${pick.final ? "" : " (open)"}`;
  if (pick.total === null) return `${dates}, billing not set up`;
  const bill = formatRm(pick.total, 2);
  return pick.final ? `${dates}, ${bill}` : `${dates}, bill so far ${bill}`;
}

export function newestFirst(picks: CyclePickOut[]): CyclePickOut[] {
  return [...picks].sort((a, b) => (a.start < b.start ? 1 : a.start > b.start ? -1 : 0));
}
