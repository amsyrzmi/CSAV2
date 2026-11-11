"use client";

import Link from "next/link";
import type { CalendarStatus } from "@/lib/bookings-api";
import { CALENDAR_RECONNECT_BANNER, calendarControls, calendarStateWord } from "@/lib/bookings-view";
import { connectionsHref, MANAGE_LINK } from "@/lib/connections-view";
import { Notice, Panel } from "../ui";
import { ManageInConnections } from "../connections/ConnectionCard";
import { mutedText, type BookingsScope, type Loaded } from "./parts";

/** The banner's first sentence; its second ("Reconnect to start them again.") now points to the tab. */
const BANNER_FIRST = `${CALENDAR_RECONNECT_BANNER.split(". ")[0]}.`;

/**
 * A member's own Google Calendar on the Calendar page: where it stands in one line, and the way to the
 * Connections tab, where it is connected, reconnected and disconnected. Shown on the same rule as
 * the old panel (the feature is on and this member can connect); a connection Google stopped
 * accepting keeps its warning.
 */
export default function GoogleCalendarLine({ scope, calendar }: { scope: BookingsScope; calendar: Loaded<CalendarStatus> }) {
  const status = calendar.data;
  const controls = calendarControls(status);
  if (calendar.error && !status) {
    return <Notice error testId="google-calendar-load-error">{calendar.error}</Notice>;
  }
  if (!status || !controls.panel) return null;
  const href = connectionsHref(scope.tenantId !== null, scope.tenantId);
  return (
    <div data-testid="google-calendar" className="mb-4 space-y-3">
      {controls.reconnect && (
        <Notice warning testId="google-calendar-banner">
          {BANNER_FIRST} Reconnect it on Connections.{" "}
          <Link className="underline" href={href} data-testid="google-calendar-banner-link">{MANAGE_LINK}</Link>
        </Notice>
      )}
      <ManageInConnections testId="google-calendar-line" name="Google Calendar"
        word={calendarStateWord(status.status)} href={href} />
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
