"use client";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { hrClient } from "@/lib/hr-api";
import { hrHref, type HrRoute } from "@/lib/hr-paths";
import { emptyText, homeItems, homeQuickActions, runsHomeMaintenance } from "@/lib/hr-people";
import { peopleClient, type HrHome, type Interview, type Person } from "@/lib/hr-people-api";
import { isDenied } from "@/lib/hr-pickers";
import { HrAssistant } from "../HrAssistant";
import { secondary, Notice, Panel } from "../ui";
import type { HrTabProps } from "./HrShell";
import { MyHr } from "./MyHr";
import { useHrRunner, useHrSave } from "./useHrRunner";

type Reminder = { id: string; kind: string; resource_id: string; read_at: string | null };
/** For the HR owner, local maintenance runs every five minutes while Home is open, as it did in the old HR tools. */
const MAINTENANCE_MS = 300000;

/**
 * Home. Staff with HR duties get "Needs you today", quick actions and the reminders; everyone else
 * gets My HR. Until 1b the separate HR chat stays here, collapsed.
 */
export function HomeTab(props: HrTabProps) {
  const { tenant, context, readonly, platform } = props;
  return <div className="space-y-5">
    {context.hr_duties ? <HrHomePage {...props} />
      : <MyHr tenant={tenant} context={context} readonly={readonly} platform={platform} />}
    <details className="card rounded-[14px] p-5 sm:p-6">
      <summary className="cursor-pointer text-base font-semibold">HR chat (assistant & voice)</summary>
      <div className="mt-5"><HrAssistant tenant={tenant} readonly={readonly} /></div>
    </details>
  </div>;
}

/** What "Needs you today" is built from, besides the reminders and interviews Home already reads. */
type Today = { home: HrHome; starting: Person[] } | { failed: true } | null;

function HrHomePage({ tenant, context, readonly, platform, refreshContext }: HrTabProps) {
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const people = useMemo(() => peopleClient(tenant), [tenant]);
  const runner = useHrRunner();
  const { busy, error, message, run, setError } = runner;
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [appointments, setAppointments] = useState<Interview[]>([]);
  const [today, setToday] = useState<Today>(null);
  const load = useCallback(async () => {
    const [notifications, interviews] = await Promise.all([client.records<Reminder>("notifications"), client.records<Interview>("my_interviews")]);
    setReminders(notifications.items); setAppointments(interviews.items);
  }, [client]);
  const save = useHrSave(runner, client, load, refreshContext);
  useEffect(() => { void run(load); }, [run, load]);
  useEffect(() => {
    let active = true;
    // A viewer the People list does not serve gets a 404: no starters, and no error.
    const starting = people.page({ status: "starting" }).then(page => page.items)
      .catch(cause => { if (isDenied(cause)) return []; throw cause; });
    Promise.all([people.home(), starting])
      .then(([home, list]) => { if (active) setToday({ home, starting: list }); })
      .catch(() => { if (active) setToday({ failed: true }); });
    return () => { active = false; };
  }, [people]);
  const maintains = runsHomeMaintenance(context, readonly);
  useEffect(() => {
    if (!maintains) return;
    let active = true;
    const maintain = async () => {
      try { await client.operation({ operation: "automation.run" }); }
      catch (cause) { if (active) setError(`Local maintenance failed: ${(cause as Error).message}`); }
    };
    void maintain(); const timer = window.setInterval(() => void maintain(), MAINTENANCE_MS);
    return () => { active = false; window.clearInterval(timer); };
  }, [client, maintains, setError]);
  const unread = reminders.filter(reminder => !reminder.read_at).length;
  const items = useMemo(() => today && "home" in today
    ? homeItems({ home: today.home, interviews: appointments, unread, starting: today.starting, leaving: [], now: new Date() })
    : [], [today, appointments, unread]);
  const quick = homeQuickActions(context, readonly);
  const href = (route: HrRoute) => hrHref(route, platform, tenant);
  return <div className="space-y-5" aria-busy={busy}>
    <Panel title="Needs you today">
      {!today ? <Notice>Loading what needs you today…</Notice>
        : "failed" in today ? <Notice error>We could not load what needs you today. Reload the page and try again.</Notice>
          : items.length === 0 ? <p className="text-sm text-ink-500">{emptyText("home")}</p>
            : <ul className="space-y-2 text-sm">{items.map(item => <li key={item.key}>
              <Link className="underline" href={href(item.href)}>{item.label}</Link></li>)}</ul>}
    </Panel>
    {quick.length > 0 && <nav aria-label="Quick actions" className="flex flex-wrap gap-2">
      {quick.map(item => <Link key={item.key} className={secondary} href={href(item.href)}>{item.label}</Link>)}
    </nav>}
    {message && <Notice>{message}</Notice>}{error && <Notice error>{error}. Check saved records before retrying an uncertain write.</Notice>}
    <Panel title="Local HR reminders"><p className="mb-4 text-sm">{maintains && "Maintenance runs every five minutes while this page is open. "}Reminders are deduplicated daily; expired candidate content is erased. No external messages are sent.</p>
      <button className={secondary} disabled={busy || readonly} onClick={() => void run(async () => { if (maintains) await client.operation({ operation: "automation.run" }); await load(); })}>Refresh reminders</button>
      <div className="mt-4 space-y-2">{appointments.map(appointment => <p key={appointment.id} className="text-sm">Interview {new Date(appointment.starts_at).toLocaleString()} – {new Date(appointment.ends_at).toLocaleTimeString()} · {appointment.status}</p>)}</div>
      <div className="mt-4 space-y-3">{reminders.map(reminder => <div key={reminder.id} className="flex flex-wrap justify-between gap-3 border-b border-ink-200 py-3 text-sm">
        <span>{reminder.kind.replaceAll("_", " ")} · {reminder.resource_id}</span>{!reminder.read_at && <button className={secondary} disabled={busy || readonly} onClick={() => void save({ operation: "notification.read", notification_id: reminder.id })}>Mark read</button>}
      </div>)}</div></Panel>
  </div>;
}
