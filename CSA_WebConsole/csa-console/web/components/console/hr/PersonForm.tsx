"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { hrClient, type HrContext } from "@/lib/hr-api";
import { hrHref } from "@/lib/hr-paths";
import {
  departmentChoices, EMPLOYMENT_TYPES, managerChoices, NEW_DEPARTMENT, PEOPLE_COPY, personBody, personFormIssue,
  personFormValues, type PersonFormValues,
} from "@/lib/hr-people";
import { peopleClient, type Department, type Person } from "@/lib/hr-people-api";
import { action, field, secondary, Notice, Panel } from "../ui";
import { regions } from "./HrForm";
import { PersonSelect, usePeopleOptions } from "./Pickers";
import { peopleWriteError } from "./refusal";

type Problem = { text: string; code: string };

/**
 * Add person, or edit a person's Overview fields (HR owner and Manage people).
 *
 * An edit starts from every stored value and sends them all back (`person.update` replaces every
 * field), including a manager who has left and an archived department when they are unchanged. A
 * stale save keeps what was typed and offers Reload. Add keeps one employee id per form, so a retry
 * after an uncertain answer cannot add the person twice.
 */
export function PersonForm({ tenant, context, platform, mode, person, departments, onSaved, onReload, onCancel }: {
  tenant: string;
  context: HrContext;
  platform: boolean;
  mode: "add" | "update";
  person?: Person;
  departments: Department[];
  onSaved?: () => void;
  onReload?: () => void;
  onCancel?: () => void;
}) {
  const router = useRouter();
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const people = usePeopleOptions(tenant);
  const [employeeId] = useState(() => person?.employee_id ?? crypto.randomUUID());
  const initial = useMemo(() => personFormValues(person), [person]);
  const [department, setDepartment] = useState(initial.department_id);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const submitting = useRef(false);
  const choices = managerChoices({
    options: people.options, person: mode === "update" ? person : null,
    viewer: { owner: context.client_owner, employeeId: context.employee?.employee_id ?? null },
  });
  const managers = { ...people, options: choices.options };
  const departmentOptions = departmentChoices(departments, mode === "update" ? person : null);

  function read(form: HTMLFormElement): PersonFormValues {
    const data = new FormData(form);
    const text = (name: keyof PersonFormValues) => data.has(name) ? String(data.get(name) ?? "") : initial[name];
    return {
      display_name: text("display_name"), job_title: text("job_title"), start_date: text("start_date"),
      // A locked manager has no control, and a picker still loading offers only "Loading…": either
      // way the stored manager is sent back, never a blank that would clear it.
      manager_id: choices.locked || !people.loaded ? initial.manager_id : text("manager_id"),
      department_id: department, new_department: text("new_department"),
      work_email: text("work_email"), work_phone: text("work_phone"), employment_type: text("employment_type"),
      region: text("region"), legal_entity_reference: text("legal_entity_reference"),
      employment_category: text("employment_category"),
    };
  }

  async function submit(form: HTMLFormElement) {
    if (submitting.current) return;
    const values = read(form);
    const issue = personFormIssue(mode, values);
    if (issue) { setProblem({ text: issue, code: "" }); return; }
    submitting.current = true; setBusy(true); setProblem(null);
    try {
      await client.operation(personBody(mode, values, { employeeId, version: person?.version ?? undefined }));
    } catch (cause) {
      setProblem({ text: peopleWriteError(cause), code: cause instanceof ApiError ? cause.code : "" });
      return;
    } finally { submitting.current = false; setBusy(false); }
    if (mode === "add") router.push(hrHref({ tab: "people", view: "profile", employeeId }, platform, tenant));
    else onSaved?.();
  }

  const title = mode === "add" ? "Add person" : `Edit ${person?.display_name ?? "person"}`;
  return <Panel title={title}>
    <form onSubmit={event => { event.preventDefault(); void submit(event.currentTarget); }}>
      <fieldset disabled={busy} className="grid gap-4 sm:grid-cols-2">
        <label className="block text-sm">Name
          <input className={`${field} mt-1`} name="display_name" maxLength={120} required defaultValue={initial.display_name} /></label>
        <label className="block text-sm">Job title{mode === "update" && " (optional)"}
          <input className={`${field} mt-1`} name="job_title" maxLength={120} required={mode === "add"} defaultValue={initial.job_title} /></label>
        <label className="block text-sm">Start date{mode === "update" && " (optional)"}
          <input className={`${field} mt-1`} name="start_date" type="date" required={mode === "add"} defaultValue={initial.start_date} /></label>
        {choices.locked
          ? <div className="text-sm"><p>Manager</p>
            <p className="mt-1 font-medium">{person?.manager_name ?? PEOPLE_COPY.noManager}</p>
            <p className="mt-1 text-ink-500">{PEOPLE_COPY.refusals.own_manager_owner_only}</p></div>
          // Remounted when the people arrive: an uncontrolled select only picks its default on mount.
          : <PersonSelect key={people.loaded ? "loaded" : "loading"} people={managers} name="manager_id" label="Manager"
            noneLabel={PEOPLE_COPY.noManager} required={false} defaultValue={initial.manager_id} />}
        <label className="block text-sm">Department
          <select className={`${field} mt-1`} name="department_id" value={department} onChange={event => setDepartment(event.target.value)}>
            <option value="">No department</option>
            {departmentOptions.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            <option value={NEW_DEPARTMENT}>{PEOPLE_COPY.newDepartment}</option>
          </select></label>
        {department === NEW_DEPARTMENT && <label className="block text-sm">New department name
          <input className={`${field} mt-1`} name="new_department" maxLength={120} required autoFocus /></label>}
        <label className="block text-sm">Work email (optional)
          <input className={`${field} mt-1`} name="work_email" type="email" maxLength={254} defaultValue={initial.work_email} /></label>
        <label className="block text-sm">Work phone (optional)
          <input className={`${field} mt-1`} name="work_phone" type="tel" maxLength={40} pattern="[0-9+() \-]{3,40}"
            title="Use digits, spaces and + ( ) -" defaultValue={initial.work_phone} /></label>
        <label className="block text-sm">Employment type (optional)
          <select className={`${field} mt-1`} name="employment_type" defaultValue={initial.employment_type}>
            <option value="">Not set</option>
            {EMPLOYMENT_TYPES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select></label>
        <fieldset className="grid gap-4 sm:col-span-2 sm:grid-cols-3">
          <legend className="mb-2 text-sm font-medium">Employment details for payroll (optional; fill in all three or none)</legend>
          <label className="block text-sm">Region
            <select className={`${field} mt-1`} name="region" defaultValue={initial.region}>
              <option value="">Not set</option>
              {regions.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select></label>
          <label className="block text-sm">Legal entity
            <input className={`${field} mt-1`} name="legal_entity_reference" maxLength={100} defaultValue={initial.legal_entity_reference} /></label>
          <label className="block text-sm">Employment category
            <input className={`${field} mt-1`} name="employment_category" maxLength={100} defaultValue={initial.employment_category} /></label>
        </fieldset>
        {problem && <div className="space-y-2 sm:col-span-2">
          <Notice error>{problem.text}</Notice>
          {problem.code === "person_changed" && onReload && <button type="button" className={secondary} onClick={onReload}>Reload</button>}
          {problem.code === "employee_exists" && <Link className="text-sm underline"
            href={hrHref({ tab: "people", view: "profile", employeeId }, platform, tenant)}>Open their profile</Link>}
        </div>}
        <div className="flex flex-wrap gap-2 sm:col-span-2">
          <button className={action}>{mode === "add" ? "Add person" : "Save changes"}</button>
          {mode === "add"
            ? <Link className={secondary} href={hrHref({ tab: "people", view: "list" }, platform, tenant)}>Cancel</Link>
            : onCancel && <button type="button" className={secondary} onClick={onCancel}>Cancel</button>}
        </div>
      </fieldset>
    </form>
  </Panel>;
}

/** The Add person page: editors only, with the departments loaded for the picker. */
export function NewPerson({ tenant, context, readonly, platform }: {
  tenant: string; context: HrContext; readonly: boolean; platform: boolean;
}) {
  const canEdit = (context.client_owner || context.switches.includes("manage_people")) && !readonly;
  const [departments, setDepartments] = useState<Department[] | null>(null);
  useEffect(() => {
    if (!canEdit) return;
    let active = true;
    // Without the list the form still offers No department and New department….
    peopleClient(tenant).departments()
      .then(result => { if (active) setDepartments(result.items); })
      .catch(() => { if (active) setDepartments([]); });
    return () => { active = false; };
  }, [tenant, canEdit]);
  const list = hrHref({ tab: "people", view: "list" }, platform, tenant);
  if (!canEdit) return <Notice>Only the HR owner and people who manage people can add people.{" "}
    <Link className="underline" href={list}>Go to the People list</Link></Notice>;
  if (!departments) return <Notice>Loading the form…</Notice>;
  return <div className="space-y-4">
    <Link className="text-sm underline" href={list}>Back to People</Link>
    <PersonForm tenant={tenant} context={context} platform={platform} mode="add" departments={departments} />
  </div>;
}
