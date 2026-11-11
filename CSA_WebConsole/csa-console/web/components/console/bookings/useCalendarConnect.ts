"use client";

import { disconnectCalendar, startCalendarConnect, type CalendarStatus } from "@/lib/bookings-api";
import { useAction, type Loaded } from "./parts";

export const DISCONNECT_CONFIRM =
  "Stop sending bookings to your Google Calendar? Your Bookings calendar and its events stay in your Google account.";

/**
 * Connect, Reconnect and Disconnect for the signed-in member's own Google Calendar, for the
 * Connections tab (the Calendar page only links there). `tenantId` is null for a member (the session names
 * the company). Connect and Reconnect end by leaving for Google; Disconnect asks first and then
 * reads the status again through `calendar.reload`.
 */
export function useCalendarConnect(tenantId: string | null, calendar: Loaded<CalendarStatus>) {
  const { busy, error, run } = useAction();

  async function connect() {
    const started = await run(() => startCalendarConnect(tenantId));
    if (started) window.location.assign(started.url);
  }

  async function disconnect() {
    if (!window.confirm(DISCONNECT_CONFIRM)) return;
    const done = await run(async () => {
      await disconnectCalendar(tenantId);
      return true;
    });
    if (done) calendar.reload();
  }

  return { busy, error, connect, disconnect };
}
