"use client";

import { useEffect, useState } from "react";
import { getAvailability } from "@/lib/bookings-api";
import { dayWindow, timeInZone } from "@/lib/bookings-view";
import { StatusChips } from "../kit";
import { mutedText, type BookingsScope } from "./parts";

/** At most this many free times are offered; anything else can be typed. */
const MOST_OFFERED = 24;

interface Query {
  typeId?: string;
  people: string[];
  resources: string[];
  minutes?: number;
}

/**
 * The free times on one day, as chips that fill in the time. Asks only when it can: a type, or
 * people and rooms with a length. The time can always be typed instead (outside hours is a
 * warning, not a wall), so a failure here never blocks booking.
 */
export default function FreeTimes({
  scope,
  zone,
  day,
  query,
  time,
  onPick,
}: {
  scope: BookingsScope;
  zone: string;
  day: string;
  query: Query;
  time: string;
  onPick: (time: string) => void;
}) {
  const bounds = dayWindow(day, zone);
  const ready = Boolean(bounds) && (query.typeId || ((query.people.length || query.resources.length) && query.minutes));
  const key = ready
    ? [bounds?.from, query.typeId, query.minutes, query.people.join(","), query.resources.join(",")].join("|")
    : "";
  const [read, setRead] = useState<{ key: string; times: string[] | null } | null>(null);

  useEffect(() => {
    if (!key || !bounds) return;
    const controller = new AbortController();
    getAvailability(
      scope.tenantId,
      { ...bounds, typeId: query.typeId, people: query.people, resources: query.resources, minutes: query.minutes },
      controller.signal,
    ).then(
      (slots) => {
        const times = [...new Set(slots.map((slot) => timeInZone(slot.starts_at, zone)))];
        setRead({ key, times });
      },
      () => { if (!controller.signal.aborted) setRead({ key, times: null }); },
    );
    return () => controller.abort();
    // `key` stands for the whole query and window; the objects behind it change identity each render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, scope.tenantId, zone]);

  if (!ready) return null;
  if (read?.key !== key) return <p role="status" className={mutedText}>Looking for free times…</p>;
  if (read.times === null) return <p className={mutedText}>Free times could not be loaded. You can still type a time.</p>;
  if (read.times.length === 0) return <p data-testid="free-times-none" className={mutedText}>No free times that day. You can still type a time.</p>;
  return (
    <div className="space-y-1">
      <p className={mutedText}>Free times</p>
      <StatusChips
        mode="single"
        testId="free-times"
        ariaLabel="Free times"
        value={time}
        onChange={(next) => onPick(Array.isArray(next) ? next[0] : next)}
        options={read.times.slice(0, MOST_OFFERED).map((value) => ({ value, label: value }))}
      />
    </div>
  );
}
