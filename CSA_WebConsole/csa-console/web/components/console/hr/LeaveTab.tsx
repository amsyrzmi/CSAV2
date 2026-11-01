"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { hrClient } from "@/lib/hr-api";
import { scheduledOptions } from "@/lib/hr-pickers";
import { Notice, Panel } from "../ui";
import { Form, local, ref, regions, value } from "./HrForm";
import type { HrTabProps } from "./HrShell";
import { PersonSelect, usePeopleOptions } from "./Pickers";
import { useHrRunner, useHrSave } from "./useHrRunner";

type Calendar = { region: string; year: number; version: number; payload: { holidays: string[] } };
type Balance = { employee_id: string; year: number; leave_type: string; version: number; remaining: string };
type Shift = { id: string; employee_id: string; starts_at: string; status: string };
const regionNames = new Map(regions);

/** Leave & attendance: balances, work calendars, leave allocation (HR owner), attendance import and shifts. */
export function LeaveTab({ tenant, context, readonly, refreshContext }: HrTabProps) {
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const runner = useHrRunner();
  const { busy, error, message, run } = runner;
  const people = usePeopleOptions(tenant);
  const owner = context.client_owner;
  const attendance = context.private_areas.includes("attendance");
  const [calendars, setCalendars] = useState<Calendar[]>([]);
  const [balances, setBalances] = useState<Balance[]>([]);
  const [shifts, setShifts] = useState<Shift[]>([]);
  const load = useCallback(async () => {
    const [calendar, balance, shift] = await Promise.all([client.records<Calendar>("calendars"), client.records<Balance>("balances"),
      attendance ? client.records<Shift>("shifts") : Promise.resolve({ items: [] as Shift[] })]);
    setCalendars(calendar.items); setBalances(balance.items); setShifts(shift.items);
  }, [client, attendance]);
  const save = useHrSave(runner, client, load, refreshContext);
  useEffect(() => { void run(load); }, [run, load]);
  const names = new Map(people.options);
  const shiftOptions = scheduledOptions(shifts, item => names.get(item.employee_id) ?? "Shift");
  return <div className="space-y-5" aria-busy={busy}>
    {message && <Notice>{message}</Notice>}{error && <Notice error>{error}. Check saved records before retrying an uncertain write.</Notice>}
    <Panel title="Reviewed leave balances"><div className="space-y-2 text-sm">{balances.map(balance => <p key={`${balance.employee_id}:${balance.year}:${balance.leave_type}`}>{names.get(balance.employee_id) ?? balance.employee_id} · {balance.year} {balance.leave_type}: {balance.remaining} days remaining</p>)}</div></Panel>
    <Panel title="Work calendars"><div className="space-y-2 text-sm">{calendars.length === 0 && <p>No work calendars yet. The HR owner adds them in Settings.</p>}
      {calendars.map(calendar => <p key={`${calendar.region}:${calendar.year}`}>{regionNames.get(calendar.region) ?? calendar.region} · {calendar.year} · {calendar.payload.holidays.length} {calendar.payload.holidays.length === 1 ? "holiday" : "holidays"}</p>)}</div></Panel>
    {owner && <Form title="Leave allocation" disabled={busy || readonly} fields={[<PersonSelect key="employee" people={people} name="employee" label="Employee" />, { name: "year", label: "Year", type: "number" },
      { name: "kind", label: "Leave type", options: [["annual", "Annual"], ["sick", "Sick"], ["unpaid", "Unpaid"], ["other", "Other"]] }, { name: "days", label: "Reviewed allocated days", type: "number" }, ref("policy", "Reviewed policy reference")]}
      save={data => save({ operation: "leave_balance.save", employee_id: value(data, "employee"), year: Number(value(data, "year")), leave_type: value(data, "kind"), days: value(data, "days"), policy_reference: value(data, "policy"),
        expected_version: balances.find(balance => balance.employee_id === value(data, "employee") && balance.year === Number(value(data, "year")) && balance.leave_type === value(data, "kind"))?.version ?? null })} />}
    {attendance && <><Form title="Attendance import" disabled={busy || readonly} fields={[<PersonSelect key="employee" people={people} name="employee" label="Employee" />, { name: "file", label: "Reviewed CSV (up to 20 records)", type: "file" }]}
      save={async data => {
        const file = data.get("file"); if (!(file instanceof File) || file.size > 64000) { runner.setError("Choose a CSV smaller than 64 KB."); return; }
        const rows = (await file.text()).trim().split(/\r?\n/).slice(1).filter(Boolean);
        const records = rows.map(row => { const [starts_at, ends_at, region, timezone, reason] = row.split(",").map(item => item.trim()); return { domain: "attendance", starts_at, ends_at, region, timezone, reason }; });
        await save({ operation: "attendance.import", employee_id: value(data, "employee"), records });
      }}><p className="text-sm sm:col-span-2">Columns: starts_at, ends_at, region, timezone, reason. Use aware ISO timestamps. Imports remain pending manager review; the batch saves atomically.</p></Form>
      <Form title="Cancel shift" disabled={busy || readonly} fields={[{ name: "schedule", label: "Shift", options: shiftOptions }, ref("reason", "Cancellation reason")]}
        save={data => save({ operation: "shift.cancel", schedule_id: value(data, "schedule"), reason: value(data, "reason") })} />
      <Form title="Shift schedule" disabled={busy || readonly} fields={[<PersonSelect key="employee" people={people} name="employee" label="Employee" />, { name: "starts", label: "Start (Malaysia time)", type: "datetime-local" }, { name: "ends", label: "End (Malaysia time)", type: "datetime-local" }]}
        save={data => save({ operation: "shift.schedule", employee_id: value(data, "employee"), starts_at: local(value(data, "starts")), ends_at: local(value(data, "ends")) })} /></>}
  </div>;
}
