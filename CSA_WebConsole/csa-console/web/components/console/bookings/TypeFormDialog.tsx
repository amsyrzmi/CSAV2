"use client";

import { useState } from "react";
import {
  addType,
  changeType,
  type BookingPersonOut,
  type BookingResourceOut,
  type BookingTypeOut,
} from "@/lib/bookings-api";
import {
  AUDIENCES,
  DELIVERIES,
  LOCATIONS,
  RESOURCE_RULES,
  audienceLabel,
  deliveryLabel,
  durationText,
  emptyTypeForm,
  formToTypeIn,
  formToTypePatch,
  locationLabel,
  resourceRuleLabel,
  typeProblem,
  typeToForm,
  type TypeForm,
} from "@/lib/bookings-view";
import { OpenDialog as Dialog } from "./parts";
import { action, Notice, secondary } from "../ui";
import { Check, Field, input, mutedText, useAction, type BookingsScope } from "./parts";

/** Active ones, plus any already chosen that were switched off since, marked as such. */
function choices<T extends { id: string; is_active: boolean }>(items: T[], chosen: string[]) {
  return items.filter((item) => item.is_active || chosen.includes(item.id));
}

function toggle(list: string[], id: string, on: boolean): string[] {
  return on ? [...list.filter((item) => item !== id), id] : list.filter((item) => item !== id);
}

/** Add a booking type, or change one. The server has the last word; the form says what is missing first. */
export default function TypeFormDialog({
  scope,
  type,
  people,
  resources,
  onClose,
  onSaved,
}: {
  scope: BookingsScope;
  /** The type to change, or null to add one. */
  type: BookingTypeOut | null;
  people: BookingPersonOut[];
  resources: BookingResourceOut[];
  onClose: () => void;
  onSaved: (saved: BookingTypeOut) => void;
}) {
  const [form, setForm] = useState<TypeForm>(() => (type ? typeToForm(type) : emptyTypeForm()));
  const [problem, setProblem] = useState("");
  const { busy, error, run } = useAction();
  const set = <K extends keyof TypeForm>(key: K, value: TypeForm[K]) => setForm((old) => ({ ...old, [key]: value }));

  async function save() {
    const found = typeProblem(form);
    setProblem(found ?? "");
    if (found) return;
    const saved = await run(() =>
      type
        ? changeType(scope.tenantId, type.id, formToTypePatch(form, type.version))
        : addType(scope.tenantId, formToTypeIn(form)),
    );
    if (saved) onSaved(saved);
  }

  const visiblePeople = choices(people, form.personIds);
  const visibleRooms = choices(resources, form.resourceIds);
  const peopleLabel =
    form.delivery === "named"
      ? "Who can take this booking"
      : form.delivery === "several"
        ? "Everyone who attends"
        : "Who can take this booking";
  const peopleHint =
    form.delivery === "any"
      ? "Leave everyone unticked to let any active person take it."
      : form.delivery === "several"
        ? "Each of these people is booked together for the whole time."
        : "Choose at least one person.";

  return (
    <Dialog onClose={() => !busy && onClose()} title={type ? `Change ${type.name}` : "Add a booking type"}
      testId="type-dialog" widthClass="max-w-2xl">
      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
        className="space-y-4"
      >
        {(problem || error) && <Notice error testId="type-error">{problem || error}</Notice>}
        <fieldset disabled={busy} className="grid min-w-0 gap-4 sm:grid-cols-2">
          <Field label="Name" className="sm:col-span-2">
            <input className={input} data-testid="type-name" value={form.name} maxLength={120}
              onChange={(event) => set("name", event.target.value)} />
          </Field>
          <Field label="Length (minutes)" hint={durationText(Number(form.duration))}>
            <input className={input} data-testid="type-duration" inputMode="numeric" value={form.duration}
              onChange={(event) => set("duration", event.target.value)} />
          </Field>
          <Field label="Who it is for">
            <select className={input} data-testid="type-audience" value={form.audience}
              onChange={(event) => set("audience", event.target.value as TypeForm["audience"])}>
              {AUDIENCES.map((value) => <option key={value} value={value}>{audienceLabel(value)}</option>)}
            </select>
          </Field>
          <Field label="Where it happens">
            <select className={input} data-testid="type-location" value={form.locationKind}
              onChange={(event) => set("locationKind", event.target.value as TypeForm["locationKind"])}>
              {LOCATIONS.map((value) => <option key={value} value={value}>{locationLabel(value)}</option>)}
            </select>
          </Field>
          {form.locationKind === "in_person" && (
            <Field label="Address">
              <input className={input} data-testid="type-address" value={form.address} maxLength={300}
                onChange={(event) => set("address", event.target.value)} />
            </Field>
          )}
          {form.locationKind === "online" && (
            <Field label="Meeting link" hint="It must start with https://">
              <input className={input} data-testid="type-link" value={form.link} maxLength={500}
                onChange={(event) => set("link", event.target.value)} />
            </Field>
          )}
          <Field label="Who takes it">
            <select className={input} data-testid="type-delivery" value={form.delivery}
              onChange={(event) => set("delivery", event.target.value as TypeForm["delivery"])}>
              {DELIVERIES.map((value) => <option key={value} value={value}>{deliveryLabel(value)}</option>)}
            </select>
          </Field>
          <fieldset className="min-w-0 sm:col-span-2" data-testid="type-people">
            <legend className="text-sm">{peopleLabel}</legend>
            {visiblePeople.length === 0 ? (
              <p className={`mt-1 ${mutedText}`}>No one is on the list yet. Add people on the People &amp; rooms page first.</p>
            ) : (
              <div className="mt-1 grid gap-x-4 sm:grid-cols-2">
                {visiblePeople.map((person) => (
                  <Check key={person.id} checked={form.personIds.includes(person.id)}
                    onChange={(on) => set("personIds", toggle(form.personIds, person.id, on))}>
                    {person.display_name}{person.is_active ? "" : " (not taking bookings)"}
                  </Check>
                ))}
              </div>
            )}
            <p className={`mt-1 ${mutedText}`}>{peopleHint}</p>
          </fieldset>
          <Field label="Room or shared thing" className="sm:col-span-2">
            <select className={input} data-testid="type-resource-rule" value={form.resourceRule}
              onChange={(event) => set("resourceRule", event.target.value as TypeForm["resourceRule"])}>
              {RESOURCE_RULES.map((value) => <option key={value} value={value}>{resourceRuleLabel(value)}</option>)}
            </select>
          </Field>
          {form.resourceRule !== "none" && (
            <fieldset className="min-w-0 sm:col-span-2" data-testid="type-rooms">
              <legend className="text-sm">Rooms and shared things</legend>
              {visibleRooms.length === 0 ? (
                <p className={`mt-1 ${mutedText}`}>None added yet. Add a room on the People &amp; rooms page first.</p>
              ) : (
                <div className="mt-1 grid gap-x-4 sm:grid-cols-2">
                  {visibleRooms.map((room) => (
                    <Check key={room.id} checked={form.resourceIds.includes(room.id)}
                      onChange={(on) => set("resourceIds", toggle(form.resourceIds, room.id, on))}>
                      {room.name}{room.is_active ? "" : " (switched off)"}
                    </Check>
                  ))}
                </div>
              )}
            </fieldset>
          )}
          <div className="sm:col-span-2">
            <Check checked={form.needsConfirmation} onChange={(on) => set("needsConfirmation", on)}
              testId="type-confirmation">
              Staff confirm each booking first. The time is held until they do.
            </Check>
          </div>
          <Field label="Time to reset afterwards (minutes)" hint={durationText(Number(form.buffer))}>
            <input className={input} inputMode="numeric" value={form.buffer}
              onChange={(event) => set("buffer", event.target.value)} />
          </Field>
          <Field label="Shortest notice (minutes)" hint={durationText(Number(form.minNotice))}>
            <input className={input} inputMode="numeric" value={form.minNotice}
              onChange={(event) => set("minNotice", event.target.value)} />
          </Field>
          <Field label="Furthest ahead (days)">
            <input className={input} inputMode="numeric" value={form.maxAdvance}
              onChange={(event) => set("maxAdvance", event.target.value)} />
          </Field>
          <Field label="Last time to change or cancel (minutes before it starts)" hint={durationText(Number(form.cutoff))}>
            <input className={input} inputMode="numeric" value={form.cutoff}
              onChange={(event) => set("cutoff", event.target.value)} />
          </Field>
          {type && (
            <div className="sm:col-span-2">
              <Check checked={form.active} onChange={(on) => set("active", on)} testId="type-active">
                People can book this type. Untick to switch it off.
              </Check>
            </div>
          )}
        </fieldset>
        <div className="flex flex-wrap gap-2">
          <button type="submit" className={action} disabled={busy} data-testid="type-save">
            {type ? "Save changes" : "Add booking type"}
          </button>
          <button type="button" className={secondary} disabled={busy} onClick={onClose}>Cancel</button>
        </div>
      </form>
    </Dialog>
  );
}
