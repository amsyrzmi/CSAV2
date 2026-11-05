"use client";

import Link from "next/link";
import { useState } from "react";
import {
  createStarterTypes,
  listPeople,
  listResources,
  listTypes,
  type BookingTypeOut,
} from "@/lib/bookings-api";
import { resourceRuleLabel, typeSummary } from "@/lib/bookings-view";
import StatusLabel from "../fs/StatusLabel";
import { action, Notice, Panel, secondary } from "../ui";
import TypeFormDialog from "./TypeFormDialog";
import { cardClass, mutedText, useAction, useLoaded, type BookingsScope } from "./parts";

/** The booking types page: what people can book, and the first step when there is nothing yet. */
export default function BookingTypes({ scope, peopleHref }: { scope: BookingsScope; peopleHref: string }) {
  const types = useLoaded(() => listTypes(scope.tenantId));
  const people = useLoaded(() => listPeople(scope.tenantId));
  const rooms = useLoaded(() => listResources(scope.tenantId));
  const [editing, setEditing] = useState<BookingTypeOut | "new" | null>(null);
  const [notice, setNotice] = useState("");
  const starters = useAction();

  const list = types.data;
  const nameOf = new Map((people.data ?? []).map((person) => [person.id, person.display_name]));
  const roomName = new Map((rooms.data ?? []).map((room) => [room.id, room.name]));
  const noPeople = people.data !== null && !people.data.some((person) => person.is_active);

  async function startWithStarters() {
    const created = await starters.run(() => createStarterTypes(scope.tenantId));
    if (!created) return;
    types.replace(created);
    setNotice(created.length ? `Added ${created.map((type) => type.name).join(" and ")}.` : "");
  }

  function saved(type: BookingTypeOut) {
    types.replace((list ?? []).some((item) => item.id === type.id)
      ? (list ?? []).map((item) => (item.id === type.id ? type : item))
      : [...(list ?? []), type]);
    setEditing(null);
    setNotice(`Saved ${type.name}.`);
  }

  if (types.error && !list) return <Notice error testId="types-error">{types.error}</Notice>;
  if (!list) return <p role="status" className={mutedText}>Loading booking types…</p>;

  return (
    <div className="space-y-4">
      {notice && <Notice testId="types-notice">{notice}</Notice>}
      {(types.error || starters.error) && <Notice error>{starters.error || types.error}</Notice>}
      {list.length === 0 ? (
        <Panel title="No booking types yet">
          <div data-testid="types-empty" className="space-y-4">
            <p className="max-w-prose text-sm">
              A booking type is something people can book, such as a 30-minute appointment. It sets how
              long it takes, who it is for and who takes it.
            </p>
            {scope.canEdit ? (
              <>
                <p className="max-w-prose text-sm">
                  Start with two ready-made types, Appointment (30 minutes) and Consultation (60 minutes).
                  You can change them afterwards.
                </p>
                <div className="flex flex-wrap gap-2">
                  <button type="button" className={action} data-testid="types-starters"
                    disabled={starters.busy} onClick={() => void startWithStarters()}>
                    Create the starter types
                  </button>
                  <button type="button" className={secondary} data-testid="types-add-own"
                    onClick={() => setEditing("new")}>
                    Add your own instead
                  </button>
                </div>
              </>
            ) : (
              <p className="max-w-prose text-sm">An Admin adds the booking types for your company.</p>
            )}
          </div>
        </Panel>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className={`max-w-prose ${mutedText}`}>
              {list.length === 1 ? "1 booking type" : `${list.length} booking types`}
            </p>
            {scope.canEdit && (
              <button type="button" className={action} data-testid="types-add" onClick={() => setEditing("new")}>
                Add a booking type
              </button>
            )}
          </div>
          <ul data-testid="types-list" className="grid gap-3 lg:grid-cols-2">
            {list.map((type) => (
              <li key={type.id} data-testid="type-card" className={cardClass}>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <h3 className="min-w-0 font-(family-name:--fs-font-heading) text-base font-semibold [overflow-wrap:anywhere]">
                    {type.name}
                  </h3>
                  <div className="flex flex-wrap gap-1">
                    {type.needs_confirmation && <StatusLabel tone="info">Staff confirm</StatusLabel>}
                    {!type.is_active && <StatusLabel tone="neutral">Switched off</StatusLabel>}
                  </div>
                </div>
                <p className={`mt-1 ${mutedText}`}>{typeSummary(type)}</p>
                {type.location_kind === "in_person" && type.address && (
                  <p className={mutedText}>{type.address}</p>
                )}
                {type.location_kind === "online" && type.meeting_link && (
                  <p className={mutedText}>{type.meeting_link}</p>
                )}
                {type.delivery !== "any" || type.person_ids.length > 0 ? (
                  <p className={mutedText}>
                    {type.person_ids.length
                      ? type.person_ids.map((id) => nameOf.get(id) ?? "Someone").join(", ")
                      : "No one chosen yet"}
                  </p>
                ) : null}
                {type.resource_rule !== "none" && (
                  <p className={mutedText}>
                    {resourceRuleLabel(type.resource_rule)}: {type.resource_ids.map((id) => roomName.get(id) ?? "A room").join(", ")}
                  </p>
                )}
                {scope.canEdit && (
                  <div className="mt-3">
                    <button type="button" className={secondary} aria-label={`Change ${type.name}`}
                      data-testid="type-edit" onClick={() => setEditing(type)}>
                      Change
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
      {noPeople && (
        <Notice warning testId="types-need-people">
          Nobody can take these bookings yet.{" "}
          <Link href={scope.query ? `${peopleHref}?${scope.query}` : peopleHref} className="underline">
            Add people on the People &amp; rooms page.
          </Link>
        </Notice>
      )}
      {editing && (
        <TypeFormDialog
          scope={scope}
          type={editing === "new" ? null : editing}
          people={people.data ?? []}
          resources={rooms.data ?? []}
          onClose={() => setEditing(null)}
          onSaved={saved}
        />
      )}
    </div>
  );
}
