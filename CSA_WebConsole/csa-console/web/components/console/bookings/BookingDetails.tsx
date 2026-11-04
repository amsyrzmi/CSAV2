"use client";

import { useState, type ReactNode } from "react";
import {
  changeBooking,
  moveBooking,
  type BookingAction,
  type BookingFinding,
  type BookingOut,
} from "@/lib/bookings-api";
import {
  bookingTitle,
  blockWhen,
  dayInZone,
  locationLabel,
  mapsLink,
  safeMeetingLink,
  timeInZone,
  wazeLink,
  zonedToUtc,
} from "@/lib/bookings-view";
import Dialog from "../fs/Dialog";
import StatusLabel, { type StatusTone } from "../fs/StatusLabel";
import { action, Notice, secondary } from "../ui";
import FreeTimes from "./FreeTimes";
import { Field, input, mutedText, useAction, type BookingsScope } from "./parts";
import { readRefusal, RefusalNotice, type Refusal } from "./Refusal";

/** What was done, for the sentence on the calendar, and the booking as the server now has it. */
export type Change = "moved" | "cancelled" | "confirmed" | "declined" | "no_show";

const STATUS: Record<string, { text: string; tone: StatusTone }> = {
  pending: { text: "Waiting to be confirmed", tone: "attention" },
  confirmed: { text: "Confirmed", tone: "success" },
  no_show: { text: "No-show", tone: "neutral" },
  cancelled: { text: "Cancelled", tone: "neutral" },
};

const POSTS: Record<Exclude<Change, "moved">, BookingAction> = {
  cancelled: "cancel",
  confirmed: "confirm",
  declined: "decline",
  no_show: "no-show",
};

function Row({ label, children, testId }: { label: string; children: ReactNode; testId?: string }) {
  return (
    <div className="grid gap-0.5 sm:grid-cols-[8rem_1fr] sm:gap-3">
      <dt className="text-sm text-[color:var(--fs-text-secondary)]">{label}</dt>
      <dd data-testid={testId} className="min-w-0 text-sm [overflow-wrap:anywhere]">{children}</dd>
    </div>
  );
}

/**
 * One booking, with what may be done to it. A private meeting the viewer is not part of is "Busy"
 * and shows only its times; an Admin can still move or cancel it. The server has the final say on
 * who may change a booking, so a refusal is shown as a sentence.
 */
export default function BookingDetails({
  scope,
  zone,
  booking,
  canChange,
  onClose,
  onChanged,
  onReload,
}: {
  scope: BookingsScope;
  zone: string;
  booking: BookingOut;
  canChange: boolean;
  onClose: () => void;
  onChanged: (booking: BookingOut, change: Change) => void;
  /** The booking changed under us: close this and read the calendar again. */
  onReload: () => void;
}) {
  const [moving, setMoving] = useState(false);
  const [ask, setAsk] = useState<"cancelled" | "declined" | null>(null);
  const [day, setDay] = useState(dayInZone(booking.starts_at, zone));
  const [time, setTime] = useState(timeInZone(booking.starts_at, zone));
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const { busy, run } = useAction();
  const status = STATUS[booking.status];
  const title = bookingTitle(booking);
  const [now] = useState(() => Date.now());
  const started = new Date(booking.starts_at).getTime() <= now;
  const link = safeMeetingLink(booking.meeting_link);
  const minutes = Math.round((new Date(booking.ends_at).getTime() - new Date(booking.starts_at).getTime()) / 60000);

  async function attempt(task: () => Promise<BookingOut>, change: Change) {
    setRefusal(null);
    let failure: unknown;
    const done = await run(async () => {
      try {
        return await task();
      } catch (cause) {
        failure = cause;
        throw cause;
      }
    });
    if (done) return onChanged(done, change);
    if (failure) setRefusal(readRefusal(failure));
  }

  function move(acknowledged: BookingFinding[] = []) {
    const starts = zonedToUtc(day, time, zone);
    if (!starts) return setRefusal({ kind: "error", text: "Choose the day and time." });
    void attempt(
      () => moveBooking(scope.tenantId, booking.id, { version: booking.version, starts_at: starts, acknowledged }),
      "moved",
    );
  }

  function post(change: Exclude<Change, "moved">) {
    void attempt(() => changeBooking(scope.tenantId, booking.id, POSTS[change], booking.version), change);
  }

  const actions = canChange && !ask && !moving;

  return (
    <Dialog open onClose={onClose} title={title} testId="booking-details" widthClass="max-w-xl">
      <div className="space-y-4">
        {booking.conflict && (
          <Notice warning testId="booking-conflict-note">
            Someone on this booking is no longer available at this time.
          </Notice>
        )}
        <dl className="space-y-2">
          <Row label="When" testId="details-when">{blockWhen(booking.starts_at, booking.ends_at, zone)}</Row>
          {booking.redacted ? (
            <Row label="Details">
              This is a private meeting. Only the people on it can see what it is.
            </Row>
          ) : (
            <>
              <Row label="Status">
                {status ? <StatusLabel tone={status.tone}>{status.text}</StatusLabel> : booking.status}
                {booking.private && <span className="ml-2"><StatusLabel tone="info">Private</StatusLabel></span>}
              </Row>
              {booking.people.length > 0 && (
                <Row label="With" testId="details-people">
                  {booking.people.map((person) => person.display_name).join(", ")}
                </Row>
              )}
              {booking.resources.length > 0 && (
                <Row label="Rooms" testId="details-rooms">
                  {booking.resources.map((room) => room.name).join(", ")}
                </Row>
              )}
              {booking.customer && (
                <Row label="Customer" testId="details-customer">
                  {booking.customer.full_name ?? "No name"}
                  {booking.customer.phone && <span className="block">{booking.customer.phone}</span>}
                  {booking.customer.email && <span className="block">{booking.customer.email}</span>}
                </Row>
              )}
              {booking.guests.length > 0 && (
                <Row label="Guests" testId="details-guests">
                  {booking.guests.map((guest) => (guest.name ? `${guest.name}, ${guest.email}` : guest.email)).join("; ")}
                </Row>
              )}
              {booking.location_kind && (
                <Row label="Where" testId="details-where">
                  {locationLabel(booking.location_kind)}
                  {booking.location_kind === "in_person" && booking.address && (
                    <>
                      <span className="block">{booking.address}</span>
                      <span className="mt-1 flex flex-wrap gap-3">
                        <a className="underline" href={mapsLink(booking.address)} target="_blank"
                          rel="noopener noreferrer" data-testid="details-maps">
                          Open in Maps
                        </a>
                        <a className="underline" href={wazeLink(booking.address)} target="_blank"
                          rel="noopener noreferrer" data-testid="details-waze">
                          Open in Waze
                        </a>
                      </span>
                    </>
                  )}
                  {booking.location_kind === "online" && link && (
                    <span className="mt-1 block">
                      <a className="underline" href={link} target="_blank" rel="noopener noreferrer"
                        data-testid="details-link">
                        Open the meeting link
                      </a>
                    </span>
                  )}
                </Row>
              )}
            </>
          )}
        </dl>

        {moving && (
          <form
            className="space-y-3 rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] p-3"
            data-testid="details-move-form"
            onSubmit={(event) => {
              event.preventDefault();
              move();
            }}
          >
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="New day">
                <input type="date" className={input} data-testid="move-day" value={day}
                  onChange={(event) => { setDay(event.target.value); setRefusal(null); }} />
              </Field>
              <Field label="New time" hint={`In ${zone}.`}>
                <input type="time" className={input} data-testid="move-time" value={time}
                  onChange={(event) => { setTime(event.target.value); setRefusal(null); }} />
              </Field>
            </div>
            {!booking.redacted && day && (
              <FreeTimes scope={scope} zone={zone} day={day} time={time} onPick={setTime}
                query={booking.type_id
                  ? { typeId: booking.type_id, people: [], resources: [] }
                  : { people: booking.people.map((p) => p.id), resources: booking.resources.map((r) => r.id), minutes } } />
            )}
            <div className="flex flex-wrap gap-2">
              <button type="submit" className={action} disabled={busy} data-testid="move-save">Move it</button>
              <button type="button" className={secondary} onClick={() => { setMoving(false); setRefusal(null); }}>
                Keep the time
              </button>
            </div>
          </form>
        )}

        {ask && (
          <div className="space-y-3 rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] p-3"
            data-testid="details-ask">
            <p className="text-sm">
              {ask === "cancelled" ? "Cancel this booking?" : "Decline this booking?"} Everyone on it who has an
              email address is told.
            </p>
            <div className="flex flex-wrap gap-2">
              <button type="button" className={action} disabled={busy} data-testid="details-ask-yes"
                onClick={() => post(ask)}>
                {ask === "cancelled" ? "Yes, cancel it" : "Yes, decline it"}
              </button>
              <button type="button" className={secondary} onClick={() => { setAsk(null); setRefusal(null); }}>
                No, keep it
              </button>
            </div>
          </div>
        )}

        {refusal && (
          <RefusalNotice refusal={refusal} proceedLabel="Move anyway" busy={busy}
            onProceed={() => { if (refusal.kind === "warn") move(refusal.findings); }}
            onPickAnother={() => setRefusal(null)} onReload={onReload} />
        )}

        {actions && (
          <div className="flex flex-wrap gap-2" data-testid="details-actions">
            {booking.status === "pending" && (
              <button type="button" className={action} disabled={busy} data-testid="details-confirm"
                onClick={() => post("confirmed")}>
                Confirm
              </button>
            )}
            <button type="button" className={secondary} disabled={busy} data-testid="details-move"
              onClick={() => setMoving(true)}>
              Move
            </button>
            {booking.status === "pending" && (
              <button type="button" className={secondary} disabled={busy} data-testid="details-decline"
                onClick={() => setAsk("declined")}>
                Decline
              </button>
            )}
            {booking.status === "confirmed" && started && !booking.redacted && (
              <button type="button" className={secondary} disabled={busy} data-testid="details-no-show"
                onClick={() => post("no_show")}>
                Mark as no-show
              </button>
            )}
            <button type="button" className={secondary} disabled={busy} data-testid="details-cancel"
              onClick={() => setAsk("cancelled")}>
              Cancel booking
            </button>
          </div>
        )}
        {!canChange && scope.canBook && !booking.redacted && ["pending", "confirmed"].includes(booking.status) && (
          <p className={mutedText} data-testid="details-readonly">
            Only the person who made a booking, or someone on it, can change it.
          </p>
        )}
      </div>
    </Dialog>
  );
}
