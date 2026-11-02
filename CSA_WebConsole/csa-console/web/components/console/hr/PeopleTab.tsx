"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import type { HrContext } from "@/lib/hr-api";
import { hrHref, type HrRoute, type PeopleView } from "@/lib/hr-paths";
import { emptyText, peopleFilters, STATUS_LABELS } from "@/lib/hr-people";
import {
  peopleClient, type Department, type OrgChart as OrgChartData, type PeopleQuery, type PeoplePage, type PersonStatus,
} from "@/lib/hr-people-api";
import StatusLabel, { type StatusTone } from "../fs/StatusLabel";
import { action, field, secondary, Notice, Panel } from "../ui";
import { Departments } from "./Departments";
import { OrgChart } from "./OrgChart";
import { PersonSelect, usePeopleOptions } from "./Pickers";

/** The three views this tab serves; the profile and Add person pages come with their own routes. */
export type PeopleListView = Extract<PeopleView, "list" | "org-chart" | "departments">;
export interface PeopleTabProps {
  tenant: string;
  context: HrContext;
  readonly: boolean;
  platform: boolean;
  view: PeopleListView;
}

const STATUS_TONES: Record<PersonStatus, StatusTone> = { active: "success", starting: "info", left: "neutral" };

/** The People tab: a switcher between the list, the org chart and (for editors) departments. */
export function PeopleTab({ tenant, context, readonly, platform, view }: PeopleTabProps) {
  const canEdit = context.client_owner || context.switches.includes("manage_people");
  const href = (route: HrRoute) => hrHref(route, platform, tenant);
  const views: { view: PeopleListView; label: string }[] = [
    { view: "list", label: "List" }, { view: "org-chart", label: "Org chart" },
    ...(canEdit ? [{ view: "departments" as const, label: "Departments" }] : []),
  ];
  return <div className="space-y-5">
    <nav aria-label="People views" className="flex flex-wrap gap-2">
      {views.map(item => <Link key={item.view} href={href({ tab: "people", view: item.view })}
        className={item.view === view ? action : secondary} aria-current={item.view === view ? "page" : undefined}>{item.label}</Link>)}
    </nav>
    {view === "list" && <PeopleList tenant={tenant} canEdit={canEdit} readonly={readonly} href={href} />}
    {view === "org-chart" && <OrgChartView tenant={tenant} canEdit={canEdit} owner={context.client_owner} href={href} />}
    {view === "departments" && (canEdit ? <Departments tenant={tenant} readonly={readonly} />
      : <Notice>Only the HR owner and people who manage people can change departments.{" "}
        <Link className="underline" href={href({ tab: "people", view: "list" })}>Go to the People list</Link></Notice>)}
  </div>;
}

type Href = (route: HrRoute) => string;
const profileRoute = (employeeId: string): HrRoute => ({ tab: "people", view: "profile", employeeId });

function OrgChartView({ tenant, canEdit, owner, href }: { tenant: string; canEdit: boolean; owner: boolean; href: Href }) {
  const people = useMemo(() => peopleClient(tenant), [tenant]);
  const [chart, setChart] = useState<OrgChartData | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let active = true;
    people.orgChart().then(result => { if (active) setChart(result); }).catch(() => { if (active) setFailed(true); });
    return () => { active = false; };
  }, [people]);
  if (failed) return <Notice error>We could not load the org chart. Reload the page and try again.</Notice>;
  if (!chart) return <Notice>Loading the org chart…</Notice>;
  return <Panel title="Org chart">
    {chart.nodes.length === 0 ? <p className="text-sm text-ink-500">{emptyText("org", canEdit)}</p>
      : <OrgChart chart={chart} owner={owner} hrefFor={id => href(profileRoute(id))} />}
  </Panel>;
}

/** The answer to one query: `page` is null when the read failed. */
type Answer = { query: PeopleQuery; page: PeoplePage | null };

function PeopleList({ tenant, canEdit, readonly, href }: { tenant: string; canEdit: boolean; readonly: boolean; href: Href }) {
  const people = useMemo(() => peopleClient(tenant), [tenant]);
  const managers = usePeopleOptions(tenant);
  const [query, setQuery] = useState<PeopleQuery>({});
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [moreBusy, setMoreBusy] = useState(false);
  const [moreFailed, setMoreFailed] = useState(false);

  useEffect(() => {
    let active = true;
    people.page(query)
      .then(page => { if (active) setAnswer({ query, page }); })
      .catch(() => { if (active) setAnswer({ query, page: null }); });
    return () => { active = false; };
  }, [people, query]);
  useEffect(() => {
    let active = true;
    // Without departments the filter still offers All and None.
    people.departments().then(result => { if (active) setDepartments(result.items); }).catch(() => undefined);
    return () => { active = false; };
  }, [people]);

  const current = answer?.query === query ? answer : null;
  const page = current?.page ?? null;
  const filtered = Object.keys(query).length > 0;
  // The manager picker falls back to a typed reference when the people read fails; a filter must not.
  const managerFilter = !managers.denied && !managers.failed;

  async function loadMore() {
    if (!page?.next_cursor || moreBusy) return;
    const asked = query;
    setMoreBusy(true); setMoreFailed(false);
    try {
      const next = await people.page(peopleFilters(asked, { cursor: page.next_cursor }));
      setAnswer(old => old && old.query === asked && old.page ? { query: asked, page: { ...next, items: [...old.page.items, ...next.items] } } : old);
    } catch { setMoreFailed(true); }
    finally { setMoreBusy(false); }
  }

  return <div className="space-y-5">
    <form className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4" role="search" aria-label="Find people"
      onChange={event => {
        const target = event.target;
        if (target instanceof HTMLSelectElement && ["department", "manager", "status"].includes(target.name))
          setQuery(old => peopleFilters(old, { [target.name]: target.value }));
      }}
      onSubmit={event => {
        event.preventDefault();
        const q = String(new FormData(event.currentTarget).get("q") ?? "");
        setQuery(old => peopleFilters(old, { q }));
      }}>
      <label className="block text-sm sm:col-span-2 lg:col-span-4">Search
        <span className="mt-1 flex gap-2">
          <input className={field} name="q" type="search" maxLength={120} placeholder="Name, job title or work email" />
          <button className={action}>Search people</button>
        </span></label>
      <label className="block text-sm">Department
        <select className={`${field} mt-1`} name="department" defaultValue="all">
          <option value="all">All departments</option><option value="none">No department</option>
          {departments.map(item => <option key={item.department_id} value={item.department_id}>
            {item.name}{item.archived ? " (archived)" : ""}</option>)}
        </select></label>
      {managerFilter && <PersonSelect people={managers} name="manager" label="Manager" noneLabel="Any manager" required={false} />}
      <label className="block text-sm">Status
        <select className={`${field} mt-1`} name="status" defaultValue="all">
          <option value="all">Active and starting</option>
          <option value="active">{STATUS_LABELS.active}</option><option value="starting">{STATUS_LABELS.starting}</option>
          {canEdit && <option value="left">{STATUS_LABELS.left}</option>}
        </select></label>
      {filtered && <div className="flex items-end">
        <button type="button" className={secondary} onClick={event => { event.currentTarget.form?.reset(); setQuery({}); }}>Clear search and filters</button>
      </div>}
    </form>
    <Panel title="People">
      {canEdit && !readonly && <Link className={`${action} mb-4`} href={href({ tab: "people", view: "new" })}>Add person</Link>}
      {!current ? <Notice>Loading people…</Notice>
        : !page ? <Notice error>We could not load the people. Reload the page and try again.</Notice>
          : page.items.length === 0
            ? <p className="text-sm text-ink-500">{filtered ? "No one matches. Clear the search or the filters." : emptyText("people", canEdit && !readonly)}</p>
            : <>
              <div className="overflow-x-auto"><table className="w-full text-left text-sm">
                <thead><tr>{["Name", "Title", "Department", "Manager", "Status"].map(label => <th className="p-2" key={label} scope="col">{label}</th>)}</tr></thead>
                <tbody>{page.items.map(person => <tr key={person.employee_id} className="border-t border-ink-200">
                  <td className="p-2"><Link className="font-medium underline" href={href(profileRoute(person.employee_id))}>{person.display_name}</Link></td>
                  <td className="p-2">{person.job_title ?? "—"}</td>
                  <td className="p-2">{person.department ?? "—"}</td>
                  <td className="p-2">{person.manager_name ?? "—"}</td>
                  <td className="p-2"><StatusLabel tone={STATUS_TONES[person.status]}>{STATUS_LABELS[person.status]}</StatusLabel></td>
                </tr>)}</tbody>
              </table></div>
              <p className="mt-3 text-sm text-ink-500" aria-live="polite">Showing {page.items.length} of {page.total}</p>
              {moreFailed && <Notice error>We could not load more people. Try again.</Notice>}
              {page.next_cursor && <button type="button" className={`${secondary} mt-3`} disabled={moreBusy} onClick={() => void loadMore()}>
                {moreBusy ? "Loading…" : "Load more"}</button>}
            </>}
    </Panel>
  </div>;
}
