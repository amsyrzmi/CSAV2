"use client";

import { useEffect, useRef, useState } from "react";
import {
  createBooking,
  searchCustomers,
  type BookingCustomer,
  type BookingFinding,
  type BookingOut,
  type BookingPersonOut,
  type BookingResourceOut,
  type BookingTypeOut,
} from "@/lib/bookings-api";
import {
  bookingProblem,
  durationText,
  emptyBookingForm,
  formToBookingIn,
  LOCATIONS,
  locationLabel,
  takesCustomer,
  todayInZone,
  type BookingForm as FormState,
} from "@/lib/bookings-view";
import Dialog from "../fs/Dialog";
import { action, Notice, secondary } from "../ui";
import FreeTimes from "./FreeTimes";
import { Check, Field, input, mutedText, useAction, type BookingsScope } from "./parts";
import { readRefusal, RefusalNotice, type Refusal } from "./Refusal";

/** The customer list for the search box; null when it can't be read (read-only members get 403). */
function useCustomers(scope: BookingsScope, q: string): BookingCustomer[] | null | undefined {
  const [read, setRead] = useState<{ q: string; rows: BookingCustomer[] | null } | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(
      () => {
        searchCustomers(scope.tenantId, q, controller.signal).then(
          (rows) => setRead({ q, rows }),
          () => { if (!controller.signal.aborted) setRead({ q, rows: null }); },
        );
      },
      q ? 250 : 0,
    );
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [scope.tenantId, q]);
  // `undefined` while the answer for this text is still on its way.
  return read?.q === q ? read.rows : undefined;
}

function CustomerPicker({
  scope,
  form,
  set,
}: {
  scope: BookingsScope;
  form: FormState;
  set: (change: Partial<FormState>) => void;
}) {
  const [q, setQ] = useState("");
  const rows = useCustomers(scope, q);
  const chosen = form.contactId;
  const [picked, setPicked] = useState<BookingCustomer | null>(null);
  return (
    <fieldset data-testid="booking-customer" className="space-y-3 rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] p-3">
      <legend className="px-1 text-sm font-medium">Customer</legend>
      {chosen && picked ? (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <span data-testid="booking-customer-chosen" className="[overflow-wrap:anywhere]">
            Booking for <strong>{picked.full_name ?? "this customer"}</strong>
          </span>
          <button type="button" className={secondary} onClick={() => { setPicked(null); set({ contactId: "" }); }}>
            Choose someone else
          </button>
        </p>
      ) : (
        <>
          {rows !== null && (
            <div className="space-y-2">
              <Field label="Find an existing customer">
                <input className={input} data-testid="booking-customer-search" value={q} maxLength={100}
                  placeholder="Name, phone or email" onChange={(event) => setQ(event.target.value)} />
              </Field>
              {rows === undefined ? (
                <p role="status" className={mutedText}>Searching…</p>
              ) : rows.length === 0 ? (
                <p data-testid="booking-customer-none" className={mutedText}>
                  {q ? "No customer matches. Add a new one below." : "No customers yet. Add the first one below."}
                </p>
              ) : (
                <ul className="space-y-1">
                  {rows.map((row) => (
                    <li key={row.id}>
                      <button type="button" className={`${secondary} w-full justify-start text-left`}
                        data-testid="booking-customer-option"
                        onClick={() => { setPicked(row); set({ contactId: row.id }); }}>
                        <span className="min-w-0 [overflow-wrap:anywhere]">
                          {row.full_name ?? "No name"}
                          <span className="block text-xs font-normal">{row.phone ?? row.email ?? ""}</span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-2">
            <p className={`sm:col-span-2 ${mutedText}`}>
              {rows === null ? "Enter the customer's details." : "Or add a new customer."} A phone number or an email is needed.
            </p>
            <Field label="Name" className="sm:col-span-2">
              <input className={input} data-testid="booking-walk-name" value={form.walkName} maxLength={120}
                onChange={(event) => set({ walkName: event.target.value })} />
            </Field>
            <Field label="Phone">
              <input className={input} data-testid="booking-walk-phone" type="tel" value={form.walkPhone} maxLength={30}
                onChange={(event) => set({ walkPhone: event.target.value })} />
            </Field>
            <Field label="Email">
              <input className={input} data-testid="booking-walk-email" type="email" value={form.walkEmail} maxLength={254}
                onChange={(event) => set({ walkEmail: event.target.value })} />
            </Field>
          </div>
        </>
      )}
    </fieldset>
  );
}

/** The dialog for a new booking: of a booking type, or a meeting with colleagues and guests. */
export default function BookingForm({
  scope,
  zone,
  types,
  people,
  rooms,
  onClose,
  onBooked,
}: {
  scope: BookingsScope;
  zone: string;
  types: BookingTypeOut[];
  people: BookingPersonOut[];
  rooms: BookingResourceOut[];
  onClose: () => void;
  onBooked: (booking: BookingOut) => void;
}) {
  const usable = types.filter((type) => type.is_active);
  const [form, setForm] = useState<FormState>(() => ({
    ...emptyBookingForm(todayInZone(zone) ?? ""),
    typeId: usable[0]?.id ?? "",
  }));
  const [problem, setProblem] = useState("");
  const [refusal, setRefusal] = useState<Refusal | null>(null);
  const { busy, run } = useAction();
  const timeInput = useRef<HTMLInputElement>(null);
  const type = usable.find((item) => item.id === form.typeId) ?? null;
  const activePeople = people.filter((person) => person.is_active);
  const activeRooms = rooms.filter((room) => room.is_active);
  const names = new Map([...people.map((p) => [p.id, p.display_name] as const), ...rooms.map((r) => [r.id, r.name] as const)]);

  function set(change: Partial<FormState>) {
    setForm((old) => ({ ...old, ...change }));
    setProblem("");
  }
  /** A new day or time is a new question, so an earlier warning or refusal no longer applies. */
  function setWhen(change: Partial<FormState>) {
    set(change);
    setRefusal(null);
  }
  const toggle = (list: string[], id: string) => (list.includes(id) ? list.filter((item) => item !== id) : [...list, id]);

  async function submit(acknowledged: BookingFinding[] = []) {
    const found = bookingProblem(form, type, zone);
    if (found) return setProblem(found);
    setProblem("");
    setRefusal(null);
    let failure: unknown;
    const booked = await run(async () => {
      try {
        return await createBooking(scope.tenantId, formToBookingIn(form, type, zone, acknowledged));
      } catch (cause) {
        failure = cause;
        throw cause;
      }
    });
    if (booked) return onBooked(booked);
    if (failure) setRefusal(readRefusal(failure));
  }

  const meetingMinutes = Number(form.minutes);
  const slotQuery = type
    ? { typeId: type.id, people: type.delivery === "named" ? form.personIds : [], resources: [] }
    : {
        people: form.personIds,
        resources: form.resourceIds,
        minutes: Number.isInteger(meetingMinutes) && meetingMinutes > 0 ? meetingMinutes : undefined,
      };

  return (
    <Dialog open onClose={onClose} title="New booking" testId="booking-dialog" widthClass="max-w-2xl">
      <form
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <Field label="What are you booking?">
          <select className={input} data-testid="booking-type" value={form.typeId}
            onChange={(event) => setWhen({ typeId: event.target.value, personIds: [], contactId: "" })}>
            {usable.map((item) => (
              <option key={item.id} value={item.id}>{item.name} ({durationText(item.duration_minutes)})</option>
            ))}
            <option value="">A meeting, with no booking type</option>
          </select>
        </Field>

        {type ? (
          <div className="space-y-3">
            <p className={mutedText}>
              {durationText(type.duration_minutes)}, {locationLabel(type.location_kind).toLowerCase()}
              {type.location_kind === "in_person" && type.address ? `, ${type.address}` : ""}.
              {type.needs_confirmation ? " Staff confirm it before the customer is told." : ""}
            </p>
            {type.delivery === "named" && (
              <fieldset className="space-y-1" data-testid="booking-people">
                <legend className="text-sm font-medium">Who is it with?</legend>
                {type.person_ids.map((id) => (
                  <Check key={id} checked={form.personIds.includes(id)}
                    onChange={() => setWhen({ personIds: toggle(form.personIds, id) })}>
                    {names.get(id) ?? "Someone"}
                  </Check>
                ))}
              </fieldset>
            )}
            {type.delivery === "several" && (
              <p className={mutedText}>
                With {type.person_ids.map((id) => names.get(id) ?? "someone").join(", ")}, all together.
              </p>
            )}
            {type.delivery === "any" && <p className={mutedText}>Whoever is free is picked for you.</p>}
            {type.resource_rule === "specific" && (
              <p className={mutedText}>
                Holds {type.resource_ids.map((id) => names.get(id) ?? "a room").join(", ")}.
              </p>
            )}
            {type.resource_rule === "any" && <p className={mutedText}>A free room is picked for you.</p>}
          </div>
        ) : (
          <div className="space-y-3" data-testid="booking-meeting">
            <div className="grid gap-3 sm:grid-cols-[1fr_8rem]">
              <Field label="Title">
                <input className={input} data-testid="booking-title" value={form.title} maxLength={120}
                  onChange={(event) => set({ title: event.target.value })} />
              </Field>
              <Field label="Minutes">
                <input className={input} data-testid="booking-minutes" inputMode="numeric" value={form.minutes}
                  onChange={(event) => setWhen({ minutes: event.target.value })} />
              </Field>
            </div>
            <fieldset className="space-y-1" data-testid="booking-colleagues">
              <legend className="text-sm font-medium">Colleagues</legend>
              {activePeople.length === 0 && <p className={mutedText}>Nobody is on the list yet.</p>}
              {activePeople.map((person) => (
                <Check key={person.id} checked={form.personIds.includes(person.id)}
                  onChange={() => setWhen({ personIds: toggle(form.personIds, person.id) })}>
                  {person.display_name}
                </Check>
              ))}
            </fieldset>
            {activeRooms.length > 0 && (
              <fieldset className="space-y-1" data-testid="booking-rooms">
                <legend className="text-sm font-medium">Rooms and shared things</legend>
                {activeRooms.map((room) => (
                  <Check key={room.id} checked={form.resourceIds.includes(room.id)}
                    onChange={() => setWhen({ resourceIds: toggle(form.resourceIds, room.id) })}>
                    {room.name}
                  </Check>
                ))}
              </fieldset>
            )}
            <Field label="Where">
              <select className={input} data-testid="booking-location" value={form.locationKind}
                onChange={(event) => set({ locationKind: event.target.value as FormState["locationKind"] })}>
                {LOCATIONS.map((kind) => <option key={kind} value={kind}>{locationLabel(kind)}</option>)}
              </select>
            </Field>
            {form.locationKind === "in_person" && (
              <Field label="Address (optional)">
                <input className={input} data-testid="booking-address" value={form.address} maxLength={300}
                  onChange={(event) => set({ address: event.target.value })} />
              </Field>
            )}
            {form.locationKind === "online" && (
              <Field label="Meeting link (optional)" hint="It must start with https://.">
                <input className={input} data-testid="booking-link" value={form.link} maxLength={500}
                  onChange={(event) => set({ link: event.target.value })} />
              </Field>
            )}
            <Check checked={form.isPrivate} onChange={(isPrivate) => set({ isPrivate })} testId="booking-private">
              Private: only the people on it and you can see what it is. Everyone else sees &ldquo;Busy&rdquo;.
            </Check>
          </div>
        )}

        {takesCustomer(type) && <CustomerPicker scope={scope} form={form} set={set} />}

        <div className="space-y-2">
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Day">
              <input type="date" className={input} data-testid="booking-day" value={form.day}
                onChange={(event) => setWhen({ day: event.target.value })} />
            </Field>
            <Field label="Time" hint={`In ${zone}.`}>
              <input ref={timeInput} type="time" className={input} data-testid="booking-time" value={form.time}
                onChange={(event) => setWhen({ time: event.target.value })} />
            </Field>
          </div>
          {form.day && (
            <FreeTimes scope={scope} zone={zone} day={form.day} query={slotQuery} time={form.time}
              onPick={(time) => setWhen({ time })} />
          )}
        </div>

        <fieldset className="space-y-2" data-testid="booking-guests">
          <legend className="text-sm font-medium">Guests from outside (optional)</legend>
          {form.guests.map((guest, index) => (
            <div key={index} className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]">
              <input className={input} aria-label={`Guest ${index + 1} name`} placeholder="Name" value={guest.name}
                maxLength={120}
                onChange={(event) => set({ guests: form.guests.map((old, at) => (at === index ? { ...old, name: event.target.value } : old)) })} />
              <input className={input} aria-label={`Guest ${index + 1} email`} placeholder="Email" type="email"
                data-testid="booking-guest-email" value={guest.email} maxLength={254}
                onChange={(event) => set({ guests: form.guests.map((old, at) => (at === index ? { ...old, email: event.target.value } : old)) })} />
              <button type="button" className={secondary} aria-label={`Remove guest ${index + 1}`}
                onClick={() => set({ guests: form.guests.filter((_, at) => at !== index) })}>
                Remove
              </button>
            </div>
          ))}
          <button type="button" className={secondary} data-testid="booking-guest-add"
            onClick={() => set({ guests: [...form.guests, { name: "", email: "" }] })}>
            Add a guest
          </button>
        </fieldset>

        {problem && <Notice error testId="booking-problem">{problem}</Notice>}
        {refusal && (
          <RefusalNotice refusal={refusal} proceedLabel="Book anyway" busy={busy}
            onProceed={() => { if (refusal.kind === "warn") void submit(refusal.findings); }}
            onPickAnother={() => { setRefusal(null); timeInput.current?.focus(); }}
            onReload={onClose} />
        )}

        <div className="flex flex-wrap gap-2">
          <button type="submit" className={action} disabled={busy} data-testid="booking-save">Book</button>
          <button type="button" className={secondary} onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Dialog>
  );
}
