"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { getCalendarStatus } from "@/lib/bookings-api";
import { calendarControls } from "@/lib/bookings-view";
import { calendarView, gmailNote, gmailView, type CardGate } from "@/lib/connections-view";
import { emailClient, type EmailContext } from "@/lib/email-api";
import { action, Notice, secondary } from "../ui";
import { mutedText, useLoaded } from "../bookings/parts";
import { useCalendarConnect } from "../bookings/useCalendarConnect";
import ConfirmAction from "../email/ConfirmAction";
import { errorFor, GMAIL_DISCONNECT_QUESTION, useGmailConnect } from "../email/useGmailConnect";
import { ConnectionCard, ConnectionRow } from "./ConnectionCard";

/**
 * The Google card of the Connections tab: the member's own Calendar and Gmail, one row each. A
 * locked row shows only its sentence and a read-only member's row a disabled Connect: neither reads
 * a status. `peopleHref` is People & rooms, linked from the Calendar row for an Admin.
 */
export function GoogleCard({ calendarGate, gmailGate, peopleHref }: {
  calendarGate: CardGate; gmailGate: CardGate; peopleHref: string;
}) {
  return <ConnectionCard testId="connections-card-google" title="Google">
    <CalendarRow gate={calendarGate} peopleHref={peopleHref} />
    <GmailRow gate={gmailGate} />
  </ConnectionCard>;
}

/** A row that reads nothing: its package is off, or the role may not connect. */
function UnreadRow({ kind, name, gate }: { kind: "calendar" | "gmail"; name: string; gate: CardGate }) {
  const testId = `connections-${kind}`;
  if (gate.locked) return <ConnectionRow testId={testId} name={name} view={null} locked={gate.locked} />;
  return <ConnectionRow testId={testId} name={name} view={null} note={gate.note}
    actions={<button className={action} type="button" disabled data-testid={`${testId}-connect`}>
      {kind === "calendar" ? "Connect Google Calendar" : "Connect Gmail"}</button>} />;
}

function CalendarRow({ gate, peopleHref }: { gate: CardGate; peopleHref: string }) {
  if (!gate.readStatus) return <UnreadRow kind="calendar" name="Google Calendar" gate={gate} />;
  return <CalendarStatusRow gate={gate} peopleHref={peopleHref} />;
}

/** The part of the row that reads the calendar status: the read and the connect calls live here only. */
function CalendarStatusRow({ gate, peopleHref }: { gate: CardGate; peopleHref: string }) {
  const calendar = useLoaded(() => getCalendarStatus(null));
  const { busy, error, connect, disconnect } = useCalendarConnect(null, calendar);
  const status = calendar.data;
  if (!status) {
    return <div data-testid="connections-calendar">
      <h4 className="font-medium">Google Calendar</h4>
      {calendar.error
        ? <Notice error testId="connections-calendar-error">{calendar.error}</Notice>
        : <p data-testid="connections-calendar-loading" className={mutedText}>Loading...</p>}
    </div>;
  }
  const controls = calendarControls(status);
  const off = busy || !gate.canAct;
  return <ConnectionRow testId="connections-calendar" name="Google Calendar" view={calendarView(status)}
    note={gate.note}
    actions={<>
      {controls.connect && <button className={action} type="button" disabled={off}
        data-testid="connections-calendar-connect" onClick={() => void connect()}>Connect Google Calendar</button>}
      {controls.reconnect && <button className={action} type="button" disabled={off}
        data-testid="connections-calendar-reconnect" onClick={() => void connect()}>Reconnect</button>}
      {controls.disconnect && <button className={secondary} type="button" disabled={off}
        data-testid="connections-calendar-disconnect" onClick={() => void disconnect()}>Disconnect</button>}
      {status.people && <Link className="text-sm underline" href={peopleHref}
        data-testid="connections-calendar-people">See your team&apos;s calendar connections</Link>}
      {error && <Notice error testId="connections-calendar-problem">{error}</Notice>}
    </>} />;
}

function GmailRow({ gate }: { gate: CardGate }) {
  if (!gate.readStatus) return <UnreadRow kind="gmail" name="Gmail" gate={gate} />;
  return <GmailStatusRow gate={gate} />;
}

/** The part of the row that reads the mailbox: the read and the connect calls live here only. */
function GmailStatusRow({ gate }: { gate: CardGate }) {
  const client = useMemo(() => emailClient(""), []);
  const [context, setContext] = useState<EmailContext | null>(null);
  const [failed, setFailed] = useState("");
  useEffect(() => {
    let live = true;
    client.context().then(
      (value) => { if (live) { setContext(value); setFailed(""); } },
      (cause: unknown) => { if (live) setFailed(errorFor(cause)); },
    );
    return () => { live = false; };
  }, [client]);
  const { busy, error, message, connect, disconnect } = useGmailConnect(client, async () => {
    setContext(await client.context());
  });
  if (!context) {
    return <div data-testid="connections-gmail">
      <h4 className="font-medium">Gmail</h4>
      {failed
        ? <Notice error testId="connections-gmail-error">{failed}</Notice>
        : <p data-testid="connections-gmail-loading" className={mutedText}>Loading...</p>}
    </div>;
  }
  const connected = context.mailbox?.status === "connected";
  const off = busy || !gate.canAct;
  return <ConnectionRow testId="connections-gmail" name="Gmail" view={gmailView(context)}
    note={gmailNote(context) ?? gate.note}
    actions={<>
      {context.can_connect && !connected && <button className={action} type="button"
        disabled={off || !context.provider_configured} data-testid="connections-gmail-connect"
        onClick={() => void connect()}>Connect Gmail</button>}
      {context.enabled && connected && <ConfirmAction testId="connections-gmail-disconnect" label="Disconnect"
        question={GMAIL_DISCONNECT_QUESTION} disabled={off} onConfirm={() => void disconnect()} />}
      {error && <Notice error testId="connections-gmail-problem">{error}</Notice>}
      {message && <Notice testId="connections-gmail-message">{message}</Notice>}
    </>} />;
}
