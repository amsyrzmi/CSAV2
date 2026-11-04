/**
 * Pure rules for the Bookings setup pages: how hours read, the seven-day hours editor, a booking
 * type's form (and its round trip to the API body), plain labels, and wall-clock times in the
 * company's time zone. Only type imports, so the node tests load this file on its own.
 */
import type {
  BookingAudience,
  BookingDelivery,
  BookingFinding,
  BookingIn,
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

const pad2 = (n: number) => String(n).padStart(2, "0");

/** The date on the company's calendar of an instant, as "YYYY-MM-DD". Throws for an unknown zone. */
export function dayInZone(instant: string | Date, zone: string): string {
  const p = zoneParts(new Date(instant).getTime(), zone);
  return `${p.year}-${pad2(p.month)}-${pad2(p.day)}`;
}

/** The time on the company's clock of an instant, as "HH:MM". */
export function timeInZone(instant: string | Date, zone: string): string {
  const p = zoneParts(new Date(instant).getTime(), zone);
  return `${pad2(p.hour)}:${pad2(p.minute)}`;
}

/** A calendar date moved by whole days ("2026-12-31" plus one is "2027-01-01"). */
export function addDays(day: string, days: number): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (!match) return day;
  const moved = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]) + days));
  return `${moved.getUTCFullYear()}-${pad2(moved.getUTCMonth() + 1)}-${pad2(moved.getUTCDate())}`;
}

/** 0 is Monday, for a date written "YYYY-MM-DD". */
function weekdayOf(day: string): number {
  const [year, month, date] = day.split("-").map(Number);
  return (new Date(Date.UTC(year, month - 1, date)).getUTCDay() + 6) % 7;
}

export interface WeekDay {
  day: string;
  /** 0 is Monday. */
  weekday: number;
  label: string;
  /** This day's midnight and the next midnight on the company's clock, as UTC instants. */
  start: string;
  end: string;
}

function midnight(day: string, zone: string): string {
  const instant = zonedToUtc(day, "00:00", zone);
  if (!instant) throw new RangeError(`Cannot read ${day} in ${zone}`);
  return instant;
}

/** One calendar day on the company's clock as the UTC window the server reads. */
export function dayWindow(day: string, timezone: string): { from: string; to: string } | null {
  const from = zonedToUtc(day, "00:00", timezone);
  const to = zonedToUtc(addDays(day, 1), "00:00", timezone);
  return from && to ? { from, to } : null;
}

/** The Monday-to-Sunday week that holds `anchor` on the company's calendar, never the browser's. */
export function weekDays(anchor: Date, timezone: string): WeekDay[] {
  const today = dayInZone(anchor, timezone);
  const monday = addDays(today, -weekdayOf(today));
  return Array.from({ length: 7 }, (_, weekday) => {
    const day = addDays(monday, weekday);
    const parts = day.split("-").map(Number);
    return {
      day,
      weekday,
      label: `${WEEKDAYS[weekday].slice(0, 3)} ${parts[2]} ${MONTHS[parts[1] - 1]}`,
      start: midnight(day, timezone),
      end: midnight(addDays(day, 1), timezone),
    };
  });
}

/** An instant in the middle of the same weekday `weeks` weeks away, on the company's calendar. */
export function shiftWeek(anchor: Date, weeks: number, timezone: string): Date {
  const day = addDays(dayInZone(anchor, timezone), weeks * 7);
  return new Date(zonedToUtc(day, "12:00", timezone) ?? anchor);
}

/** "9 Nov to 15 Nov 2026", with both years when the week crosses New Year. */
export function weekTitle(days: WeekDay[]): string {
  const first = days[0].day;
  const last = days[days.length - 1].day;
  const from = dayText(first);
  return `${first.slice(0, 4) === last.slice(0, 4) ? from.replace(/ \d{4}$/, "") : from} to ${dayText(last)}`;
}

export interface Placed<T> {
  item: T;
  /** Minutes after midnight on the company's clock; the end stops at midnight. */
  startMinute: number;
  endMinute: number;
  /** Column among those that overlap, and how many columns that group needs. */
  lane: number;
  lanes: number;
}

/** The shortest a booking is drawn, so a short one can still be read and pressed. */
const MIN_DRAWN = 30;

/**
 * Places one day's bookings on a 24-hour grid. Bookings that overlap in time (different people
 * or rooms) sit in side-by-side columns; one that overlaps nothing takes the full width.
 */
export function layoutDay<T extends { starts_at: string; ends_at: string }>(
  items: T[],
  timezone: string,
): Placed<T>[] {
  const minutes = items.map((item) => {
    const starts = zoneParts(new Date(item.starts_at).getTime(), timezone);
    const ends = zoneParts(new Date(item.ends_at).getTime(), timezone);
    const startMinute = starts.hour * 60 + starts.minute;
    const sameDay = starts.year === ends.year && starts.month === ends.month && starts.day === ends.day;
    const endMinute = sameDay ? ends.hour * 60 + ends.minute : 1440;
    return { item, startMinute, endMinute: Math.min(1440, Math.max(endMinute, startMinute + MIN_DRAWN)) };
  });
  minutes.sort((a, b) => a.startMinute - b.startMinute || b.endMinute - a.endMinute);
  const placed: Placed<T>[] = [];
  let cluster: Placed<T>[] = [];
  let laneEnds: number[] = [];
  let clusterEnd = -1;
  const close = () => {
    for (const entry of cluster) entry.lanes = laneEnds.length;
    cluster = [];
    laneEnds = [];
  };
  for (const entry of minutes) {
    if (entry.startMinute >= clusterEnd) close();
    let lane = laneEnds.findIndex((end) => end <= entry.startMinute);
    if (lane === -1) lane = laneEnds.length;
    laneEnds[lane] = entry.endMinute;
    clusterEnd = cluster.length ? Math.max(clusterEnd, entry.endMinute) : entry.endMinute;
    const next = { ...entry, lane, lanes: 1 };
    cluster.push(next);
    placed.push(next);
  }
  close();
  return placed;
}

const FINDINGS: Record<string, string> = {
  outside_hours: "Outside working hours.",
  closed_day: "This is a closed day.",
  unavailable: "Not available that day.",
  short_notice: "Shorter notice than this booking type allows.",
};

/** The sentence for one finding the server returned. Never says why a person is away. */
export function findingCopy(finding: string): string {
  return FINDINGS[finding] ?? "This time needs a second look.";
}

const MAIL_NOTES: Record<string, string> = {
  off: "Mail is off, so nobody was emailed.",
  failed: "We couldn't email everyone. Some may not have got it.",
  none: "Nobody on this booking has an email address.",
};

/** What to tell staff after a change when the mail did not all go; null when it did or is unknown. */
export function emailNote(status: string | null | undefined): string | null {
  return (status && MAIL_NOTES[status]) || null;
}

/** What a booking is called on the calendar. A private meeting the viewer is not part of is "Busy". */
export function bookingTitle(booking: { redacted: boolean; title: string | null; kind: string }): string {
  if (booking.redacted) return "Busy";
  return booking.title?.trim() || (booking.kind === "meeting" ? "Meeting" : "Appointment");
}

export const mapsLink = (address: string) =>
  `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(address)}`;
export const wazeLink = (address: string) => `https://waze.com/ul?q=${encodeURIComponent(address)}&navigate=yes`;

/** A meeting link is shown as a link only when it begins with https://. */
export function safeMeetingLink(link: string | null | undefined): string | null {
  return link && HTTPS_LINK.test(link) ? link : null;
}

/**
 * Whether to offer Move and Cancel. An Admin may change any booking, including a redacted private
 * meeting. Staff may change what they are on, or made in this session: the API names no creator,
 * so the server has the final say (403) for the rest. Read-only members change nothing.
 */
export function canChangeBooking(
  booking: { id: string; people: { id: string }[]; redacted: boolean; status: string },
  who: { admin: boolean; staff: boolean; myPersonIds: string[]; mine: string[] },
): boolean {
  if (booking.status !== "pending" && booking.status !== "confirmed") return false;
  if (who.admin) return true;
  if (!who.staff || booking.redacted) return false;
  return booking.people.some((person) => who.myPersonIds.includes(person.id)) || who.mine.includes(booking.id);
}

const PHONE = /^\+?[0-9][0-9 ()\-.]*$/;
/** A phone number as the server takes it: digits with spaces, brackets, dots and dashes. */
export const isPhone = (text: string) => text.length >= 3 && text.length <= 30 && PHONE.test(text);

/** The booking form. Everything is text while it is typed; `typeId` empty means a meeting. */
export interface BookingForm {
  typeId: string;
  title: string;
  minutes: string;
  day: string;
  time: string;
  personIds: string[];
  resourceIds: string[];
  isPrivate: boolean;
  locationKind: BookingLocationKind;
  address: string;
  link: string;
  contactId: string;
  walkName: string;
  walkPhone: string;
  walkEmail: string;
  guests: { name: string; email: string }[];
}

export function emptyBookingForm(day: string): BookingForm {
  return {
    typeId: "", title: "", minutes: "30", day, time: "", personIds: [], resourceIds: [],
    isPrivate: false, locationKind: "phone", address: "", link: "", contactId: "", walkName: "",
    walkPhone: "", walkEmail: "", guests: [],
  };
}

type BookingTypeRules = Pick<BookingTypeOut, "audience" | "delivery">;

/** Whether the form asks for a customer: a booking of a type that is not for staff only. */
export const takesCustomer = (type: BookingTypeRules | null) => type !== null && type.audience !== "staff";

const trimmed = (text: string) => text.trim() || null;

/** The first thing missing or wrong, as a sentence, or null when the form can be sent. */
export function bookingProblem(form: BookingForm, type: BookingTypeRules | null, zone: string): string | null {
  if (type) {
    if (type.delivery === "named" && form.personIds.length === 0) return "Choose who this booking is with.";
    if (takesCustomer(type)) {
      const name = form.walkName.trim();
      const phone = form.walkPhone.trim();
      const email = form.walkEmail.trim();
      const started = Boolean(name || phone || email);
      if (!form.contactId && !started && type.audience === "customers") {
        return "Choose a customer, or enter a new one with a phone number or an email.";
      }
      if (!form.contactId && started) {
        if (!name) return "Enter the customer's name.";
        if (!phone && !email) return "Enter the customer's phone number or email.";
        if (phone && !isPhone(phone)) return "Check the customer's phone number.";
        if (email && !isEmail(email)) return "Check the customer's email address.";
      }
    }
  } else {
    if (!form.title.trim()) return "Give the meeting a title.";
    const minutes = whole(form.minutes);
    if (minutes === null || minutes < 1 || minutes > 1440) {
      return "Enter the length as a whole number of minutes, from 1 to 1,440 (24 hours).";
    }
    if (form.personIds.length === 0 && form.resourceIds.length === 0) {
      return "Choose at least one colleague or room.";
    }
    if (form.locationKind === "online" && form.link.trim() && !HTTPS_LINK.test(form.link.trim())) {
      return "The meeting link must start with https://.";
    }
  }
  if (!zonedToUtc(form.day, form.time, zone)) return "Choose the day and time.";
  for (const guest of form.guests) {
    if (guest.email.trim() && !isEmail(guest.email.trim())) {
      return `Check the guest's email address: ${guest.email.trim()}.`;
    }
  }
  return null;
}

/** The request body. Call it on a form with no problem; it sends only what the chosen rules use. */
export function formToBookingIn(
  form: BookingForm,
  type: BookingTypeRules & { id: string } | null,
  zone: string,
  acknowledged: BookingFinding[] = [],
): BookingIn {
  const body: BookingIn = {
    starts_at: zonedToUtc(form.day, form.time, zone) ?? "",
    people: type ? (type.delivery === "named" ? form.personIds : []) : form.personIds,
    resources: type ? [] : form.resourceIds,
    guests: form.guests
      .filter((guest) => guest.email.trim())
      .map((guest) => ({ name: trimmed(guest.name), email: guest.email.trim() })),
    acknowledged,
  };
  if (type) {
    body.type_id = type.id;
    if (takesCustomer(type)) {
      if (form.contactId) body.contact_id = form.contactId;
      else if (form.walkName.trim()) {
        body.walk_in = {
          name: form.walkName.trim(),
          phone: trimmed(form.walkPhone),
          email: trimmed(form.walkEmail),
        };
      }
    }
    return body;
  }
  body.title = form.title.trim();
  body.minutes = Number(form.minutes);
  body.private = form.isPrivate;
  body.location_kind = form.locationKind;
  if (form.locationKind === "in_person" && form.address.trim()) body.address = form.address.trim();
  if (form.locationKind === "online" && form.link.trim()) body.meeting_link = form.link.trim();
  return body;
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
