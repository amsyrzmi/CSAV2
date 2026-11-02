/**
 * Pure date rules for the calendar date-range filter (components/console/kit/DateRangeFilter.tsx).
 * A day is a local calendar date `YYYY-MM-DD`; a range holds inclusive days. Dates are built
 * from their parts (never parsed from free-form text), so the host's time zone only matters in
 * `toInstants` and `todayLocal`, where it is meant to.
 */
export type DayRange = { from: string | null; to: string | null };

export type PresetKey = "today" | "last7" | "last30" | "month" | "clear";

export const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
] as const;

/** Monday first, matching the default week start of `monthGrid`. */
export const WEEKDAY_NAMES = [
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
  "Sunday",
] as const;

const DAY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;

const pad = (value: number, width: number) => String(value).padStart(width, "0");

/** The day string for a year, a month from 1 to 12 and a day of month. */
export function formatDay(year: number, month: number, day: number): string {
  return `${pad(year, 4)}-${pad(month, 2)}-${pad(day, 2)}`;
}

/** The parts of a real calendar day, or null for anything else (including 2026-02-30). */
export function parseDay(day: string): { year: number; month: number; day: number } | null {
  const match = DAY_PATTERN.exec(day);
  if (!match) return null;
  const [year, month, dayOfMonth] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const check = new Date(Date.UTC(year, month - 1, dayOfMonth));
  check.setUTCFullYear(year); // years 0 to 99 would otherwise map to 1900 to 1999
  if (
    check.getUTCFullYear() !== year ||
    check.getUTCMonth() !== month - 1 ||
    check.getUTCDate() !== dayOfMonth
  )
    return null;
  return { year, month, day: dayOfMonth };
}

/** Days in a month (month from 1 to 12). */
export function daysInMonth(year: number, month: number): number {
  const probe = new Date(Date.UTC(2000, month, 0));
  probe.setUTCFullYear(year, month, 0);
  return probe.getUTCDate();
}

/** `day` moved by a whole number of days (negative goes back). Returns `day` if it is not a real day. */
export function addDays(day: string, amount: number): string {
  const parts = parseDay(day);
  if (!parts) return day;
  const moved = new Date(Date.UTC(2000, 0, 1));
  moved.setUTCFullYear(parts.year, parts.month - 1, parts.day + amount);
  return formatDay(moved.getUTCFullYear(), moved.getUTCMonth() + 1, moved.getUTCDate());
}

/** `day` moved by whole months; the day of month is clamped (31 Jan plus one month is 28 or 29 Feb). */
export function addMonths(day: string, amount: number): string {
  const parts = parseDay(day);
  if (!parts) return day;
  const index = parts.year * 12 + (parts.month - 1) + amount;
  const year = Math.floor(index / 12);
  const month = (index % 12 + 12) % 12 + 1;
  return formatDay(year, month, Math.min(parts.day, daysInMonth(year, month)));
}

/** Negative before, zero equal, positive after. Day strings sort as text. */
export function compareDays(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

/** Today's date in the browser's time zone. */
export function todayLocal(now: Date = new Date()): string {
  return formatDay(now.getFullYear(), now.getMonth() + 1, now.getDate());
}

/** "November 2026". */
export function monthLabel(year: number, month: number): string {
  return `${MONTH_NAMES[month - 1] ?? ""} ${year}`;
}

/** "12 November 2026", for a day button's accessible name. */
export function dayLabel(day: string): string {
  const parts = parseDay(day);
  return parts ? `${parts.day} ${MONTH_NAMES[parts.month - 1]} ${parts.year}` : day;
}

/** Position of a day in its week: 0 for the first day of the week (Monday by default) to 6. */
export function weekdayIndex(day: string, weekStart: 0 | 1 = 1): number {
  const parts = parseDay(day);
  if (!parts) return 0;
  const probe = new Date(Date.UTC(2000, 0, 1));
  probe.setUTCFullYear(parts.year, parts.month - 1, parts.day);
  return (probe.getUTCDay() - weekStart + 7) % 7;
}

/**
 * The weeks of a month (month from 1 to 12) as rows of 7 day strings, with null padding before
 * the 1st and after the last day. `weekStart` 1 (the default) begins on Monday, 0 on Sunday.
 */
export function monthGrid(year: number, month: number, weekStart: 0 | 1 = 1): (string | null)[][] {
  const first = new Date(Date.UTC(2000, 0, 1));
  first.setUTCFullYear(year, month - 1, 1);
  const lead = (first.getUTCDay() - weekStart + 7) % 7;
  const cells: (string | null)[] = Array.from({ length: lead }, () => null);
  const total = daysInMonth(year, month);
  for (let day = 1; day <= total; day += 1) cells.push(formatDay(year, month, day));
  while (cells.length % 7 !== 0) cells.push(null);
  const rows: (string | null)[][] = [];
  for (let index = 0; index < cells.length; index += 7) rows.push(cells.slice(index, index + 7));
  return rows;
}

/** The quick choices, relative to `today`. "Last 7 days" is today and the six days before. */
export function presets(today: string): { key: PresetKey; label: string; range: DayRange }[] {
  const parts = parseDay(today);
  const monthStart = parts ? formatDay(parts.year, parts.month, 1) : today;
  const monthEnd = parts ? formatDay(parts.year, parts.month, daysInMonth(parts.year, parts.month)) : today;
  return [
    { key: "today", label: "Today", range: { from: today, to: today } },
    { key: "last7", label: "Last 7 days", range: { from: addDays(today, -6), to: today } },
    { key: "last30", label: "Last 30 days", range: { from: addDays(today, -29), to: today } },
    { key: "month", label: "This month", range: { from: monthStart, to: monthEnd } },
    { key: "clear", label: "Clear", range: { from: null, to: null } },
  ];
}

/**
 * One click on a day. A first click (nothing chosen) sets a one-day range; a click on a one-day
 * range sets the other end (swapping when earlier); a click when the two ends differ restarts
 * at that day.
 */
export function selectDay(range: DayRange, day: string): DayRange {
  const anchor = range.from ?? range.to;
  const complete = range.from !== null && range.to !== null && range.from !== range.to;
  if (anchor === null || complete) return { from: day, to: day };
  return compareDays(day, anchor) < 0 ? { from: day, to: anchor } : { from: anchor, to: day };
}

function localStart(day: string): string | null {
  const parts = parseDay(day);
  if (!parts) return null;
  return new Date(parts.year, parts.month - 1, parts.day, 0, 0, 0, 0).toISOString();
}

/**
 * The range as ISO UTC instants for the server: `from` is the local start of the first day,
 * `to` the local start of the day AFTER the last day (an exclusive end). Null stays null. Uses
 * the browser's time zone only. `_now` is accepted for call-site symmetry and is not used.
 */
export function toInstants(
  range: DayRange,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars -- part of the agreed signature
  _now?: Date,
): { from: string | null; to: string | null } {
  return {
    from: range.from === null ? null : localStart(range.from),
    to: range.to === null ? null : localStart(addDays(range.to, 1)),
  };
}

function short(parts: { day: number; month: number }): string {
  return `${parts.day} ${MONTH_NAMES[parts.month - 1]?.slice(0, 3)}`;
}

/** "Any date", "12 Nov 2026" or "12 Nov to 18 Nov 2026" (years shown on both ends when they differ). */
export function describeRange(range: DayRange): string {
  const start = range.from ? parseDay(range.from) : null;
  const end = range.to ? parseDay(range.to) : null;
  const only = start ?? end;
  if (!only) return "Any date";
  if (!start || !end || range.from === range.to) return `${short(only)} ${only.year}`;
  if (start.year === end.year) return `${short(start)} to ${short(end)} ${end.year}`;
  return `${short(start)} ${start.year} to ${short(end)} ${end.year}`;
}
