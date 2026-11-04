"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { localToday } from "@/lib/hr-guide";
import { hrClient, type HrContext } from "@/lib/hr-api";
import { hrHref } from "@/lib/hr-paths";
import {
  departmentChoices, employmentProfileIssue, EMPLOYMENT_TYPES, managerChoices, NEW_DEPARTMENT, PEOPLE_COPY, personBody, personFormIssue,
  personFormValues, personSaveError, type PersonFormValues,
} from "@/lib/hr-people";
import { peopleClient, type Department, type Person } from "@/lib/hr-people-api";
import { DetailsRow } from "../kit";
import { action, field, secondary, Notice, Panel } from "../ui";
import { regions } from "./HrForm";
import { PersonSelect, usePeopleOptions } from "./Pickers";

type Problem = { text: string; code: string };

/** The fields kept behind "More details", and the three inside "Payroll details" (their form names). */
const PAYROLL_FIELDS = ["region", "legal_entity_reference", "employment_category"];
const MORE_FIELDS = ["work_email", "work_phone", "employment_type", ...PAYROLL_FIELDS];

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
  // Step 2 stays folded unless an edit already has something in it. The form always holds every field
  // (a folded section is only hidden), so what is sent is the same either way.
  const [moreOpen, setMoreOpen] = useState(() => mode === "update" && MORE_FIELDS.some(name => initial[name as keyof PersonFormValues].trim() !== ""));
  const [payrollOpen, setPayrollOpen] = useState(() => PAYROLL_FIELDS.some(name => initial[name as keyof PersonFormValues].trim() !== ""));
  const [today] = useState(localToday);
  const formRef = useRef<HTMLFormElement>(null);
  const pendingFocus = useRef<string | null>(null);
  const choices = managerChoices({
    options: people.options, person: mode === "update" ? person : null,
    viewer: { owner: context.client_owner, employeeId: context.employee?.employee_id ?? null },
  });
  const managers = { ...people, options: choices.options };
  const departmentOptions = departmentChoices(departments, mode === "update" ? person : null);

  /** Opens the folded section holding this field and queues focus for it once it is visible. */
  function reveal(name: string) {
    const payroll = PAYROLL_FIELDS.includes(name);
    if (!payroll && !MORE_FIELDS.includes(name)) return false;
    if (moreOpen && (!payroll || payrollOpen)) return false;
    setMoreOpen(true); if (payroll) setPayrollOpen(true);
    pendingFocus.current = name;
    return true;
  }
  useEffect(() => {
    const name = pendingFocus.current;
    if (!name) return;
    pendingFocus.current = null;
    const control = formRef.current?.elements.namedItem(name);
    if (!(control instanceof HTMLElement)) return;
    control.focus();
    if (control instanceof HTMLInputElement && !control.validity.valid) control.reportValidity();
  });
  /** The browser cannot focus a hidden field, so the first invalid one in a folded section opens it. */
  function onInvalid(event: React.FormEvent<HTMLFormElement>) {
    const target = event.target as HTMLInputElement;
    const first = Array.from(event.currentTarget.elements).find(item => {
      const control = item as HTMLInputElement; return control.willValidate && !control.validity.valid;
    });
    if (target === first && reveal(target.name)) event.preventDefault();
  }

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
    if (issue) {
      setProblem({ text: issue, code: "" });
      // The payroll all-or-none check is the one rule that can fail inside the folded sections.
      if (issue === employmentProfileIssue(values)) reveal(PAYROLL_FIELDS.find(name => values[name as keyof PersonFormValues].trim() === "") ?? PAYROLL_FIELDS[0]);
      return;
    }
    submitting.current = true; setBusy(true); setProblem(null);
    try {
      await client.operation(personBody(mode, values, { employeeId, version: person?.version ?? undefined }));
    } catch (cause) {
      const failure = cause instanceof ApiError ? { status: cause.status, code: cause.code } : null;
      setProblem({ text: personSaveError(mode, failure), code: failure?.code ?? "" });
      return;
    } finally { submitting.current = false; setBusy(false); }
    if (mode === "add") router.push(hrHref({ tab: "people", view: "profile", employeeId }, platform, tenant));
    else onSaved?.();
  }

  const title = mode === "add" ? "Add person" : `Edit ${person?.display_name ?? "person"}`;
  return <Panel title={title}>
    <form ref={formRef} onInvalidCapture={onInvalid}
      onSubmit={event => { event.preventDefault(); void submit(event.currentTarget); }}>
      <fieldset disabled={busy} className="grid gap-4 sm:grid-cols-2">
        <label className="block text-sm">Name
          <input className={`${field} mt-1`} name="display_name" maxLength={120} required defaultValue={initial.display_name} /></label>
        <label className="block text-sm">Job title{mode === "update" && " (optional)"}
          <input className={`${field} mt-1`} name="job_title" maxLength={120} required={mode === "add"} defaultValue={initial.job_title} /></label>
        <label className="block text-sm">Start date{mode === "update" && " (optional)"}
          <input className={`${field} mt-1`} name="start_date" type="date" required={mode === "add"} defaultValue={initial.start_date || (mode === "add" ? today : "")} /></label>
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
        <div className="sm:col-span-2">
        <DetailsRow testId="hr-person-more" toggleLabel="contact and employment" open={moreOpen} onOpenChange={setMoreOpen} summary={<>
          <span className="block text-sm font-semibold">More details</span>
          <span className="block text-sm text-[color:var(--fs-text-secondary)]">Work email, phone, employment type and payroll details. All optional.</span>
        </>}><div className="grid gap-4 sm:grid-cols-2">
        <label className="block text-sm">Work email (optional)
          <input className={`${field} mt-1`} name="work_email" type="email" maxLength={254} defaultValue={initial.work_email} /></label>
        <label className="block text-sm">Work phone (optional)
          <input className={`${field} mt-1`} name="work_phone" type="tel" maxLength={40} pattern="[0-9+\(\) \-]{3,40}"
            title="Use digits, spaces and + ( ) -" defaultValue={initial.work_phone} /></label>
        <label className="block text-sm">Employment type (optional)
          <select className={`${field} mt-1`} name="employment_type" defaultValue={initial.employment_type}>
            <option value="">Not set</option>
            {EMPLOYMENT_TYPES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
          </select></label>
        <div className="sm:col-span-2">
        <DetailsRow testId="hr-person-payroll" toggleLabel="payroll" open={payrollOpen} onOpenChange={setPayrollOpen} summary={<>
          <span className="block text-sm font-semibold">Payroll details</span>
          <span className="block text-sm text-[color:var(--fs-text-secondary)]">Region, legal entity and employment category.</span>
        </>}>
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
        </DetailsRow>
        </div>
        </div></DetailsRow>
        </div>
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
