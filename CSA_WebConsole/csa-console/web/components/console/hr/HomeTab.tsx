"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { hrClient } from "@/lib/hr-api";
import type { Interview } from "@/lib/hr-people-api";
import { HrAssistant } from "../HrAssistant";
import { secondary, Notice, Panel } from "../ui";
import type { HrTabProps } from "./HrShell";
import { useHrRunner, useHrSave } from "./useHrRunner";

type Reminder = { id: string; kind: string; resource_id: string; read_at: string | null };
/** Local maintenance runs every five minutes while Home is open, as it did in the old HR tools. */
const MAINTENANCE_MS = 300000;

/**
 * Home. Task 8 adds "needs you today" and My HR above the reminders. Until 1b the separate HR chat
 * stays here, collapsed.
 */
export function HomeTab({ tenant, readonly, refreshContext }: HrTabProps) {
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const runner = useHrRunner();
  const { busy, error, message, run, setError } = runner;
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [appointments, setAppointments] = useState<Interview[]>([]);
  const load = useCallback(async () => {
    const [notifications, interviews] = await Promise.all([client.records<Reminder>("notifications"), client.records<Interview>("my_interviews")]);
    setReminders(notifications.items); setAppointments(interviews.items);
  }, [client]);
  const save = useHrSave(runner, client, load, refreshContext);
  useEffect(() => { void run(load); }, [run, load]);
  useEffect(() => {
    if (readonly) return;
    let active = true;
    const maintain = async () => {
      try { await client.operation({ operation: "automation.run" }); }
      catch (cause) { if (active) setError(`Local maintenance failed: ${(cause as Error).message}`); }
    };
    void maintain(); const timer = window.setInterval(() => void maintain(), MAINTENANCE_MS);
    return () => { active = false; window.clearInterval(timer); };
  }, [client, readonly, setError]);
  return <div className="space-y-5" aria-busy={busy}>
    {message && <Notice>{message}</Notice>}{error && <Notice error>{error}. Check saved records before retrying an uncertain write.</Notice>}
    <Panel title="Local HR reminders"><p className="mb-4 text-sm">Maintenance runs every five minutes while this page is open. Reminders are deduplicated daily; expired candidate content is erased. No external messages are sent.</p>
      <button className={secondary} disabled={busy || readonly} onClick={() => void run(async () => { await client.operation({ operation: "automation.run" }); await load(); })}>Refresh reminders</button>
      <div className="mt-4 space-y-2">{appointments.map(appointment => <p key={appointment.id} className="text-sm">Interview {new Date(appointment.starts_at).toLocaleString()} – {new Date(appointment.ends_at).toLocaleTimeString()} · {appointment.status}</p>)}</div>
      <div className="mt-4 space-y-3">{reminders.map(reminder => <div key={reminder.id} className="flex flex-wrap justify-between gap-3 border-b border-ink-200 py-3 text-sm">
        <span>{reminder.kind.replaceAll("_", " ")} · {reminder.resource_id}</span>{!reminder.read_at && <button className={secondary} disabled={busy || readonly} onClick={() => void save({ operation: "notification.read", notification_id: reminder.id })}>Mark read</button>}
      </div>)}</div></Panel>
    <details className="card rounded-[14px] p-5 sm:p-6">
      <summary className="cursor-pointer text-base font-semibold">HR chat (assistant & voice)</summary>
      <div className="mt-5"><HrAssistant tenant={tenant} readonly={readonly} /></div>
    </details>
  </div>;
}
