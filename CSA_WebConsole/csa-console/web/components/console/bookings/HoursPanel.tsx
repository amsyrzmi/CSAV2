"use client";

import { useState } from "react";
import { addClosedDay, listClosedDays, removeClosedDay, type BookingHoursOut } from "@/lib/bookings-api";
import { dayText, hoursLines, todayInZone } from "@/lib/bookings-view";
import { action, Notice, Panel, secondary } from "../ui";
import HoursDialog from "./HoursDialog";
import { cardClass, Field, input, mutedText, useAction, useLoaded, type BookingsScope, type Loaded } from "./parts";

/** The company's opening hours and the days it is closed. */
export default function HoursPanel({ scope, hours }: { scope: BookingsScope; hours: Loaded<BookingHoursOut> }) {
  const [open, setOpen] = useState(false);
  return (
    <Panel title="Opening hours">
      <div className="space-y-6">
        <div data-testid="company-hours">
          {hours.error ? (
            <Notice error>{hours.error}</Notice>
          ) : !hours.data ? (
            <p role="status" className={mutedText}>Loading hours…</p>
          ) : (
            <>
              {hoursLines(hours.data).map((line) => (
                <p key={line} data-testid="company-hours-line" className="text-sm [overflow-wrap:anywhere]">{line}</p>
              ))}
              <p className={`mt-1 ${mutedText}`}>
                People and rooms follow these unless they have hours of their own.
              </p>
              <div className="mt-3">
                <button type="button" className={scope.canEdit ? action : secondary} data-testid="company-hours-open"
                  onClick={() => setOpen(true)}>
                  {scope.canEdit ? "Change opening hours" : "See each day"}
                </button>
              </div>
            </>
          )}
        </div>
        <ClosedDays scope={scope} />
      </div>
      {open && (
        <HoursDialog
          scope={scope}
          subject={{ kind: "company" }}
          companyDays={null}
          onClose={() => setOpen(false)}
          onSaved={() => hours.reload()}
        />
      )}
    </Panel>
  );
}

function ClosedDays({ scope }: { scope: BookingsScope }) {
  const days = useLoaded(() => listClosedDays(scope.tenantId));
  const [day, setDay] = useState("");
  const [note, setNote] = useState("");
  const { busy, error, run } = useAction();
  const today = todayInZone(scope.zone);
  // A closed day that has passed is history: only today and later are listed.
  const upcoming = (days.data ?? []).filter((item) => !today || item.day >= today);

  async function close() {
    if (!day) return;
    const saved = await run(() => addClosedDay(scope.tenantId, { day, note: note.trim() || null }));
    if (!saved) return;
    days.reload();
    setDay("");
    setNote("");
  }

  return (
    <div data-testid="closed-days">
      <h3 className="text-sm font-semibold">Closed days</h3>
      <p className={`mt-1 ${mutedText}`}>Holidays and other days nobody can be booked.</p>
      {days.error && <div className="mt-2"><Notice error>{days.error}</Notice></div>}
      {error && <div className="mt-2"><Notice error testId="closed-days-error">{error}</Notice></div>}
      {days.data && upcoming.length === 0 && (
        <p data-testid="closed-days-empty" className={`mt-2 ${mutedText}`}>No closed days coming up.</p>
      )}
      <ul className="mt-2 space-y-2">
        {upcoming.map((item) => (
          <li key={item.day} data-testid="closed-day" className={`${cardClass} flex flex-wrap items-center justify-between gap-2`}>
            <span className="min-w-0 text-sm [overflow-wrap:anywhere]">
              <span className="font-medium">{dayText(item.day)}</span>
              {item.note ? `: ${item.note}` : ""}
            </span>
            {scope.canEdit && (
              <button type="button" className={secondary} disabled={busy}
                aria-label={`Open again on ${dayText(item.day)}`}
                onClick={() => void run(async () => {
                  await removeClosedDay(scope.tenantId, item.day);
                  days.reload();
                })}>
                Open again
              </button>
            )}
          </li>
        ))}
      </ul>
      {scope.canEdit && (
        <form
          className="mt-3 grid gap-3 sm:grid-cols-[auto_1fr_auto] sm:items-end"
          onSubmit={(event) => {
            event.preventDefault();
            void close();
          }}
        >
          <Field label="Day">
            <input type="date" className={input} data-testid="closed-day-date" value={day}
              onChange={(event) => setDay(event.target.value)} />
          </Field>
          <Field label="Note (optional)">
            <input className={input} data-testid="closed-day-note" value={note} maxLength={200}
              onChange={(event) => setNote(event.target.value)} />
          </Field>
          <button type="submit" className={secondary} disabled={busy || !day} data-testid="closed-day-add">
            Close this day
          </button>
        </form>
      )}
    </div>
  );
}

