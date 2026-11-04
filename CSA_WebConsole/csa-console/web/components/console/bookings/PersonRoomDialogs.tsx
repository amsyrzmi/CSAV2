"use client";

import { useState, type ReactNode } from "react";
import {
  addPerson,
  addResource,
  changePerson,
  changeResource,
  type BookingPersonOut,
  type BookingResourceOut,
} from "@/lib/bookings-api";
import { isEmail } from "@/lib/bookings-view";
import { OpenDialog as Dialog } from "./parts";
import { action, Notice, secondary } from "../ui";
import { Check, Field, input, useAction, type BookingsScope } from "./parts";

function FormShell({
  title,
  testId,
  problem,
  busy,
  submitLabel,
  onSubmit,
  onClose,
  children,
}: {
  title: string;
  testId: string;
  problem: string;
  busy: boolean;
  submitLabel: string;
  onSubmit: () => void;
  onClose: () => void;
  children: ReactNode;
}) {
  return (
    <Dialog onClose={() => !busy && onClose()} title={title} testId={testId}>
      <form
        noValidate
        className="space-y-4"
        onSubmit={(event) => {
          event.preventDefault();
          onSubmit();
        }}
      >
        {problem && <Notice error testId={`${testId}-error`}>{problem}</Notice>}
        <fieldset disabled={busy} className="min-w-0 space-y-4">{children}</fieldset>
        <div className="flex flex-wrap gap-2">
          <button type="submit" className={action} disabled={busy} data-testid={`${testId}-save`}>{submitLabel}</button>
          <button type="button" className={secondary} disabled={busy} onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Dialog>
  );
}

/** Add a person by hand, or change one. Linked accounts come from "Add everyone from your team". */
export function PersonDialog({
  scope,
  person,
  onClose,
  onSaved,
}: {
  scope: BookingsScope;
  person: BookingPersonOut | null;
  onClose: () => void;
  onSaved: (saved: BookingPersonOut) => void;
}) {
  const [name, setName] = useState(person?.display_name ?? "");
  const [email, setEmail] = useState(person?.email ?? "");
  const [active, setActive] = useState(person?.is_active ?? true);
  const [problem, setProblem] = useState("");
  const { busy, error, run } = useAction();

  async function save() {
    const address = email.trim();
    const found = !name.trim()
      ? "Enter the person's name."
      : address && !isEmail(address)
        ? "Enter one email address, like name@example.com, or leave it empty."
        : "";
    setProblem(found);
    if (found) return;
    const saved = await run(() =>
      person
        ? changePerson(scope.tenantId, person.id, {
            version: person.version,
            display_name: name.trim(),
            email: address || null,
            is_active: active,
          })
        : addPerson(scope.tenantId, { display_name: name.trim(), email: address || null }),
    );
    if (saved) onSaved(saved);
  }

  return (
    <FormShell title={person ? `Change ${person.display_name}` : "Add a person"} testId="person-dialog"
      problem={problem || error} busy={busy} submitLabel={person ? "Save changes" : "Add person"}
      onSubmit={() => void save()} onClose={onClose}>
      <Field label="Name">
        <input className={input} data-testid="person-name" value={name} maxLength={120}
          onChange={(event) => setName(event.target.value)} />
      </Field>
      <Field label="Email (optional)" hint="Used to send them calendar invitations.">
        <input className={input} data-testid="person-email" type="email" value={email} maxLength={254}
          onChange={(event) => setEmail(event.target.value)} />
      </Field>
      {person && (
        <Check checked={active} onChange={setActive} testId="person-active">
          Can be booked. Untick when they are not taking bookings.
        </Check>
      )}
    </FormShell>
  );
}

/** Add a room or shared thing, or change one. */
export function RoomDialog({
  scope,
  room,
  onClose,
  onSaved,
}: {
  scope: BookingsScope;
  room: BookingResourceOut | null;
  onClose: () => void;
  onSaved: (saved: BookingResourceOut) => void;
}) {
  const [name, setName] = useState(room?.name ?? "");
  const [capacity, setCapacity] = useState(room?.capacity === null || room === null ? "" : String(room.capacity));
  const [active, setActive] = useState(room?.is_active ?? true);
  const [problem, setProblem] = useState("");
  const { busy, error, run } = useAction();

  async function save() {
    const text = capacity.trim();
    const size = text === "" ? null : /^\d+$/.test(text) ? Number(text) : NaN;
    const found = !name.trim()
      ? "Enter a name for the room or shared thing."
      : size !== null && !(size >= 1 && size <= 10000)
        ? "Enter how many it holds as a whole number from 1 to 10,000, or leave it empty."
        : "";
    setProblem(found);
    if (found) return;
    const saved = await run(() =>
      room
        ? changeResource(scope.tenantId, room.id, {
            version: room.version,
            name: name.trim(),
            capacity: size,
            is_active: active,
          })
        : addResource(scope.tenantId, { name: name.trim(), capacity: size }),
    );
    if (saved) onSaved(saved);
  }

  return (
    <FormShell title={room ? `Change ${room.name}` : "Add a room or shared thing"} testId="room-dialog"
      problem={problem || error} busy={busy} submitLabel={room ? "Save changes" : "Add room"}
      onSubmit={() => void save()} onClose={onClose}>
      <Field label="Name">
        <input className={input} data-testid="room-name" value={name} maxLength={120}
          onChange={(event) => setName(event.target.value)} />
      </Field>
      <Field label="How many it holds (optional)">
        <input className={input} data-testid="room-capacity" inputMode="numeric" value={capacity}
          onChange={(event) => setCapacity(event.target.value)} />
      </Field>
      {room && (
        <Check checked={active} onChange={setActive} testId="room-active">
          Can be booked. Untick to switch it off.
        </Check>
      )}
    </FormShell>
  );
}
