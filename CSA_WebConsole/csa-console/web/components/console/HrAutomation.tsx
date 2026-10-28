"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { hrClient, type HrContext } from "@/lib/hr-api";
import { HrAccessPage } from "./HrAccessPage";
import { HrAssistant } from "./HrAssistant";
import { action, field, secondary, Notice, Panel } from "./ui";

type Spec = { multiple?: boolean; name: string; label: string; type?: string; options?: [string, string][]; optional?: boolean; value?: string; max?: number };
type Employee = { employee_id: string; display_name: string; version: number; active: boolean; account_id: string | null;
  manager_id: string | null; job_title: string | null; department: string | null; work_email: string | null;
  region: string | null; legal_entity_reference: string | null; employment_category: string | null };
type Candidate = { id: string; display_name: string; version: number; retain_until: string; consent_id: string };
type Details = Candidate & { cv_text: string; consent_evidence: string; notes: { by: string; note: string; at: string }[] };
type Snapshot = { case_id: string; digest: string; status: string; version: number; payload: { currency: string; net_total: string;
  lines: { employee_id: string; net: string; amounts: { gross: string; pcb: string; employee_epf: string; employee_socso: string; employee_eis: string } }[] } };
type Calendar = { region: string; year: number; version: number };
type Balance = { employee_id: string; year: number; leave_type: string; version: number; remaining: string };
type Reminder = { id: string; kind: string; resource_id: string; read_at: string | null };
const regions: [string, string][] = [["peninsular_labuan", "Peninsular Malaysia / Labuan"], ["sabah", "Sabah"], ["sarawak", "Sarawak"]];
const value = (data: FormData, name: string) => String(data.get(name) ?? "").trim();
const maybe = (data: FormData, name: string) => value(data, name) || null;
const local = (input: string) => `${input}:00+08:00`;
const ref = (name: string, label: string): Spec => ({ name, label });
const regionField: Spec = { name: "region", label: "Workforce region", options: regions };
const dates: Spec[] = [{ name: "starts_on", label: "Period start", type: "date" }, { name: "ends_on", label: "Period end", type: "date" }];
function Form({ title, fields, disabled, save, children }: { title: string; fields: Spec[]; disabled: boolean;
  save: (data: FormData) => Promise<void>; children?: React.ReactNode }) {
  const submitting = useRef(false);
  const [localBusy, setLocalBusy] = useState(false);
  const [problem, setProblem] = useState("");
  return <Panel title={title}>{problem && <Notice error>{problem}</Notice>}<form onSubmit={event => {
    event.preventDefault(); if (submitting.current) return;
    const data = new FormData(event.currentTarget); submitting.current = true; setLocalBusy(true); setProblem("");
    void save(data).catch(cause => setProblem((cause as Error).message)).finally(() => { submitting.current = false; setLocalBusy(false); });
  }}>
    <fieldset disabled={disabled || localBusy} className="grid gap-4 sm:grid-cols-2">{fields.map(spec => <label className="block text-sm" key={spec.name}>{spec.label}
      {spec.options ? <select className={`${field} mt-1 ${spec.multiple ? "h-36" : ""}`} name={spec.name} required={!spec.optional} multiple={spec.multiple} size={spec.multiple ? spec.options.length : undefined} defaultValue={spec.multiple ? [] : spec.value ?? ""}>
        {!spec.multiple && <option value="">Choose…</option>}{spec.options.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select>
        : spec.type === "textarea" ? <textarea className={`${field} mt-1 h-32 py-2`} name={spec.name} maxLength={spec.max ?? 1200} required={!spec.optional} />
          : <input className={`${field} mt-1`} name={spec.name} type={spec.type ?? "text"} defaultValue={spec.value}
            maxLength={spec.max ?? 200} required={!spec.optional} step={spec.type === "number" ? "0.01" : undefined} accept={spec.type === "file" ? ".csv,text/csv" : undefined} />}
    </label>)}{children}<button className={action}>Save {title.toLowerCase()}</button></fieldset>
  </form></Panel>;
}

export function HrAutomation({ tenant, context, readonly }: { tenant: string; context: HrContext | null; readonly: boolean }) {
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const [section, setSection] = useState("assistant");
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [members, setMembers] = useState<{ user_id: string; display_name: string | null }[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [details, setDetails] = useState<Details | null>(null);
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [calendars, setCalendars] = useState<Calendar[]>([]);
  const [balances, setBalances] = useState<Balance[]>([]);
  const [appointments, setAppointments] = useState<{ id: string; starts_at: string; ends_at: string; status: string }[]>([]);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [editing, setEditing] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const flight = useRef(false);
  const newEmployee = useRef(crypto.randomUUID());
  const ruleId = useRef(crypto.randomUUID());
  const [liveContext, setLiveContext] = useState<HrContext | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const advancedRef = useRef<HTMLDetailsElement>(null);
  const effective = liveContext ?? context;
  const owner = effective?.client_owner ?? false;
  async function run(task: () => Promise<void>) {
    if (flight.current) return;
    flight.current = true; setBusy(true); setError(""); setMessage("");
    try { await task(); } catch (cause) { setError((cause as Error).message); }
    finally { flight.current = false; setBusy(false); }
  }
  async function load(target: string) {
    if (target === "setup") {
      const [people, accounts] = await Promise.all([client.records<Employee>("employees"), client.records<{ user_id: string; display_name: string | null }>("members")]);
      setEmployees(people.items); setMembers(accounts.items);
    }
    if (target === "recruitment") setCandidates((await client.records<Candidate>("candidates")).items);
    if (target === "attendance") {
      const [calendar, balance] = await Promise.all([client.records<Calendar>("calendars"), client.records<Balance>("balances")]);
      setCalendars(calendar.items); setBalances(balance.items);
    }
    if (target === "reminders") {
      const [notifications, interviews] = await Promise.all([client.records<Reminder>("notifications"), client.records<{ id: string; starts_at: string; ends_at: string; status: string }>("my_interviews")]);
      setReminders(notifications.items); setAppointments(interviews.items);
    }
  }
  async function save(body: unknown, after?: () => void) {
    await run(async () => {
      const receipt = await client.operation(body);
      after?.(); setMessage(`${receipt.status.replaceAll("_", " ")} · reference ${receipt.resource_id}`);
      try { setLiveContext(await client.context()); await load(section); } catch (cause) { setError(`Saved; refreshing failed: ${(cause as Error).message}`); }
    });
  }
  useEffect(() => {
    if (readonly) return;
    let active = true;
    const maintain = async () => {
      try { await client.operation({ operation: "automation.run" }); }
      catch (cause) { if (active) setError(`Local maintenance failed: ${(cause as Error).message}`); }
    };
    void maintain(); const timer = window.setInterval(() => void maintain(), 300000);
    return () => { active = false; window.clearInterval(timer); };
  }, [client, readonly]);
  const selected = employees.find(employee => employee.employee_id === editing);
  const employeeOptions: [string, string][] = employees.map(employee => [employee.employee_id, employee.display_name]);
  const memberOptions: [string, string][] = members.map(member => [member.user_id, member.display_name ?? member.user_id]);
  const candidateOptions: [string, string][] = candidates.map(candidate => [candidate.id, candidate.display_name]);
  return <div className="space-y-5"><nav className="flex flex-wrap gap-2" aria-label="HR tools">
    {[["assistant", "Assistant & voice"], ...(owner ? [["setup", "People & access"]] : []),
      ...(effective?.private_areas.includes("recruitment") ? [["recruitment", "Recruitment"]] : []),
      ["attendance", "Calendars & attendance"], ...(effective?.private_areas.includes("payroll") ? [["payroll", "Payroll review"]] : []),
      ["reminders", "Reminders"]].map(([id, label]) => <button key={id} className={section === id ? action : secondary} disabled={busy}
        onClick={() => { setSection(id); setDetails(null); setSnapshot(null); void run(() => load(id)); }}>{label}</button>)}
  </nav>{message && <Notice>{message}</Notice>}{error && <Notice error>{error}. Check saved records before retrying an uncertain write.</Notice>}
    {section === "assistant" && <HrAssistant tenant={tenant} readonly={readonly} />}
    {section === "setup" && owner && <>
      <HrAccessPage tenant={tenant} readonly={readonly} onAdvanced={() => {
        setAdvanced(true); window.requestAnimationFrame(() => advancedRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
      }} />
      <details ref={advancedRef} className="space-y-5" open={advanced} onToggle={event => setAdvanced(event.currentTarget.open)}>
      <summary className="cursor-pointer text-base font-semibold">Advanced</summary>
      <div className="mt-5 space-y-5">
      <label className="block text-sm">Employee to edit<select className={`${field} mt-1`} value={editing} onChange={event => setEditing(event.target.value)}>
        <option value="">Create employee</option>{employeeOptions.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
      <Form key={editing || "new"} title="Employee" disabled={busy || readonly} fields={[
        { ...ref("name", "Display name"), value: selected?.display_name },
        { ...ref("job_title", "Job title"), optional: true, value: selected?.job_title ?? "" },
        { ...ref("department", "Department"), optional: true, value: selected?.department ?? "" },
        { name: "email", label: "Work email", type: "email", optional: true, value: selected?.work_email ?? "" },
        { name: "manager", label: "Assigned manager", options: employeeOptions, optional: true, value: selected?.manager_id ?? "" },
        { name: "account", label: "Linked console member", options: memberOptions, optional: true, value: selected?.account_id ?? "" },
        { ...regionField, optional: true, value: selected?.region ?? "" },
        { ...ref("entity", "Legal entity (with region and category for payroll)"), optional: true, value: selected?.legal_entity_reference ?? "" },
        { ...ref("category", "Reviewed employment category"), optional: true, value: selected?.employment_category ?? "" },
        { name: "active", label: "Employee status", options: [["true", "Active"], ["false", "Inactive"]], value: selected?.active === false ? "false" : "true" },
      ]} save={data => save({ operation: "employee.save", employee_id: selected?.employee_id ?? newEmployee.current,
        expected_version: selected?.version ?? null, account_id: maybe(data, "account"), active: value(data, "active") === "true",
        region: maybe(data, "region"), legal_entity_reference: maybe(data, "entity"), employment_category: maybe(data, "category"),
        values: { display_name: value(data, "name"), job_title: maybe(data, "job_title"), department: maybe(data, "department"), work_email: maybe(data, "email"),
          assigned_manager: value(data, "manager") ? { tenant_id: tenant, employee_id: value(data, "manager") } : null } }, () => { newEmployee.current = crypto.randomUUID(); })} />
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
      </div>
      </details>
    </>}
    {section === "recruitment" && <>
      <Form title="Consented CV intake" disabled={busy || readonly} fields={[ref("name", "Candidate display name"), ref("evidence", "Recorded consent evidence reference"),
        { name: "retain", label: "Retention deadline (Malaysia time)", type: "datetime-local" }, { name: "cv", label: "CV text", type: "textarea", max: 12000 }]}
        save={data => save({ operation: "candidate.intake", display_name: value(data, "name"), consent_evidence: value(data, "evidence"), retain_until: local(value(data, "retain")), cv_text: value(data, "cv").replace(/[\r\n\t]+/g, " ") })} />
      <Panel title="Candidates"><div className="space-y-3">{candidates.map(candidate => <div key={candidate.id} className="flex flex-wrap items-center justify-between gap-3 border-b border-ink-200 py-3">
        <span>{candidate.display_name} · retain until {new Date(candidate.retain_until).toLocaleDateString()}</span><button className={secondary} disabled={busy} onClick={() => void run(async () => setDetails((await client.privateRecord<Details>("candidate", candidate.id)).items[0]))}>Open private CV</button>
      </div>)}</div></Panel>
      {details && <Panel title={`Private candidate: ${details.display_name}`}><p className="whitespace-pre-wrap text-sm">{details.cv_text}</p><p className="my-3 text-xs">Consent evidence: {details.consent_evidence}</p>
        <ul className="space-y-2 text-sm">{details.notes.map((note, index) => <li key={index}>{note.note} · {new Date(note.at).toLocaleString()}</li>)}</ul>
        <Form title="Human interview note" disabled={busy || readonly} fields={[{ name: "note", label: "Job-related evidence and human observations", type: "textarea" }]}
          save={data => save({ operation: "candidate.note", candidate_id: details.id, expected_version: details.version, note: value(data, "note").replace(/[\r\n]+/g, " ") }, () => setDetails(null))} />
        <details className="mt-4"><summary>Withdraw consent and erase this CV and notes</summary><button className={`${secondary} mt-3`} disabled={busy || readonly} onClick={() => void save({ operation: "candidate.erase", candidate_id: details.id, expected_version: details.version }, () => setDetails(null))}>Confirm erasure and cancel local interviews</button></details>
      </Panel>}
      <Form title="Cancel local interview" disabled={busy || readonly} fields={[ref("schedule", "Interview reference"), ref("reason", "Cancellation reason")]}
        save={data => save({ operation: "interview.cancel", schedule_id: value(data, "schedule"), reason: value(data, "reason") })} />
      <Form title="Local interview schedule" disabled={busy || readonly} fields={[{ name: "candidate", label: "Candidate", options: candidateOptions }, ref("interviewer", "Interviewer console member reference"),
        { name: "starts", label: "Start (Malaysia time)", type: "datetime-local" }, { name: "ends", label: "End (Malaysia time)", type: "datetime-local" },
        { name: "timezone", label: "Time zone", options: [["Asia/Kuala_Lumpur", "Kuala Lumpur"], ["Asia/Kuching", "Kuching"]] }]}
        save={data => save({ operation: "interview.schedule", candidate_id: value(data, "candidate"), interviewer_id: value(data, "interviewer"), starts_at: local(value(data, "starts")), ends_at: local(value(data, "ends")), timezone: value(data, "timezone") })}>
        <p className="text-sm sm:col-span-2">Creates a local appointment and reminder. External invitations require a configured calendar connection.</p>
      </Form>
    </>}
    {section === "attendance" && <>
      <Panel title="Reviewed leave balances"><div className="space-y-2 text-sm">{balances.map(balance => <p key={`${balance.employee_id}:${balance.year}:${balance.leave_type}`}>{balance.employee_id} · {balance.year} {balance.leave_type}: {balance.remaining} days remaining</p>)}</div></Panel>
      {owner && <><Form title="Work calendar" disabled={busy || readonly} fields={[regionField, { name: "year", label: "Year", type: "number" },
        { name: "weekdays", label: "Working weekdays", multiple: true, options: [["0", "Monday"], ["1", "Tuesday"], ["2", "Wednesday"], ["3", "Thursday"], ["4", "Friday"], ["5", "Saturday"], ["6", "Sunday"]] }, { ...ref("holidays", "Reviewed holiday dates, separated by commas (YYYY-MM-DD)"), optional: true }]}
        save={data => save({ operation: "calendar.save", region: value(data, "region"), year: Number(value(data, "year")), expected_version: calendars.find(calendar => calendar.region === value(data, "region") && calendar.year === Number(value(data, "year")))?.version ?? null,
          working_weekdays: data.getAll("weekdays").map(Number), holidays: value(data, "holidays").split(",").map(item => item.trim()).filter(Boolean) })} />
        <Form title="Leave allocation" disabled={busy || readonly} fields={[ref("employee", "Employee reference"), { name: "year", label: "Year", type: "number" },
          { name: "kind", label: "Leave type", options: [["annual", "Annual"], ["sick", "Sick"], ["unpaid", "Unpaid"], ["other", "Other"]] }, { name: "days", label: "Reviewed allocated days", type: "number" }, ref("policy", "Reviewed policy reference")]}
          save={data => save({ operation: "leave_balance.save", employee_id: value(data, "employee"), year: Number(value(data, "year")), leave_type: value(data, "kind"), days: value(data, "days"), policy_reference: value(data, "policy"),
            expected_version: balances.find(balance => balance.employee_id === value(data, "employee") && balance.year === Number(value(data, "year")) && balance.leave_type === value(data, "kind"))?.version ?? null })} /></>}
      {effective?.private_areas.includes("attendance") && <><Form title="Attendance import" disabled={busy || readonly} fields={[ref("employee", "Employee reference"), { name: "file", label: "Reviewed CSV (up to 20 records)", type: "file" }]}
        save={async data => {
          const file = data.get("file"); if (!(file instanceof File) || file.size > 64000) { setError("Choose a CSV smaller than 64 KB."); return; }
          const rows = (await file.text()).trim().split(/\r?\n/).slice(1).filter(Boolean);
          const records = rows.map(row => { const [starts_at, ends_at, region, timezone, reason] = row.split(",").map(item => item.trim()); return { domain: "attendance", starts_at, ends_at, region, timezone, reason }; });
          await save({ operation: "attendance.import", employee_id: value(data, "employee"), records });
        }}><p className="text-sm sm:col-span-2">Columns: starts_at, ends_at, region, timezone, reason. Use aware ISO timestamps. Imports remain pending manager review; the batch saves atomically.</p></Form>
        <Form title="Cancel shift" disabled={busy || readonly} fields={[ref("schedule", "Shift reference"), ref("reason", "Cancellation reason")]}
          save={data => save({ operation: "shift.cancel", schedule_id: value(data, "schedule"), reason: value(data, "reason") })} />
        <Form title="Shift schedule" disabled={busy || readonly} fields={[ref("employee", "Employee reference"), { name: "starts", label: "Start (Malaysia time)", type: "datetime-local" }, { name: "ends", label: "End (Malaysia time)", type: "datetime-local" }]}
          save={data => save({ operation: "shift.schedule", employee_id: value(data, "employee"), starts_at: local(value(data, "starts")), ends_at: local(value(data, "ends")) })} /></>}
    </>}
    {section === "payroll" && <>
      <Notice>Financial actions are manual. Contribution tables and employment categories need qualified review. PCB values come from a separately reviewed calculation. This workspace does not send payments or statutory filings.</Notice>
      <Form title="Reviewed contribution rules" disabled={busy || readonly} fields={[regionField, ref("entity", "Legal entity reference"), ref("category", "Employment category"),
        { name: "cycle", label: "Pay cycle", options: [["weekly", "Weekly"], ["fortnightly", "Fortnightly"], ["monthly", "Monthly"]] }, ...dates,
        ref("source", "Official source / reviewed version reference"), ref("qualification", "Reviewer qualification evidence"), { name: "file", label: "Reviewed contribution bands CSV", type: "file" }]}
        save={async data => {
          const file = data.get("file"); if (!(file instanceof File) || file.size > 200000) { setError("Choose a reviewed CSV smaller than 200 KB."); return; }
          const bands: Record<string, object[]> = { epf: [], socso: [], eis: [] };
          for (const row of (await file.text()).trim().split(/\r?\n/).slice(1).filter(Boolean)) {
            const [scheme, above, through, employee, employer] = row.split(",").map(item => item.trim());
            if (!Object.hasOwn(bands, scheme)) { setError("Each scheme must be epf, socso or eis."); return; }
            bands[scheme].push({ above, through, employee, employer });
          }
          await save({ operation: "payroll.rules", rule_id: ruleId.current, region: value(data, "region"), legal_entity_reference: value(data, "entity"), employment_category: value(data, "category"), cycle: value(data, "cycle"),
            effective_from: value(data, "starts_on"), effective_until: value(data, "ends_on"), source_reference: value(data, "source"), reviewer_qualification_reference: value(data, "qualification"), ...bands }, () => { ruleId.current = crypto.randomUUID(); });
        }}><p className="text-sm sm:col-span-2">CSV columns: scheme, above, through, employee, employer. Use contiguous wage bands and plain decimal amounts. Rules are immutable; save a new reviewed version for changes.</p></Form>
      <Form title="Payroll snapshot" disabled={busy || readonly} fields={[ref("case", "Payroll request reference (create under HR requests)"), { name: "file", label: "Reviewed payroll lines CSV", type: "file" }]}
        save={async data => {
          const file = data.get("file"); if (!(file instanceof File) || file.size > 64000) { setError("Choose a reviewed CSV smaller than 64 KB."); return; }
          const lines = (await file.text()).trim().split(/\r?\n/).slice(1).filter(Boolean).map(row => {
            const [employee_id, gross, epf_wage, socso_wage, eis_wage, reviewed_pcb, reviewed_other_deductions, tax_review_reference] = row.split(",").map(item => item.trim());
            return { employee_id, gross, epf_wage, socso_wage, eis_wage, reviewed_pcb, reviewed_other_deductions, tax_review_reference };
          }); await save({ operation: "payroll.snapshot", case_id: value(data, "case"), lines });
        }}><p className="text-sm sm:col-span-2">CSV columns: employee_id, gross, epf_wage, socso_wage, eis_wage, reviewed_pcb, reviewed_other_deductions, tax_review_reference.</p></Form>
      <Form title="Private payroll review" disabled={busy} fields={[ref("case", "Payroll request reference")]}
        save={data => run(async () => setSnapshot((await client.privateRecord<Snapshot>("payroll_snapshot", value(data, "case"))).items[0]))} />
      {snapshot && <Panel title={`Payroll ${snapshot.status}`}><p className="mb-4">MYR net total: {snapshot.payload.net_total}</p>
        <div className="overflow-x-auto"><table className="w-full text-left text-sm"><thead><tr>{["Employee", "Gross", "EPF", "SOCSO", "EIS", "PCB", "Net"].map(label => <th className="p-2" key={label}>{label}</th>)}</tr></thead><tbody>
          {snapshot.payload.lines.map(line => <tr key={line.employee_id}>{[line.employee_id, line.amounts.gross, line.amounts.employee_epf, line.amounts.employee_socso, line.amounts.employee_eis, line.amounts.pcb, line.net].map((item, index) => <td className="p-2" key={index}>{item}</td>)}</tr>)}
        </tbody></table></div>
        {snapshot.status === "prepared" && <button className={`${action} mt-4`} disabled={busy || readonly} onClick={() => void run(async () => {
          await client.decide(snapshot.case_id, { action: "approve", expected_version: 0, payroll_digest: snapshot.digest }); setSnapshot(null); setMessage("Exact payroll snapshot approved locally. No payment or filing was sent.");
        })}>Confirm reviewed amounts as a distinct reviewer</button>}
        {snapshot.status === "approved" && <Form title="Payroll reversal" disabled={busy || readonly} fields={[ref("reason", "Reason for reversing this local run")]}
          save={data => save({ operation: "payroll.reverse", case_id: snapshot.case_id, expected_version: snapshot.version, reason: value(data, "reason") }, () => setSnapshot(null))} />}
      </Panel>}
    </>}
    {section === "reminders" && <Panel title="Local HR reminders"><p className="mb-4 text-sm">Maintenance runs every five minutes while these tools are open. Reminders are deduplicated daily; expired candidate content is erased. No external messages are sent.</p>
      <button className={secondary} disabled={busy || readonly} onClick={() => void run(async () => { await client.operation({ operation: "automation.run" }); await load("reminders"); })}>Refresh reminders</button>
      <div className="mt-4 space-y-2">{appointments.map(appointment => <p key={appointment.id} className="text-sm">Interview {new Date(appointment.starts_at).toLocaleString()} – {new Date(appointment.ends_at).toLocaleTimeString()} · {appointment.status}</p>)}</div>
      <div className="mt-4 space-y-3">{reminders.map(reminder => <div key={reminder.id} className="flex flex-wrap justify-between gap-3 border-b border-ink-200 py-3 text-sm">
        <span>{reminder.kind.replaceAll("_", " ")} · {reminder.resource_id}</span>{!reminder.read_at && <button className={secondary} disabled={busy || readonly} onClick={() => void save({ operation: "notification.read", notification_id: reminder.id })}>Mark read</button>}
      </div>)}</div></Panel>}
  </div>;
}
