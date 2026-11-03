/** Small pure helpers for the People & HR guide pieces (Add person, Settings). */

/** Today's date in the browser's own time zone as YYYY-MM-DD (never the UTC date). */
export function localToday(now: Date = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** The longest holiday list the field has always accepted. */
export const HOLIDAY_LIST_MAX = 200;

export type HolidayAdd = { list: string; error?: undefined } | { list?: undefined; error: string };

/**
 * Adds one date to the comma list the owner can still edit. The list is read the way Settings reads
 * it when saving (split on commas, trimmed, blanks dropped) and written back as "a, b, c".
 */
export function addHolidayDate(list: string, date: string, max: number = HOLIDAY_LIST_MAX): HolidayAdd {
  const day = date.trim();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return { error: "Choose a date first." };
  const items = list.split(",").map((item) => item.trim()).filter(Boolean);
  if (items.includes(day)) return { error: "That date is already in the list." };
  const next = [...items, day].join(", ");
  if (next.length > max) return { error: "The list is full. Remove a date from it before adding another." };
  return { list: next };
}
