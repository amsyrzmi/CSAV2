"use client";

import { useState } from "react";
import {
  addPeopleFromTeam,
  getHours,
  listPeople,
  listResources,
  type BookingPersonOut,
  type BookingResourceOut,
} from "@/lib/bookings-api";
import { emailForViewer, periodsToDays } from "@/lib/bookings-view";
import StatusLabel from "../fs/StatusLabel";
import { action, Notice, Panel, secondary } from "../ui";
import BlocksPanel from "./BlocksPanel";
import HoursDialog, { type HoursSubject } from "./HoursDialog";
import HoursPanel from "./HoursPanel";
import { PersonDialog, RoomDialog } from "./PersonRoomDialogs";
import { cardClass, mutedText, useAction, useLoaded, type BookingsScope } from "./parts";

type Dialog =
  | { kind: "person"; person: BookingPersonOut | null }
  | { kind: "room"; room: BookingResourceOut | null }
  | { kind: "hours"; subject: HoursSubject };

/** The people and rooms page: who and what can be booked, and when they are available. */
export default function PeopleAndRooms({ scope }: { scope: BookingsScope }) {
  const companyHours = useLoaded(() => getHours(scope.tenantId));
  const people = useLoaded(() => listPeople(scope.tenantId));
  const rooms = useLoaded(() => listResources(scope.tenantId));
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [notice, setNotice] = useState("");
  const fromTeam = useAction();

  const peopleList = people.data;
  const roomList = rooms.data;

  async function addEveryone() {
    const added = await fromTeam.run(() => addPeopleFromTeam(scope.tenantId));
    if (!added) return;
    people.reload();
    setNotice(
      added.length === 0
        ? "Everyone on your team is already on the list."
        : added.length === 1
          ? `Added ${added[0].display_name}.`
          : `Added ${added.length} people from your team.`,
    );
  }

  function upsert<T extends { id: string }>(list: T[] | null, item: T): T[] {
    const all = list ?? [];
    return all.some((entry) => entry.id === item.id)
      ? all.map((entry) => (entry.id === item.id ? item : entry))
      : [...all, item];
  }

  return (
    <div className="space-y-5">
      {notice && <Notice testId="people-notice">{notice}</Notice>}
      <HoursPanel scope={scope} hours={companyHours} />

      <Panel title="People">
        <div data-testid="people" className="space-y-4">
          <p className={`max-w-prose ${mutedText}`}>The people who can be booked.</p>
          {people.error && <Notice error>{people.error}</Notice>}
          {fromTeam.error && <Notice error testId="people-error">{fromTeam.error}</Notice>}
          {!peopleList && !people.error && <p role="status" className={mutedText}>Loading people…</p>}
          {peopleList && peopleList.length === 0 && (
            <div data-testid="people-empty" className="space-y-3">
              <p className="max-w-prose text-sm">
                No one is on the list yet.{" "}
                {scope.canEdit
                  ? "Add your team to get started, then give each person their own hours if they differ."
                  : "An Admin adds the people who can be booked."}
              </p>
            </div>
          )}
          {scope.canEdit && peopleList && (
            <div className="flex flex-wrap gap-2">
              <button type="button" className={peopleList.length === 0 ? action : secondary}
                data-testid="people-from-team" disabled={fromTeam.busy} onClick={() => void addEveryone()}>
                Add everyone from your team
              </button>
              <button type="button" className={secondary} data-testid="people-add"
                onClick={() => setDialog({ kind: "person", person: null })}>
                Add a person
              </button>
            </div>
          )}
          {peopleList && peopleList.length > 0 && (
            <ul className="grid gap-3 lg:grid-cols-2" data-testid="people-list">
              {peopleList.map((person) => {
                const email = emailForViewer(person.email, scope.canEdit);
                return (
                  <li key={person.id} data-testid="person-card" className={cardClass}>
                    <div className="flex flex-wrap items-start justify-between gap-2">
                      <h3 className="min-w-0 font-(family-name:--fs-font-heading) text-base font-semibold [overflow-wrap:anywhere]">
                        {person.display_name}
                      </h3>
                      {!person.is_active && <StatusLabel tone="neutral">Not taking bookings</StatusLabel>}
                    </div>
                    {email && <p className={`mt-1 ${mutedText}`}>{email}</p>}
                    {person.employee_id && <p className={mutedText}>Linked to HR</p>}
                    <div className="mt-3 flex flex-wrap gap-2">
                      <button type="button" className={secondary} aria-label={`Working hours for ${person.display_name}`}
                        data-testid="person-hours"
                        onClick={() => setDialog({ kind: "hours", subject: { kind: "person", id: person.id, name: person.display_name } })}>
                        Working hours
                      </button>
                      {scope.canEdit && (
                        <button type="button" className={secondary} aria-label={`Change ${person.display_name}`}
                          data-testid="person-edit" onClick={() => setDialog({ kind: "person", person })}>
                          Change
                        </button>
                      )}
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </Panel>

      <Panel title="Rooms and shared things">
        <div data-testid="rooms" className="space-y-4">
          <p className={`max-w-prose ${mutedText}`}>Rooms, equipment or anything else that can be booked.</p>
          {rooms.error && <Notice error>{rooms.error}</Notice>}
          {!roomList && !rooms.error && <p role="status" className={mutedText}>Loading rooms…</p>}
          {roomList && roomList.length === 0 && (
            <p data-testid="rooms-empty" className="max-w-prose text-sm">
              Nothing added yet.{" "}
              {scope.canEdit ? "Add a room if bookings need one." : "An Admin adds the rooms and shared things."}
            </p>
          )}
          {scope.canEdit && roomList && (
            <button type="button" className={secondary} data-testid="room-add"
              onClick={() => setDialog({ kind: "room", room: null })}>
              Add a room or shared thing
            </button>
          )}
          {roomList && roomList.length > 0 && (
            <ul className="grid gap-3 lg:grid-cols-2" data-testid="rooms-list">
              {roomList.map((room) => (
                <li key={room.id} data-testid="room-card" className={cardClass}>
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <h3 className="min-w-0 font-(family-name:--fs-font-heading) text-base font-semibold [overflow-wrap:anywhere]">
                      {room.name}
                    </h3>
                    {!room.is_active && <StatusLabel tone="neutral">Switched off</StatusLabel>}
                  </div>
                  {room.capacity !== null && (
                    <p className={`mt-1 ${mutedText}`}>Holds {room.capacity}</p>
                  )}
                  <div className="mt-3 flex flex-wrap gap-2">
                    <button type="button" className={secondary} aria-label={`Working hours for ${room.name}`}
                      data-testid="room-hours"
                      onClick={() => setDialog({ kind: "hours", subject: { kind: "resource", id: room.id, name: room.name } })}>
                      Working hours
                    </button>
                    {scope.canEdit && (
                      <button type="button" className={secondary} aria-label={`Change ${room.name}`}
                        data-testid="room-edit" onClick={() => setDialog({ kind: "room", room })}>
                        Change
                      </button>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Panel>

      <BlocksPanel scope={scope} people={peopleList ?? []} rooms={roomList ?? []} />

      {dialog?.kind === "person" && (
        <PersonDialog scope={scope} person={dialog.person} onClose={() => setDialog(null)}
          onSaved={(saved) => {
            people.replace(upsert(peopleList, saved));
            setDialog(null);
            setNotice(`Saved ${saved.display_name}.`);
          }} />
      )}
      {dialog?.kind === "room" && (
        <RoomDialog scope={scope} room={dialog.room} onClose={() => setDialog(null)}
          onSaved={(saved) => {
            rooms.replace(upsert(roomList, saved));
            setDialog(null);
            setNotice(`Saved ${saved.name}.`);
          }} />
      )}
      {dialog?.kind === "hours" && (
        <HoursDialog scope={scope} subject={dialog.subject}
          companyDays={companyHours.data ? periodsToDays(companyHours.data.periods) : null}
          onClose={() => setDialog(null)} />
      )}
    </div>
  );
}
