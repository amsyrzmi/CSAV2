/**
 * Pure rules for the Bookings setup pages: how hours read, the seven-day hours editor, a booking
 * type's form (and its round trip to the API body), plain labels, and wall-clock times in the
 * company's time zone. Only type imports, so the node tests load this file on its own.
 */
import type {
  BookingAudience,
  BookingDelivery,
  BookingHoursOut,
  BookingLocationKind,
  BookingPeriod,
  BookingResourceRule,
  BookingTypeIn,
  BookingTypeOut,
  BookingTypePatch,
} from "@/lib/bookings-api";

export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"] as const;
/** The server refuses anything that runs past midnight, so this is the latest a day can close. */
export const LATEST_CLOSE = "23:59";
const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

// ---- Hours -------------------------------------------------------------------------------

export interface HoursRow { opens: string; closes: string }
/** Seven days, Monday first; an empty day is closed. */
export type HoursDays = HoursRow[][];

function dayRuns(days: number[]): string {
  const runs: number[][] = [];
  for (const day of days) {
    const last = runs[runs.length - 1];
    if (last && last[last.length - 1] === day - 1) last.push(day);
    else runs.push([day]);
  }
  return runs
    .map((run) => {
      const first = WEEKDAYS[run[0]];
      const end = WEEKDAYS[run[run.length - 1]];
      return run.length >= 3 ? `${first} to ${end}` : run.length === 2 ? `${first} and ${end}` : first;
    })
    .join(", ");
}

/**
 * What a set of hours says, one line per distinct day pattern. `is_default` means two different
 * things and is read by who is asking: for the company it marks the default week; for a person or
 * room, no periods always means "follows the company" whatever the flag says.
 */
export function hoursLines(hours: Pick<BookingHoursOut, "subject_kind" | "is_default" | "periods">): string[] {
  if (hours.periods.length === 0) {
    return [hours.subject_kind === "company" ? "Closed every day" : "Same as the company"];
  }
  const days = periodsToDays(hours.periods);
  const groups = new Map<string, number[]>();
  days.forEach((rows, weekday) => {
    if (rows.length === 0) return;
    const text = rows.map((row) => `${row.opens} to ${row.closes}`).join(" and ");
    groups.set(text, [...(groups.get(text) ?? []), weekday]);
  });
  const mark = hours.subject_kind === "company" && hours.is_default ? " (default)" : "";
  return [...groups].map(([text, weekdays]) => `${dayRuns(weekdays)}, ${text}${mark}`);
}

export function hoursLabel(hours: Pick<BookingHoursOut, "subject_kind" | "is_default" | "periods">): string {
  return hoursLines(hours).join("; ");
}

const byOpening = (a: HoursRow, b: HoursRow) => a.opens.localeCompare(b.opens);

export function periodsToDays(periods: BookingPeriod[]): HoursDays {
  const days: HoursDays = WEEKDAYS.map(() => []);
  for (const period of periods) days[period.weekday]?.push({ opens: period.opens, closes: period.closes });
  return days.map((rows) => [...rows].sort(byOpening));
}

export function daysToPeriods(days: HoursDays): BookingPeriod[] {
  return days.flatMap((rows, weekday) =>
    [...rows].sort(byOpening).map((row) => ({ weekday, opens: row.opens, closes: row.closes })),
  );
}

/** The first thing wrong with the hours, as a sentence, or null when the server will take them. */
export function hoursProblem(days: HoursDays): string | null {
  for (const [weekday, rows] of days.entries()) {
    const name = WEEKDAYS[weekday];
    for (const row of rows) {
      if (!TIME.test(row.opens) || !TIME.test(row.closes)) {
        return `${name}: choose an opening and a closing time, or remove the empty period.`;
      }
      if (row.closes <= row.opens) {
        return `${name}: each period must open before it closes. Hours can't run past midnight, so the latest closing time is ${LATEST_CLOSE}.`;
      }
    }
    const sorted = [...rows].sort(byOpening);
    for (let index = 1; index < sorted.length; index += 1) {
      if (sorted[index].opens < sorted[index - 1].closes) return `${name}: periods can't overlap.`;
    }
  }
  return null;
}

// ---- Booking types -----------------------------------------------------------------------

const AUDIENCE: Record<BookingAudience, string> = {
  customers: "Customers",
  staff: "Staff only",
  both: "Customers and staff",
};
const DELIVERY: Record<BookingDelivery, string> = {
  named: "A person you choose",
  any: "Any free person",
  several: "Several people together",
};
const LOCATION: Record<BookingLocationKind, string> = {
  in_person: "In person",
  phone: "By phone",
  online: "Online",
};
const RESOURCE_RULE: Record<BookingResourceRule, string> = {
  none: "No room needed",
  specific: "These rooms, all held for the booking",
  any: "Any free room from these",
};
export const audienceLabel = (value: BookingAudience) => AUDIENCE[value];
export const deliveryLabel = (value: BookingDelivery) => DELIVERY[value];
export const locationLabel = (value: BookingLocationKind) => LOCATION[value];
export const resourceRuleLabel = (value: BookingResourceRule) => RESOURCE_RULE[value];
export const AUDIENCES = Object.keys(AUDIENCE) as BookingAudience[];
export const DELIVERIES = Object.keys(DELIVERY) as BookingDelivery[];
export const LOCATIONS = Object.keys(LOCATION) as BookingLocationKind[];
export const RESOURCE_RULES = Object.keys(RESOURCE_RULE) as BookingResourceRule[];

const plural = (count: number, unit: string) => `${count} ${unit}${count === 1 ? "" : "s"}`;

/** "30 minutes", "1 hour 30 minutes", "2 days"; 0 is "None". */
export function durationText(minutes: number): string {
  if (!Number.isFinite(minutes) || minutes <= 0) return "None";
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const rest = minutes % 60;
  return [days && plural(days, "day"), hours && plural(hours, "hour"), rest && plural(rest, "minute")]
    .filter(Boolean)
    .join(" ");
}

export function typeSummary(type: Pick<BookingTypeOut, "duration_minutes" | "audience" | "delivery" | "location_kind">): string {
  return [
    durationText(type.duration_minutes),
    audienceLabel(type.audience),
    deliveryLabel(type.delivery),
    locationLabel(type.location_kind),
  ].join(" · ");
}

/** The booking type form; numbers stay text while they are typed. */
export interface TypeForm {
  name: string;
  duration: string;
  buffer: string;
  audience: BookingAudience;
  delivery: BookingDelivery;
  resourceRule: BookingResourceRule;
  locationKind: BookingLocationKind;
  address: string;
  link: string;
  needsConfirmation: boolean;
  minNotice: string;
  maxAdvance: string;
  cutoff: string;
  personIds: string[];
  resourceIds: string[];
  active: boolean;
}

/** The server's own defaults, so a new type behaves as the API documents. */
export function emptyTypeForm(): TypeForm {
  return {
    name: "", duration: "30", buffer: "0", audience: "customers", delivery: "any", resourceRule: "none",
    locationKind: "phone", address: "", link: "", needsConfirmation: false, minNotice: "60",
    maxAdvance: "60", cutoff: "120", personIds: [], resourceIds: [], active: true,
  };
}

export function typeToForm(type: BookingTypeOut): TypeForm {
  return {
    name: type.name,
    duration: String(type.duration_minutes),
    buffer: String(type.buffer_minutes),
    audience: type.audience,
    delivery: type.delivery,
    resourceRule: type.resource_rule,
    locationKind: type.location_kind,
    address: type.address ?? "",
    link: type.meeting_link ?? "",
    needsConfirmation: type.needs_confirmation,
    minNotice: String(type.min_notice_minutes),
    maxAdvance: String(type.max_advance_days),
    cutoff: String(type.change_cutoff_minutes),
    personIds: [...type.person_ids],
    resourceIds: [...type.resource_ids],
    active: type.is_active,
  };
}

const HTTPS_LINK = /^https:\/\/[^\s/]\S*$/;
const whole = (text: string): number | null => (/^\d+$/.test(text.trim()) ? Number(text.trim()) : null);

/** Keeps only what the chosen rules use, as the server does. Call it on a form with no problem. */
export function formToTypeIn(form: TypeForm): BookingTypeIn {
  return {
    name: form.name.trim(),
    duration_minutes: Number(form.duration),
    buffer_minutes: Number(form.buffer),
    audience: form.audience,
    delivery: form.delivery,
    resource_rule: form.resourceRule,
    location_kind: form.locationKind,
    address: form.locationKind === "in_person" ? form.address.trim() : null,
    meeting_link: form.locationKind === "online" ? form.link.trim() : null,
    needs_confirmation: form.needsConfirmation,
    min_notice_minutes: Number(form.minNotice),
    max_advance_days: Number(form.maxAdvance),
    change_cutoff_minutes: Number(form.cutoff),
    person_ids: form.personIds,
    resource_ids: form.resourceRule === "none" ? [] : form.resourceIds,
  };
}

export function formToTypePatch(form: TypeForm, version: number): BookingTypePatch {
  return { ...formToTypeIn(form), version, is_active: form.active };
}

/** The first thing missing or wrong, as a sentence, or null when the form can be sent. */
export function typeProblem(form: TypeForm): string | null {
  if (!form.name.trim()) return "Give the booking type a name.";
  const duration = whole(form.duration);
  if (duration === null || duration < 1 || duration > 1440) {
    return "Enter the length as a whole number of minutes, from 1 to 1,440 (24 hours).";
  }
  const limits: [string, string, number][] = [
    ["time to clean up or reset after it", form.buffer, 1440],
    ["shortest notice", form.minNotice, 525600],
    ["furthest ahead it can be booked, in days", form.maxAdvance, 730],
    ["last time to change or cancel before it starts", form.cutoff, 525600],
  ];
  for (const [label, text, max] of limits) {
    const value = whole(text);
    if (value === null || value > max) {
      return `Enter the ${label} as a whole number from 0 to ${max.toLocaleString("en-US")}.`;
    }
  }
  if (form.delivery === "named" && form.personIds.length === 0) return "Choose who takes this booking.";
  if (form.delivery === "several" && form.personIds.length === 0) return "Choose the people who all attend.";
  if (form.resourceRule !== "none" && form.resourceIds.length === 0) {
    return "Choose at least one room or shared thing.";
  }
  if (form.locationKind === "in_person" && !form.address.trim()) return "Enter the address where people meet.";
  if (form.locationKind === "online" && !HTTPS_LINK.test(form.link.trim())) {
    return "Enter the meeting link. It must start with https://.";
  }
  return null;
}

// ---- People, rooms and times ---------------------------------------------------------------

const EMAIL = /^[^\s@<>,;"]+@[^\s@<>,;"]+\.[^\s@<>,;"]+$/;
/** One plain address, as the server accepts it. */
export const isEmail = (text: string) => text.length >= 3 && text.length <= 254 && EMAIL.test(text);

/** Only an Admin is shown a person's email. */
export function emailForViewer(email: string | null, isAdmin: boolean): string | null {
  return isAdmin && email ? email : null;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

function zoneParts(instant: number, zone: string) {
  const found = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: zone, year: "numeric", month: "numeric", day: "numeric",
      hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
    }).formatToParts(new Date(instant)).map((part) => [part.type, Number(part.value)]),
  );
  return found as Record<"year" | "month" | "day" | "hour" | "minute" | "second", number>;
}

/** The zone's offset from UTC, in milliseconds, at that instant. */
function offsetAt(instant: number, zone: string): number {
  const p = zoneParts(instant, zone);
  const wall = Date.UTC(p.year, p.month - 1, p.day, p.hour, p.minute, p.second);
  return wall - Math.floor(instant / 1000) * 1000;
}

/** "2026-11-05" and "09:00" on the company's clock as an aware UTC time; null if either is not valid. */
export function zonedToUtc(day: string, time: string, zone: string): string | null {
  const date = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (!date || !TIME.test(time)) return null;
  const [hour, minute] = time.split(":").map(Number);
  const wall = Date.UTC(Number(date[1]), Number(date[2]) - 1, Number(date[3]), hour, minute);
  try {
    // Two passes settle on the right offset when the guess lands on the other side of a clock change.
    let instant = wall - offsetAt(wall, zone);
    instant = wall - offsetAt(instant, zone);
    return new Date(instant).toISOString();
  } catch {
    return null;
  }
}

/** "5 Nov 2026, 09:00 to 12:00", or both dates when the block spans days. */
export function blockWhen(startsAt: string, endsAt: string, zone: string): string {
  const read = (value: string) => {
    const p = zoneParts(new Date(value).getTime(), zone);
    const pad = (n: number) => String(n).padStart(2, "0");
    return { date: `${p.day} ${MONTHS[p.month - 1]} ${p.year}`, time: `${pad(p.hour)}:${pad(p.minute)}` };
  };
  const start = read(startsAt);
  const end = read(endsAt);
  return start.date === end.date
    ? `${start.date}, ${start.time} to ${end.time}`
    : `${start.date}, ${start.time} to ${end.date}, ${end.time}`;
}

/** "5 Nov 2026" for a date written "2026-11-05". */
export function dayText(day: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  return match ? `${Number(match[3])} ${MONTHS[Number(match[2]) - 1]} ${match[1]}` : day;
}

/** Today's date on the company's calendar as "YYYY-MM-DD"; null when the zone is not known. */
export function todayInZone(zone: string | null, now: Date = new Date()): string | null {
  if (!zone) return null;
  try {
    const p = zoneParts(now.getTime(), zone);
    const pad = (n: number) => String(n).padStart(2, "0");
    return `${p.year}-${pad(p.month)}-${pad(p.day)}`;
  } catch {
    return null;
  }
}
