"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { hrClient } from "@/lib/hr-api";
import { HrAccessPage } from "../HrAccessPage";
import { Notice } from "../ui";
import { Form, ref, regionField, value } from "./HrForm";
import type { HrTabProps } from "./HrShell";
import { useMemberOptions } from "./Pickers";
import { useHrRunner, useHrSave } from "./useHrRunner";

type Calendar = { region: string; year: number; version: number };
const weekdays: [string, string][] = [["0", "Monday"], ["1", "Tuesday"], ["2", "Wednesday"], ["3", "Thursday"], ["4", "Friday"], ["5", "Saturday"], ["6", "Sunday"]];

/**
 * Settings (HR owner): the access page with company features, invitations and recent changes, then the
 * work calendar, background maintenance and private access. The raw access profile form sits under Advanced.
 */
export function SettingsTab({ tenant, readonly, refreshContext }: HrTabProps) {
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const runner = useHrRunner();
  const { busy, error, message, run, setError } = runner;
  const members = useMemberOptions(tenant, "members");
  const memberOptions = members.options;
  const [calendars, setCalendars] = useState<Calendar[]>([]);
  const [advanced, setAdvanced] = useState(false);
  const advancedRef = useRef<HTMLDetailsElement>(null);
  const load = useCallback(async () => { setCalendars((await client.records<Calendar>("calendars")).items); }, [client]);
  const save = useHrSave(runner, client, load, refreshContext);
  useEffect(() => { void run(load); }, [run, load]);
  return <div className="space-y-5" aria-busy={busy}>
    <HrAccessPage tenant={tenant} readonly={readonly} onAdvanced={() => {
      setAdvanced(true); window.requestAnimationFrame(() => advancedRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    }} />
    {message && <Notice>{message}</Notice>}{error && <Notice error>{error}. Check saved records before retrying an uncertain write.</Notice>}
    <Form title="Work calendar" disabled={busy || readonly} fields={[regionField, { name: "year", label: "Year", type: "number" },
      { name: "weekdays", label: "Working weekdays", multiple: true, options: weekdays }, { ...ref("holidays", "Reviewed holiday dates, separated by commas (YYYY-MM-DD)"), optional: true }]}
      save={data => save({ operation: "calendar.save", region: value(data, "region"), year: Number(value(data, "year")), expected_version: calendars.find(calendar => calendar.region === value(data, "region") && calendar.year === Number(value(data, "year")))?.version ?? null,
        working_weekdays: data.getAll("weekdays").map(Number), holidays: value(data, "holidays").split(",").map(item => item.trim()).filter(Boolean) })} />
    <Form title="Background maintenance" disabled={busy || readonly} fields={[
      { name: "enabled", label: "Retention and reminders", options: [["true", "Enable for this tenant"], ["false", "Disable background maintenance"]] },
      { name: "hours", label: "Remind after pending hours", type: "number", value: "24" }]}
      save={data => save({ operation: "automation.configure", enabled: value(data, "enabled") === "true", remind_after_hours: Number(value(data, "hours")) })}>
      <p className="text-sm sm:col-span-2">Requires the separately configured maintenance worker. It can erase expired candidate content and create local reminders; it cannot make HR decisions or send external messages.</p>
    </Form>
    <Form title="Private access" disabled={busy || readonly} fields={[{ name: "user", label: "Console member", options: memberOptions },
      { name: "area", label: "Private capability", options: [["recruitment", "CV intake, notes and interviews"], ["attendance", "Attendance import and shifts"], ["payroll", "Private payroll records"]] },
      { name: "enabled", label: "Access decision", options: [["true", "Grant explicitly"], ["false", "Revoke"]] }]}
      save={data => save({ operation: "private_access.save", user_id: value(data, "user"), area: value(data, "area"), enabled: value(data, "enabled") === "true" })} />
    <details ref={advancedRef} className="space-y-5" open={advanced} onToggle={event => setAdvanced(event.currentTarget.open)}>
      <summary className="cursor-pointer text-base font-semibold">Advanced</summary>
      <div className="mt-5 space-y-5">
        <Form title="Access profile" disabled={busy || readonly} fields={[{ name: "user", label: "Console member", options: memberOptions },
          { name: "profile", multiple: true, label: "Replace action grants with these selected profiles", options: [["self", "Employee self service"], ["manager", "Assigned manager"], ["hr", "HR reader and recruitment preparation"], ["payroll_operator", "Payroll preparer"], ["payroll_reviewer", "Payroll reviewer"], ["owner", "Recorded client-owner decisions"], ["none", "Remove action grants"]] }]} save={data => {
            const profiles = data.getAll("profile").map(String);
            if (profiles.includes("none") && profiles.length > 1) { setError("Choose Remove action grants alone to clear access."); return Promise.resolve(); }
            const fields = ["display_name", "job_title", "department", "work_email", "assigned_manager"];
            const grants: object[] = []; const workflows: object[] = [];
            const grant = (permission: string, scope: string) => grants.push({ action: permission, scope, fields: permission === "directory.read" ? fields : [] });
            const workflow = (domain: string, permission: string, scope: string) => workflows.push({ domain, action: permission, scope });
            for (const profile of profiles) {
            if (["self", "manager", "hr"].includes(profile)) {
              const scope = profile === "self" ? "self" : profile === "manager" ? "assigned_manager" : "hr_operator";
              grant("directory.read", scope); grant("training.read", scope);
            }
            if (profile === "self") {
              for (const permission of ["training.draft", "training.submit", "training.withdraw"]) grant(permission, "self");
              for (const domain of ["attendance", "leave"]) for (const permission of ["read", "prepare"]) workflow(domain, permission, "self");
            }
            if (profile === "manager") { grant("training.decide", "assigned_manager"); for (const domain of ["attendance", "leave"]) for (const permission of ["read", "decide"]) workflow(domain, permission, "assigned_manager"); }
            if (profile === "hr") { for (const permission of ["read", "prepare"]) workflow("recruitment", permission, "hr_operator"); for (const domain of ["attendance", "leave"]) workflow(domain, "read", "hr_operator"); }
            if (profile === "payroll_operator") for (const permission of ["read", "prepare"]) workflow("payroll", permission, "payroll_operator");
            if (profile === "payroll_reviewer") workflow("payroll", "decide", "payroll_reviewer");
            if (profile === "owner") { grant("training.decide", "client_owner"); for (const domain of ["recruitment", "attendance", "leave"]) workflow(domain, "decide", "client_owner"); }
            }
            return save({ operation: "access.save", user_id: value(data, "user"),
              grants: Array.from(new Map(grants.map(grant => [JSON.stringify(grant), grant])).values()),
              workflows: Array.from(new Map(workflows.map(grant => [JSON.stringify(grant), grant])).values()) });
          }}><p className="text-sm sm:col-span-2">This replaces action grants. It does not create a client-owner assignment or grant private CV/payroll access.</p></Form>
      </div>
    </details>
  </div>;
}
