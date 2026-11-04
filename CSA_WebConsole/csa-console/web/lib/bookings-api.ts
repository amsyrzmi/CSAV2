import { api, del, patch, post } from "./api";

/** Mirrors of the `Booking*` schemas in docs/console_openapi.json (csa/bookings/contracts.py). */
export type BookingAudience = "customers" | "staff" | "both";
export type BookingDelivery = "named" | "any" | "several";
export type BookingResourceRule = "none" | "specific" | "any";
export type BookingLocationKind = "in_person" | "phone" | "online";
export type BookingSubjectKind = "company" | "person" | "resource";

export interface BookingPersonOut {
  id: string;
  display_name: string;
  user_id: string | null;
  employee_id: string | null;
  email: string | null;
  is_active: boolean;
  version: number;
}

export interface BookingPersonIn {
  display_name: string;
  email?: string | null;
}

/** A field sent as `null` is cleared; one left out is kept. */
export interface BookingPersonPatch {
  version: number;
  display_name?: string | null;
  email?: string | null;
  is_active?: boolean | null;
}

export interface BookingResourceOut {
  id: string;
  name: string;
  capacity: number | null;
  is_active: boolean;
  version: number;
}

export interface BookingResourceIn {
  name: string;
  capacity?: number | null;
}

export interface BookingResourcePatch {
  version: number;
  name?: string | null;
  capacity?: number | null;
  is_active?: boolean | null;
}

export interface BookingTypeOut {
  id: string;
  name: string;
  duration_minutes: number;
  buffer_minutes: number;
  audience: BookingAudience;
  delivery: BookingDelivery;
  resource_rule: BookingResourceRule;
  location_kind: BookingLocationKind;
  address: string | null;
  meeting_link: string | null;
  needs_confirmation: boolean;
  min_notice_minutes: number;
  max_advance_days: number;
  change_cutoff_minutes: number;
  is_active: boolean;
  version: number;
  person_ids: string[];
  resource_ids: string[];
}

export interface BookingTypeIn {
  name: string;
  duration_minutes: number;
  location_kind: BookingLocationKind;
  buffer_minutes?: number;
  audience?: BookingAudience;
  delivery?: BookingDelivery;
  resource_rule?: BookingResourceRule;
  address?: string | null;
  meeting_link?: string | null;
  needs_confirmation?: boolean;
  min_notice_minutes?: number;
  max_advance_days?: number;
  change_cutoff_minutes?: number;
  person_ids?: string[];
  resource_ids?: string[];
}

export interface BookingTypePatch extends Partial<Omit<BookingTypeIn, "name">> {
  version: number;
  name?: string | null;
  is_active?: boolean | null;
}

/** `weekday` 0 is Monday; times are "HH:MM" in the company's time zone. */
export interface BookingPeriod {
  weekday: number;
  opens: string;
  closes: string;
}

/**
 * `is_default` means two things: for the company, "this is the default week, nothing was set";
 * for a person or room, "nothing stored, it follows the company" (periods is then empty). Read
 * it with `bookings-view.hoursLabel`, never on its own.
 */
export interface BookingHoursOut {
  subject_kind: BookingSubjectKind;
  subject_id: string | null;
  is_default: boolean;
  periods: BookingPeriod[];
}

export interface BookingHoursIn {
  subject_kind?: BookingSubjectKind;
  subject_id?: string | null;
  periods: BookingPeriod[];
}

export interface BookingClosedDayOut {
  day: string;
  note: string | null;
}

export interface BookingBlockOut {
  id: string;
  subject_kind: "person" | "resource";
  subject_id: string;
  starts_at: string;
  ends_at: string;
  note: string | null;
  created_by: string;
}

export interface BookingBlockIn {
  subject_kind: "person" | "resource";
  subject_id: string;
  starts_at: string;
  ends_at: string;
  note?: string | null;
}

const BASE = "/console/v1/bookings";

/**
 * `tenantId` is null for a tenant member (the session names the tenant) and the chosen
 * company for a platform admin, who must always send `client_id`. Empty values are left out.
 */
export function bookingsPath(
  tenantId: string | null,
  rest = "",
  params: Record<string, string | null | undefined> = {},
): string {
  const query = new URLSearchParams();
  if (tenantId) query.set("client_id", tenantId);
  for (const [key, value] of Object.entries(params)) if (value) query.set(key, value);
  return `${BASE}${rest}${query.size ? `?${query}` : ""}`;
}

const id = (value: string) => `/${encodeURIComponent(value)}`;

export function listPeople(tenantId: string | null): Promise<BookingPersonOut[]> {
  return api(bookingsPath(tenantId, "/people"));
}

export function addPerson(tenantId: string | null, body: BookingPersonIn): Promise<BookingPersonOut> {
  return post(bookingsPath(tenantId, "/people"), body);
}

export function changePerson(
  tenantId: string | null,
  personId: string,
  body: BookingPersonPatch,
): Promise<BookingPersonOut> {
  return patch(bookingsPath(tenantId, `/people${id(personId)}`), body);
}

/** Adds each enabled team member not yet listed; resolves to the people added ([] the second time). */
export function addPeopleFromTeam(tenantId: string | null): Promise<BookingPersonOut[]> {
  return post(bookingsPath(tenantId, "/people/from-team"));
}

export function listResources(tenantId: string | null): Promise<BookingResourceOut[]> {
  return api(bookingsPath(tenantId, "/resources"));
}

export function addResource(tenantId: string | null, body: BookingResourceIn): Promise<BookingResourceOut> {
  return post(bookingsPath(tenantId, "/resources"), body);
}

export function changeResource(
  tenantId: string | null,
  resourceId: string,
  body: BookingResourcePatch,
): Promise<BookingResourceOut> {
  return patch(bookingsPath(tenantId, `/resources${id(resourceId)}`), body);
}

export function listTypes(tenantId: string | null): Promise<BookingTypeOut[]> {
  return api(bookingsPath(tenantId, "/types"));
}

export function addType(tenantId: string | null, body: BookingTypeIn): Promise<BookingTypeOut> {
  return post(bookingsPath(tenantId, "/types"), body);
}

export function changeType(
  tenantId: string | null,
  typeId: string,
  body: BookingTypePatch,
): Promise<BookingTypeOut> {
  return patch(bookingsPath(tenantId, `/types${id(typeId)}`), body);
}

/** Creates "Appointment" and "Consultation" when the company has no type; else returns the existing ones. */
export function createStarterTypes(tenantId: string | null): Promise<BookingTypeOut[]> {
  return post(bookingsPath(tenantId, "/types/starters"));
}

/** The company's hours without a subject; a person's or room's own hours with one. */
export function getHours(
  tenantId: string | null,
  subject?: { kind: "person" | "resource"; id: string },
): Promise<BookingHoursOut> {
  return api(bookingsPath(tenantId, "/hours", { subject_kind: subject?.kind, subject_id: subject?.id }));
}

/** Replaces those hours as a whole; an empty list clears them. */
export function replaceHours(tenantId: string | null, body: BookingHoursIn): Promise<BookingHoursOut> {
  return api(bookingsPath(tenantId, "/hours"), { method: "PUT", body: JSON.stringify(body) });
}

export function listClosedDays(
  tenantId: string | null,
  range: { from?: string; to?: string } = {},
): Promise<BookingClosedDayOut[]> {
  return api(bookingsPath(tenantId, "/closed-days", range));
}

/** Closing a day that is already closed changes its note. */
export function addClosedDay(
  tenantId: string | null,
  body: { day: string; note?: string | null },
): Promise<BookingClosedDayOut> {
  return post(bookingsPath(tenantId, "/closed-days"), body);
}

export function removeClosedDay(tenantId: string | null, day: string): Promise<void> {
  return del(bookingsPath(tenantId, `/closed-days${id(day)}`));
}

/** Blocks ending after `from` (default now) and starting before `to`. */
export function listBlocks(
  tenantId: string | null,
  range: { from?: string; to?: string } = {},
): Promise<BookingBlockOut[]> {
  return api(bookingsPath(tenantId, "/blocks", range));
}

export function addBlock(tenantId: string | null, body: BookingBlockIn): Promise<BookingBlockOut> {
  return post(bookingsPath(tenantId, "/blocks"), body);
}

export function removeBlock(tenantId: string | null, blockId: string): Promise<void> {
  return del(bookingsPath(tenantId, `/blocks${id(blockId)}`));
}

// ---- Bookings, the calendar and customers ---------------------------------------------------

/** The findings a booking or a move can come back with, as they are on the wire. */
export type BookingFinding = "outside_hours" | "closed_day" | "short_notice" | "unavailable";
export type BookingStatus = "pending" | "confirmed" | "cancelled" | "no_show";
/** How the mail went, on the answer to a staff action only: null on every read. */
export type BookingMail = "sent" | "off" | "failed" | "none";

export interface BookingPersonRef {
  id: string;
  display_name: string;
  is_active: boolean;
}

export interface BookingResourceRef {
  id: string;
  name: string;
  is_active: boolean;
}

export interface BookingCustomer {
  id: string;
  full_name: string | null;
  phone: string | null;
  email: string | null;
}

export interface BookingGuest {
  name: string | null;
  email: string;
}

/**
 * A redacted private meeting has `redacted: true`, `title: "Busy"`, no people, rooms or details;
 * a read-only member gets `customer: null` and no guests.
 */
export interface BookingOut {
  id: string;
  status: BookingStatus;
  kind: "appointment" | "meeting";
  title: string | null;
  private: boolean;
  redacted: boolean;
  starts_at: string;
  ends_at: string;
  version: number;
  type_id: string | null;
  customer: BookingCustomer | null;
  people: BookingPersonRef[];
  resources: BookingResourceRef[];
  guests: BookingGuest[];
  location_kind: BookingLocationKind | null;
  address: string | null;
  meeting_link: string | null;
  /** "unavailable" when a person on it now has time off over it; never says why. */
  conflict: "unavailable" | null;
  email?: BookingMail | null;
}

/** A private meeting the viewer is not part of, on a filtered read: times only, and no id. */
export interface BookingBusy {
  starts_at: string;
  ends_at: string;
  label: "Busy";
}

export interface BookingCalendar {
  items: BookingOut[];
  busy: BookingBusy[];
}

export interface BookingSlot {
  starts_at: string;
  ends_at: string;
  people: string[];
  resources: string[];
}

export interface BookingIn {
  starts_at: string;
  type_id?: string | null;
  title?: string | null;
  minutes?: number | null;
  people?: string[];
  resources?: string[];
  contact_id?: string | null;
  walk_in?: { name: string; phone?: string | null; email?: string | null } | null;
  guests?: { name?: string | null; email: string }[];
  private?: boolean;
  location_kind?: BookingLocationKind | null;
  address?: string | null;
  meeting_link?: string | null;
  acknowledged?: BookingFinding[];
}

export interface BookingMoveIn {
  version: number;
  starts_at: string;
  acknowledged?: BookingFinding[];
}

export type BookingAction = "cancel" | "confirm" | "decline" | "no-show";

export function getCalendar(
  tenantId: string | null,
  range: { from: string; to: string },
  filter: { person?: string; resource?: string } = {},
  signal?: AbortSignal,
): Promise<BookingCalendar> {
  return api(
    bookingsPath(tenantId, "/calendar", { ...range, person: filter.person, resource: filter.resource }),
    { signal },
  );
}

/** Free times for a type, or (no type) for the people and rooms given and `minutes`. */
export function getAvailability(
  tenantId: string | null,
  query: { from: string; to: string; typeId?: string; people?: string[]; resources?: string[]; minutes?: number },
  signal?: AbortSignal,
): Promise<BookingSlot[]> {
  const search = new URLSearchParams();
  if (tenantId) search.set("client_id", tenantId);
  search.set("from", query.from);
  search.set("to", query.to);
  if (query.typeId) search.set("type_id", query.typeId);
  if (query.minutes) search.set("minutes", String(query.minutes));
  for (const person of query.people ?? []) search.append("people", person);
  for (const resource of query.resources ?? []) search.append("resources", resource);
  return api(`${BASE}/availability?${search}`, { signal });
}

/** Read-only members get 403 here, as in the Inbox. */
export function searchCustomers(
  tenantId: string | null,
  q: string,
  signal?: AbortSignal,
): Promise<BookingCustomer[]> {
  return api(bookingsPath(tenantId, "/customers", { q: q.trim() || undefined }), { signal });
}

export function createBooking(tenantId: string | null, body: BookingIn): Promise<BookingOut> {
  return post(bookingsPath(tenantId, "/bookings"), body);
}

export function moveBooking(
  tenantId: string | null,
  bookingId: string,
  body: BookingMoveIn,
): Promise<BookingOut> {
  return patch(bookingsPath(tenantId, `/bookings${id(bookingId)}`), body);
}

export function changeBooking(
  tenantId: string | null,
  bookingId: string,
  action: BookingAction,
  version: number,
): Promise<BookingOut> {
  return post(bookingsPath(tenantId, `/bookings${id(bookingId)}/${action}`), { version });
}
