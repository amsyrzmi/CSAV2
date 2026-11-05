"use client";

import { ChevronLeft, ChevronRight, Clock, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import {
  getCalendar,
  listPeople,
  listResources,
  listTypes,
  type BookingBusy,
  type BookingCalendar,
  type BookingOut,
} from "@/lib/bookings-api";
import {
  bookingTitle,
  canChangeBooking,
  changeMarks,
  customerNote,
  dayInZone,
  emailNote,
  entriesOnDay,
  inactiveNotes,
  layoutDay,
  shiftWeek,
  sourceNote,
  timeInZone,
  weekDays,
  weekTitle,
  type ChangeMarks,
  type Placed,
  type WeekDay,
} from "@/lib/bookings-view";
import { action, Notice, secondary } from "../ui";
import BookingDetails, { type Change } from "./BookingDetails";
import BookingForm from "./BookingForm";
import { errorText, input, mutedText, useLoaded, ZoneGate, type BookingsScope } from "./parts";

const WIDE = "(min-width: 768px)";
/** Height of one hour in the week view, and the hours always shown. */
const HOUR_PX = 48;
const DEFAULT_FROM = 8;
const DEFAULT_TO = 18;

const SAID: Record<Change | "created", string> = {
  created: "Booked.",
  moved: "Moved.",
  cancelled: "Cancelled.",
  confirmed: "Confirmed.",
  declined: "Declined.",
  no_show: "Marked as a no-show.",
};

/** One calendar row: a booking, or a bare busy time (which has no id and cannot be opened). */
type Entry = BookingOut | BookingBusy;
const isBooking = (entry: Entry): entry is BookingOut => "id" in entry;

function useWide(): boolean {
  return useSyncExternalStore(
    (notify) => {
      const media = window.matchMedia(WIDE);
      media.addEventListener("change", notify);
      return () => media.removeEventListener("change", notify);
    },
    () => window.matchMedia(WIDE).matches,
    () => false,
  );
}

const blockTone = (entry: Entry) =>
  !isBooking(entry) || entry.redacted
    ? "border-[color:var(--fs-border)] bg-[var(--fs-neutral-bg)] text-[color:var(--fs-neutral-text)]"
    : entry.status === "pending"
      ? "border-dashed border-[color:var(--fs-primary)] bg-[var(--fs-surface)] text-[color:var(--fs-text)]"
      : "border-[color:var(--fs-primary)] bg-[var(--fs-surface-blue)] text-[color:var(--fs-text)]";

/** What a booking says about itself on the calendar; a redacted one says only "Busy". */
function describe(entry: Entry, zone: string, marks: ChangeMarks): string {
  const when = `${timeInZone(entry.starts_at, zone)} to ${timeInZone(entry.ends_at, zone)}`;
  if (!isBooking(entry)) return `${when}, Busy`;
  const parts = [when, bookingTitle(entry)];
  if (!entry.redacted) {
    if (entry.customer?.full_name) parts.push(entry.customer.full_name);
    if (entry.people.length) parts.push(entry.people.map((p) => p.display_name).join(", "));
    if (entry.status === "pending") parts.push("waiting to be confirmed");
    const asked = marks.asked.get(entry.id);
    if (asked) parts.push(asked);
    if (marks.waiting.has(entry.id)) parts.push(WAITING.toLowerCase());
    if (entry.conflict) parts.push("someone is unavailable");
    parts.push(...inactiveNotes(entry));
  }
  return parts.join(", ");
}

const CONFLICT = "Someone on this booking is unavailable";
const WAITING = "A change is waiting";

/**
 * Compact flags for the first line of a block; each one's name carries its sentence. One is for a
 * person with time off over it, one for someone or somewhere switched off since it was made, and
 * one for a customer's request to move it that staff have not answered.
 */
function Flag({ entry, marks }: { entry: Entry; marks: ChangeMarks }) {
  if (!isBooking(entry)) return null;
  const off = inactiveNotes(entry).join(" ");
  return (
    <>
      {marks.waiting.has(entry.id) && (
        <span data-testid="booking-change-waiting" role="img" aria-label={WAITING} title={WAITING}
          className="inline-flex shrink-0 text-[color:var(--fs-primary)]">
          <Clock aria-hidden="true" size={12} />
        </span>
      )}
      {entry.conflict && (
        <span data-testid="booking-conflict" role="img" aria-label={CONFLICT} title={CONFLICT}
          className="inline-flex shrink-0 text-[color:var(--fs-warning)]">
          <TriangleAlert aria-hidden="true" size={12} />
        </span>
      )}
      {off && (
        <span data-testid="booking-inactive" role="img" aria-label={off} title={off}
          className="inline-flex shrink-0 text-[color:var(--fs-warning)]">
          <TriangleAlert aria-hidden="true" size={12} />
        </span>
      )}
    </>
  );
}

function WeekGrid({
  days,
  zone,
  entries,
  today,
  marks,
  onOpen,
}: {
  days: WeekDay[];
  zone: string;
  entries: Entry[];
  today: string;
  marks: ChangeMarks;
  onOpen: (booking: BookingOut) => void;
}) {
  const placed = days.map((day) => layoutDay(entriesOnDay(entries, day), zone, day));
  const all = placed.flat();
  const first = Math.min(DEFAULT_FROM, ...all.map((p) => Math.floor(p.startMinute / 60)));
  const last = Math.max(DEFAULT_TO, ...all.map((p) => Math.ceil(p.endMinute / 60)));
  const hours = Array.from({ length: last - first }, (_, index) => first + index);
  const body = (last - first) * HOUR_PX;
  return (
    <div data-testid="calendar-week" className="grid grid-cols-[2.75rem_repeat(7,minmax(0,1fr))] text-xs">
      <div />
      {days.map((day) => (
        <div key={day.day} data-testid="calendar-day-heading" aria-current={day.day === today ? "date" : undefined}
          className={`border-b border-[color:var(--fs-border)] px-1 py-2 text-center ${day.day === today ? "font-semibold text-[color:var(--fs-primary)]" : ""}`}>
          {day.label}
          {day.day === today && <span className="sr-only"> (today)</span>}
        </div>
      ))}
      <div className="relative" style={{ height: body }} aria-hidden="true">
        {hours.map((hour) => (
          <span key={hour} className="absolute right-1 -translate-y-1/2 text-[color:var(--fs-text-secondary)]"
            style={{ top: (hour - first) * HOUR_PX }}>
            {String(hour).padStart(2, "0")}:00
          </span>
        ))}
      </div>
      {days.map((day, index) => (
        <div key={day.day} data-testid="calendar-day" data-day={day.day}
          className="relative min-w-0 border-l border-[color:var(--fs-border)]" style={{ height: body }}>
          {hours.map((hour) => (
            <div key={hour} className="absolute inset-x-0 border-t border-[color:var(--fs-border)]"
              style={{ top: (hour - first) * HOUR_PX }} />
          ))}
          {placed[index].map((slot) => (
            <Block key={slotKey(slot)} slot={slot} zone={zone} first={first} marks={marks} onOpen={onOpen} />
          ))}
        </div>
      ))}
    </div>
  );
}

const slotKey = (slot: Placed<Entry>) =>
  isBooking(slot.item) ? slot.item.id : `busy-${slot.item.starts_at}-${slot.item.ends_at}-${slot.lane}`;

function Block({
  slot,
  zone,
  first,
  marks,
  onOpen,
}: {
  slot: Placed<Entry>;
  zone: string;
  first: number;
  marks: ChangeMarks;
  onOpen: (booking: BookingOut) => void;
}) {
  const { item } = slot;
  const style = {
    top: ((slot.startMinute - first * 60) / 60) * HOUR_PX,
    height: ((slot.endMinute - slot.startMinute) / 60) * HOUR_PX - 2,
    left: `${(slot.lane / slot.lanes) * 100}%`,
    width: `calc(${100 / slot.lanes}% - 2px)`,
  };
  const text = (
    <>
      <span className="flex items-center gap-1 font-medium">
        <Flag entry={item} marks={marks} />
        <span className="min-w-0 truncate">
          {timeInZone(item.starts_at, zone)}-{timeInZone(item.ends_at, zone)}
        </span>
      </span>
      <span className="block truncate">{isBooking(item) ? bookingTitle(item) : "Busy"}</span>
    </>
  );
  const shared = `absolute overflow-hidden rounded-[var(--fs-radius-control)] border px-1 py-0.5 text-left leading-tight ${blockTone(item)}`;
  return isBooking(item) ? (
    <button type="button" data-testid={item.redacted ? "booking-busy" : "booking"} style={style}
      aria-label={describe(item, zone, marks)} title={describe(item, zone, marks)} onClick={() => onOpen(item)}
      className={`${shared} hover:border-[color:var(--fs-primary)] focus-visible:outline-3 focus-visible:outline-offset-1 focus-visible:outline-[color:var(--fs-focus)]`}>
      {text}
    </button>
  ) : (
    <div data-testid="busy" style={style} title={describe(item, zone, marks)} className={shared}>
      {text}
    </div>
  );
}

function Agenda({
  days,
  zone,
  entries,
  marks,
  onOpen,
}: {
  days: WeekDay[];
  zone: string;
  entries: Entry[];
  marks: ChangeMarks;
  onOpen: (booking: BookingOut) => void;
}) {
  const busyDays = days.filter((day) => entriesOnDay(entries, day).length > 0);
  if (busyDays.length === 0) {
    return <p data-testid="calendar-empty" className={mutedText}>Nothing is booked this week.</p>;
  }
  return (
    <div data-testid="calendar-agenda" className="space-y-4">
      {busyDays.map((day) => (
        <section key={day.day} aria-label={day.label}>
          <h3 className="mb-1 text-sm font-semibold">{day.label}</h3>
          <ul className="space-y-2">
            {layoutDay(entriesOnDay(entries, day), zone, day).map((slot) => {
              const item = slot.item;
              const inner = (
                <>
                  <span className="block font-medium">
                    {timeInZone(item.starts_at, zone)} to {timeInZone(item.ends_at, zone)}
                  </span>
                  <span className="block [overflow-wrap:anywhere]">
                    {isBooking(item) ? bookingTitle(item) : "Busy"}
                    {isBooking(item) && !item.redacted && item.customer?.full_name ? `, ${item.customer.full_name}` : ""}
                  </span>
                  {isBooking(item) && !item.redacted && item.people.length > 0 && (
                    <span className={`block ${mutedText}`}>{item.people.map((p) => p.display_name).join(", ")}</span>
                  )}
                  {slot.continuesBefore && <span className={`block ${mutedText}`}>Continues from the day before</span>}
                  {slot.continuesAfter && <span className={`block ${mutedText}`}>Continues the next day</span>}
                  {isBooking(item) && item.status === "pending" && !item.redacted && (
                    <span className={`block ${mutedText}`}>Waiting to be confirmed</span>
                  )}
                  {isBooking(item) && marks.asked.get(item.id) && (
                    <span data-testid="booking-asked" className="block text-sm font-medium">
                      {marks.asked.get(item.id)}
                    </span>
                  )}
                  {isBooking(item) && marks.waiting.has(item.id) && (
                    <span data-testid="booking-change-waiting" className={`block ${mutedText}`}>{WAITING}</span>
                  )}
                  {isBooking(item) && sourceNote(item) && (
                    <span className={`block ${mutedText}`}>{sourceNote(item)}</span>
                  )}
                  {isBooking(item) && item.conflict && (
                    <span data-testid="booking-conflict" className="mt-1 inline-flex items-center gap-1 font-medium">
                      <TriangleAlert aria-hidden="true" size={14} className="text-[color:var(--fs-warning)]" />
                      {CONFLICT}
                    </span>
                  )}
                  {isBooking(item) && inactiveNotes(item).map((note) => (
                    <span key={note} data-testid="booking-inactive" className="mt-1 flex items-center gap-1 font-medium">
                      <TriangleAlert aria-hidden="true" size={14} className="shrink-0 text-[color:var(--fs-warning)]" />
                      {note}
                    </span>
                  ))}
                </>
              );
              const box = `block w-full rounded-[var(--fs-radius-panel)] border p-3 text-left text-sm ${blockTone(item)}`;
              return (
                <li key={slotKey(slot)}>
                  {isBooking(item) ? (
                    <button type="button" data-testid={item.redacted ? "booking-busy" : "booking"} className={box}
                      aria-label={describe(item, zone, marks)} onClick={() => onOpen(item)}>
                      {inner}
                    </button>
                  ) : (
                    <div data-testid="busy" className={box}>{inner}</div>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}

/**
 * The calendar: a week on the company's clock (a grid from 768 px up, an agenda list below), a
 * filter for one person or room, and the buttons to book, open, move and cancel. Reads need
 * the company's time zone, so nothing is drawn until it is known.
 */
export default function BookingsCalendar({ scope }: { scope: BookingsScope }) {
  return (
    <ZoneGate scope={scope}>{(zone) => <Calendar scope={scope} zone={zone} />}</ZoneGate>
  );
}

function Calendar({ scope, zone }: { scope: BookingsScope; zone: string }) {
  const wide = useWide();
  const [anchor, setAnchor] = useState(() => new Date());
  const [filter, setFilter] = useState("");
  const [round, setRound] = useState(0);
  const [creating, setCreating] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const people = useLoaded(() => listPeople(scope.tenantId));
  const rooms = useLoaded(() => listResources(scope.tenantId));
  const types = useLoaded(() => listTypes(scope.tenantId));

  const days = useMemo(() => weekDays(anchor, zone), [anchor, zone]);
  const from = days[0].start;
  const to = days[6].end;
  const key = `${from}|${to}|${filter}|${round}`;
  const [read, setRead] = useState<{ key: string; data?: BookingCalendar; error?: string } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const [kind, id] = filter.split(":");
    getCalendar(
      scope.tenantId,
      { from, to },
      { person: kind === "person" ? id : undefined, resource: kind === "resource" ? id : undefined },
      controller.signal,
    ).then(
      (data) => setRead({ key, data }),
      (cause: unknown) => { if (!controller.signal.aborted) setRead({ key, error: errorText(cause) }); },
    );
    return () => controller.abort();
  }, [key, from, to, filter, scope.tenantId]);

  const loading = read?.key !== key;
  const shown = read?.data;
  const today = dayInZone(new Date(), zone);
  const entries = useMemo<Entry[]>(() => [...(shown?.items ?? []), ...(shown?.busy ?? [])], [shown]);

  const opened = shown?.items.find((booking) => booking.id === openId) ?? null;
  const marks = useMemo(() => changeMarks(shown?.items ?? [], zone), [shown, zone]);

  const reload = () => setRound((value) => value + 1);
  function finish(booking: BookingOut, change: Change | "created") {
    setOpenId(null);
    setCreating(false);
    setNotice(
      [SAID[change], emailNote(booking.email), customerNote(booking.customer_told)].filter(Boolean).join(" "),
    );
    reload();
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className={secondary} aria-label="Previous week" data-testid="calendar-prev"
            onClick={() => setAnchor(shiftWeek(anchor, -1, zone))}>
            <ChevronLeft aria-hidden="true" size={16} />
          </button>
          <button type="button" className={secondary} data-testid="calendar-today" onClick={() => setAnchor(new Date())}>
            This week
          </button>
          <button type="button" className={secondary} aria-label="Next week" data-testid="calendar-next"
            onClick={() => setAnchor(shiftWeek(anchor, 1, zone))}>
            <ChevronRight aria-hidden="true" size={16} />
          </button>
          <p aria-live="polite" data-testid="calendar-range" className="min-w-0 text-sm font-medium">
            {weekTitle(days)}
          </p>
        </div>
        {scope.canBook && (
          <button type="button" className={action} data-testid="booking-new"
            disabled={types.data === null || people.data === null || rooms.data === null}
            onClick={() => setCreating(true)}>
            New booking
          </button>
        )}
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <label className="block min-w-0 text-sm">Whose calendar
          <select className={`${input} mt-1`} data-testid="calendar-filter" value={filter}
            onChange={(event) => setFilter(event.target.value)}>
            <option value="">Everyone</option>
            {/* Someone or somewhere switched off keeps their place, so their bookings can still be listed. */}
            {(people.data ?? []).length > 0 && (
              <optgroup label="People">
                {(people.data ?? []).map((p) => (
                  <option key={p.id} value={`person:${p.id}`}>
                    {p.is_active ? p.display_name : `${p.display_name} (not taking bookings)`}
                  </option>
                ))}
              </optgroup>
            )}
            {(rooms.data ?? []).length > 0 && (
              <optgroup label="Rooms and shared things">
                {(rooms.data ?? []).map((r) => (
                  <option key={r.id} value={`resource:${r.id}`}>
                    {r.is_active ? r.name : `${r.name} (not available)`}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </label>
        <p className={mutedText}>Times are in {zone}.</p>
      </div>
      {notice && <Notice testId="calendar-notice">{notice}</Notice>}
      {read?.error && !loading && <Notice error testId="calendar-error">{read.error}</Notice>}
      {(people.error || rooms.error || types.error) && (
        <Notice error>{people.error || rooms.error || types.error}</Notice>
      )}
      <div aria-busy={loading} data-testid="calendar">
        {!shown ? (
          read?.error && !loading ? null : <p role="status" className={mutedText}>Loading the calendar…</p>
        ) : wide ? (
          <WeekGrid days={days} zone={zone} entries={entries} today={today} marks={marks}
            onOpen={(b) => setOpenId(b.id)} />
        ) : (
          <Agenda days={days} zone={zone} entries={entries} marks={marks} onOpen={(b) => setOpenId(b.id)} />
        )}
      </div>
      {creating && types.data && people.data && rooms.data && (
        <BookingForm scope={scope} zone={zone} types={types.data} people={people.data} rooms={rooms.data}
          onClose={() => setCreating(false)} onBooked={(booking) => finish(booking, "created")} />
      )}
      {opened && (
        <BookingDetails scope={scope} zone={zone} booking={opened} canChange={canChangeBooking(opened)}
          asked={marks.asked.get(opened.id) ?? null} waiting={marks.waiting.has(opened.id)}
          onClose={() => setOpenId(null)} onChanged={finish}
          onReload={() => { setOpenId(null); reload(); }} />
      )}
    </div>
  );
}
