"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import type { CalendarStatus } from "@/lib/bookings-api";
import {
  CALENDAR_EXPLAINER, CALENDAR_RECONNECT_BANNER, calendarControls, calendarResult, calendarStateWord, type CalendarResult,
} from "@/lib/bookings-view";
import { action, Notice, Panel, secondary } from "../ui";
import { mutedText, type BookingsScope, type Loaded } from "./parts";
import { useCalendarConnect } from "./useCalendarConnect";

/**
 * What Google sent the browser back with, shown once and then taken out of the address so a reload
 * does not say it again. Other parts of the address (a platform admin's company) are kept.
 */
function useConnectionResult(): CalendarResult | null {
  const search = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  // Read once on arrival: taking it out of the address below must not take the sentence away.
  const [result] = useState<CalendarResult | null>(() => calendarResult(search));
  useEffect(() => {
    if (!search.has("calendar") && !search.has("calendar_error")) return;
    const rest = new URLSearchParams(search.toString());
    rest.delete("calendar");
    rest.delete("calendar_error");
    router.replace(rest.size ? `${pathname}?${rest}` : pathname, { scroll: false });
  }, [search, pathname, router]);
  return result;
}

/** A member's own Google Calendar: where it stands, and Connect, Reconnect and Disconnect. */
export default function GoogleCalendarPanel({ scope, calendar }: { scope: BookingsScope; calendar: Loaded<CalendarStatus> }) {
  const result = useConnectionResult();
  const { busy, error, connect, disconnect } = useCalendarConnect(scope.tenantId, calendar);
  const status = calendar.data;
  const controls = calendarControls(status);
  if (calendar.error && !status) {
    return <Notice error testId="google-calendar-load-error">{calendar.error}</Notice>;
  }
  if (!status || !controls.panel) {
    // A result can arrive for someone the feature no longer shows; it is still worth a sentence.
    return result ? <Notice error={result.tone === "error"} testId="google-calendar-result">{result.text}</Notice> : null;
  }

  return (
    <div data-testid="google-calendar" className="mb-4">
      <Panel title="Google Calendar">
        <div className="space-y-3">
          {result && (
            <Notice error={result.tone === "error"} testId="google-calendar-result">{result.text}</Notice>
          )}
          {controls.reconnect && (
            <Notice warning testId="google-calendar-banner">
              {CALENDAR_RECONNECT_BANNER}
            </Notice>
          )}
          <p className="text-sm">
            <span className="text-[color:var(--fs-text-secondary)]">Status: </span>
            <span data-testid="google-calendar-status" className="font-medium">{calendarStateWord(status.status)}</span>
          </p>
          <p className={mutedText}>
            {CALENDAR_EXPLAINER}
          </p>
          {error && <Notice error testId="google-calendar-error">{error}</Notice>}
          <div className="flex flex-wrap gap-2">
            {controls.connect && (
              <button type="button" className={action} disabled={busy} data-testid="google-calendar-connect"
                onClick={() => void connect()}>
                Connect Google Calendar
              </button>
            )}
            {controls.reconnect && (
              <button type="button" className={action} disabled={busy} data-testid="google-calendar-reconnect"
                onClick={() => void connect()}>
                Reconnect
              </button>
            )}
            {controls.disconnect && (
              <button type="button" className={secondary} disabled={busy} data-testid="google-calendar-disconnect"
                onClick={() => void disconnect()}>
                Disconnect
              </button>
            )}
          </div>
        </div>
      </Panel>
    </div>
  );
}

/** For an Admin: each person with an account and where their connection stands, as a status only. */
export function GoogleCalendarPeople({ calendar }: { calendar: Loaded<CalendarStatus> }) {
  const people = calendar.data?.available ? calendar.data.people : null;
  if (!people) return null;
  return (
    <div data-testid="google-calendar-people" className="mt-4">
      <Panel title="Google Calendar">
        {people.length === 0 ? (
          <p className={mutedText}>Nobody on the people list has an account that can connect yet.</p>
        ) : (
          <ul className="divide-y divide-[color:var(--fs-border)]">
            {people.map((person) => (
              <li key={person.person_id} className="flex flex-wrap items-center justify-between gap-2 py-2 text-sm">
                <span className="min-w-0 [overflow-wrap:anywhere]">{person.name}</span>
                <span data-testid={`google-calendar-person-${person.person_id}`} className={mutedText}>
                  {calendarStateWord(person.status)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}
