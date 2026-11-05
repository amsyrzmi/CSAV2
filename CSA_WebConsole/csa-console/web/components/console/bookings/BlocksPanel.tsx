"use client";

import { useState } from "react";
import {
  addBlock,
  listBlocks,
  removeBlock,
  type BookingPersonOut,
  type BookingResourceOut,
} from "@/lib/bookings-api";
import { blockWhen, zonedToUtc } from "@/lib/bookings-view";
import { Notice, Panel, secondary } from "../ui";
import { cardClass, Field, input, mutedText, useAction, useLoaded, ZoneGate, type BookingsScope } from "./parts";

interface Props {
  scope: BookingsScope;
  people: BookingPersonOut[];
  rooms: BookingResourceOut[];
}

/**
 * Times when a person or room can't be booked (a training day, repairs). Shown and entered on the
 * company's clock, so nothing shows until the company's time zone is known; if it can't be read,
 * the panel says so and offers no form, rather than using the browser's zone.
 */
export default function BlocksPanel(props: Props) {
  return (
    <Panel title="Unavailable times">
      <div data-testid="blocks" className="space-y-4">
        <p className={`max-w-prose ${mutedText}`}>
          Make a person or room unavailable for a while, such as a training day or repairs.
        </p>
        <ZoneGate scope={props.scope}>{(zone) => <Blocks {...props} zone={zone} />}</ZoneGate>
      </div>
    </Panel>
  );
}

function Blocks({ scope, people, rooms, zone }: Props & { zone: string }) {
  const blocks = useLoaded(() => listBlocks(scope.tenantId));
  const [who, setWho] = useState("");
  const [startDay, setStartDay] = useState("");
  const [startTime, setStartTime] = useState("09:00");
  const [endDay, setEndDay] = useState("");
  const [endTime, setEndTime] = useState("18:00");
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState("");
  const { busy, error, run } = useAction();
  const names = new Map<string, string>([
    ...people.map((person) => [person.id, person.display_name] as const),
    ...rooms.map((room) => [room.id, room.name] as const),
  ]);

  async function block() {
    const [kind, id] = who.split(":");
    const starts = zonedToUtc(startDay, startTime, zone);
    const ends = zonedToUtc(endDay || startDay, endTime, zone);
    if (!kind || !id) return setProblem("Choose a person or a room.");
    if (!starts || !ends) return setProblem("Choose the day and time it starts and ends.");
    if (ends <= starts) return setProblem("The end must be after the start.");
    setProblem("");
    const saved = await run(() =>
      addBlock(scope.tenantId, {
        subject_kind: kind as "person" | "resource",
        subject_id: id,
        starts_at: starts,
        ends_at: ends,
        note: note.trim() || null,
      }),
    );
    if (!saved) return;
    blocks.reload();
    setNote("");
  }

  return (
    <>
      {blocks.error && <Notice error>{blocks.error}</Notice>}
      {error && <Notice error testId="blocks-error">{error}</Notice>}
      {blocks.data && blocks.data.length === 0 && (
        <p data-testid="blocks-empty" className={mutedText}>Nothing is blocked out.</p>
      )}
      <ul className="space-y-2">
        {(blocks.data ?? []).map((item) => (
          <li key={item.id} data-testid="block" className={`${cardClass} flex flex-wrap items-center justify-between gap-2`}>
            <span className="min-w-0 text-sm [overflow-wrap:anywhere]">
              <span className="font-medium">{names.get(item.subject_id) ?? "Someone"}</span>
              {`: ${blockWhen(item.starts_at, item.ends_at, zone)}`}
              {item.note ? `. ${item.note}` : ""}
            </span>
            {scope.canEdit && (
              <button type="button" className={secondary} disabled={busy}
                aria-label={`Remove the unavailable time for ${names.get(item.subject_id) ?? "this person"}`}
                onClick={() => void run(async () => {
                  await removeBlock(scope.tenantId, item.id);
                  blocks.reload();
                })}>
                Remove
              </button>
            )}
          </li>
        ))}
      </ul>
      {scope.canEdit && (
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            void block();
          }}
        >
          {problem && <div className="sm:col-span-2"><Notice error testId="blocks-problem">{problem}</Notice></div>}
          <Field label="Who or what" className="sm:col-span-2">
            <select className={input} data-testid="block-who" value={who} onChange={(event) => setWho(event.target.value)}>
              <option value="">Choose…</option>
              {people.length > 0 && (
                <optgroup label="People">
                  {people.map((person) => <option key={person.id} value={`person:${person.id}`}>{person.display_name}</option>)}
                </optgroup>
              )}
              {rooms.length > 0 && (
                <optgroup label="Rooms and shared things">
                  {rooms.map((room) => <option key={room.id} value={`resource:${room.id}`}>{room.name}</option>)}
                </optgroup>
              )}
            </select>
          </Field>
          <Field label="From day">
            <input type="date" className={input} data-testid="block-start-day" value={startDay}
              onChange={(event) => setStartDay(event.target.value)} />
          </Field>
          <Field label="From time">
            <input type="time" className={input} value={startTime} onChange={(event) => setStartTime(event.target.value)} />
          </Field>
          <Field label="Until day" hint="Leave empty for the same day.">
            <input type="date" className={input} value={endDay} onChange={(event) => setEndDay(event.target.value)} />
          </Field>
          <Field label="Until time">
            <input type="time" className={input} value={endTime} onChange={(event) => setEndTime(event.target.value)} />
          </Field>
          <Field label="Note (optional)" className="sm:col-span-2" hint={`Times are in ${zone}.`}>
            <input className={input} value={note} maxLength={200} onChange={(event) => setNote(event.target.value)} />
          </Field>
          <div className="sm:col-span-2">
            <button type="submit" className={secondary} disabled={busy} data-testid="block-add">
              Make unavailable
            </button>
          </div>
        </form>
      )}
    </>
  );
}
