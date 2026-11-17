"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { hrHref } from "@/lib/hr-paths";
import { peopleClient, type Department } from "@/lib/hr-people-api";
import { processClient, type ProcessFilters, type ProcessKind, type ProcessRow, type ProcessStatus } from "@/lib/hr-process-api";
import { processLoadError } from "@/lib/hr-process-copy";
import { dueText, processKindLabel, processToday, progressText, statusLabel } from "@/lib/hr-process-view";
import StatusLabel, { type StatusTone } from "../fs/StatusLabel";
import { action, field, secondary, Notice, Panel } from "../ui";
import { PersonSelect, usePeopleOptions } from "./Pickers";
import { StartProcessForm } from "./StartProcessForm";

const card = "rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3";
const STATUS_TONES: Record<ProcessStatus, StatusTone> = { active: "info", completed: "success", cancelled: "neutral" };
export const processTone = (status: ProcessStatus) => Object.hasOwn(STATUS_TONES, status) ? STATUS_TONES[status] : "neutral";
const KINDS: ProcessKind[] = ["onboarding", "offboarding", "custom"];
const STATUSES: ProcessStatus[] = ["active", "completed", "cancelled"];
/** The list opens on the processes in progress. */
const DEFAULT: ProcessFilters = { status: "active" };

/** The answer to one set of filters: `failed` when the read failed. */
type Answer = { filters: ProcessFilters; items: ProcessRow[]; next: string | null } | { filters: ProcessFilters; failed: string };

function readFilters(form: HTMLFormElement): ProcessFilters {
  const data = new FormData(form);
  const text = (name: string) => String(data.get(name) ?? "");
  return {
    kind: text("kind") as ProcessKind | "",
    status: text("status") as ProcessStatus | "",
    overdue: data.get("overdue") === "on",
    department: text("department"),
    owner: text("owner"),
  };
}

/**
 * The HR team's processes: filters (kind, status, overdue only, department, a step's owner), newest
 * first with "Load more" along the API's keyset, a table from `md` and cards below it. Each row opens
 * its process. "Start a process" opens the start form (not for read-only members).
 */
export function ProcessList({ tenant, readonly, platform }: { tenant: string; readonly: boolean; platform: boolean }) {
  // Memoised: the client holds the Idempotency-Key of an uncertain start until it succeeds.
  const client = useMemo(() => processClient(tenant), [tenant]);
  const router = useRouter();
  const people = usePeopleOptions(tenant);
  const [filters, setFilters] = useState<ProcessFilters>(DEFAULT);
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [reads, setReads] = useState(0);
  const [departments, setDepartments] = useState<Department[]>([]);
  const [more, setMore] = useState({ busy: false, error: "" });
  const [starting, setStarting] = useState(false);
  useEffect(() => {
    let active = true;
    client.processes(filters)
      .then(page => { if (active) setAnswer({ filters, items: page.items, next: page.next_cursor }); })
      .catch(cause => { if (active) setAnswer({ filters, failed: processLoadError(cause) }); });
    return () => { active = false; };
  }, [client, filters, reads]);
  useEffect(() => {
    let active = true;
    // Without departments the filter still offers "Any department".
    peopleClient(tenant).departments().then(result => { if (active) setDepartments(result.items); }).catch(() => undefined);
    return () => { active = false; };
  }, [tenant]);

  const current = answer?.filters === filters ? answer : null;
  const filtered = Boolean(filters.kind || filters.status !== DEFAULT.status || filters.overdue || filters.department
    || filters.owner);
  // The owner picker falls back to a typed reference when the people read fails; a filter must not.
  const ownerFilter = !people.denied && !people.failed;
  const choose = (next: ProcessFilters) => { setMore({ busy: false, error: "" }); setFilters(next); };
  const loadMore = async () => {
    if (!current || "failed" in current || !current.next || more.busy) return;
    const asked = current;
    setMore({ busy: true, error: "" });
    try {
      const page = await client.processes(asked.filters, asked.next ?? undefined);
      setAnswer(old => old === asked ? { ...asked, items: [...asked.items, ...page.items], next: page.next_cursor } : old);
      setMore({ busy: false, error: "" });
    } catch (cause) { setMore({ busy: false, error: processLoadError(cause) }); }
  };
  const today = processToday();
  const detailHref = (processId: string) => hrHref({ tab: "processes", view: "detail", processId }, platform, tenant);

  return <div className="space-y-5" data-testid="hr-process-list">
    <form className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" role="search" aria-label="Filter processes"
      onSubmit={event => event.preventDefault()}
      onChange={event => choose(readFilters(event.currentTarget))}>
      <label className="block text-sm">Kind
        <select className={`${field} mt-1`} name="kind" defaultValue="">
          <option value="">Any kind</option>
          {KINDS.map(kind => <option key={kind} value={kind}>{processKindLabel(kind)}</option>)}
        </select></label>
      <label className="block text-sm">Status
        <select className={`${field} mt-1`} name="status" defaultValue={DEFAULT.status}>
          <option value="">Any status</option>
          {STATUSES.map(status => <option key={status} value={status}>{statusLabel(status)}</option>)}
        </select></label>
      <label className="block text-sm">Department
        <select className={`${field} mt-1`} name="department" defaultValue="">
          <option value="">Any department</option>
          {departments.map(item => <option key={item.department_id} value={item.department_id}>
            {item.name}{item.archived ? " (archived)" : ""}</option>)}
        </select></label>
      {ownerFilter && <PersonSelect people={people} name="owner" label="Step owner" noneLabel="Anyone" required={false} />}
      <label className="flex min-h-10 items-center gap-2 self-end text-sm pointer-coarse:min-h-11">
        <input type="checkbox" name="overdue" className="h-4 w-4" />Overdue only</label>
      {filtered && <div className="flex items-end">
        <button type="button" className={secondary}
          onClick={event => { event.currentTarget.form?.reset(); choose(DEFAULT); }}>Clear filters</button>
      </div>}
    </form>
    <Panel title="Processes">
      {!readonly && <button type="button" className={`${action} mb-4`} onClick={() => setStarting(true)}>Start a process</button>}
      {!current ? <Notice>Loading processes…</Notice>
        : "failed" in current ? <div className="space-y-3">
          <Notice error>{current.failed}</Notice>
          <button type="button" className={secondary} onClick={() => setReads(count => count + 1)}>Try again</button>
        </div>
          : current.items.length === 0 ? <div className="space-y-3">
            <p className="text-sm text-ink-500">No processes match.</p>
            {!filtered && !readonly && <p className="text-sm text-ink-500">
              A process starts when you add a person or mark someone as leaving, or when you start one here.</p>}
          </div>
            : <>
              <div className="overflow-x-auto"><table data-testid="hr-process-table" className="hidden w-full text-left text-sm md:table">
                <thead><tr>{["Person", "Process", "Progress", "Next due", "Status"].map(label =>
                  <th className="p-2" key={label} scope="col">{label}</th>)}</tr></thead>
                <tbody>{current.items.map(row => <tr key={row.process_id} data-testid={`hr-process-row-${row.process_id}`}
                  className="border-t border-ink-200 align-top">
                  <td className="p-2 [overflow-wrap:anywhere]"><Link className="font-medium underline" href={detailHref(row.process_id)}>
                    {row.employee_name}</Link></td>
                  <td className="p-2 [overflow-wrap:anywhere]"><ProcessName row={row} /></td>
                  <td className="p-2">{progressText(row.tasks_done, row.tasks_total)}</td>
                  <td className="p-2"><NextDue row={row} today={today} /></td>
                  <td className="p-2"><Status row={row} /></td>
                </tr>)}</tbody>
              </table>
              <ul data-testid="hr-process-cards" className="space-y-3 md:hidden">{current.items.map(row =>
                <li key={row.process_id} data-testid={`hr-process-card-${row.process_id}`} className={card}>
                  <Link className="font-medium underline [overflow-wrap:anywhere]" href={detailHref(row.process_id)}>{row.employee_name}</Link>
                  <dl className="mt-2 space-y-1 text-sm">
                    <div className="flex justify-between gap-3"><dt className="text-[color:var(--fs-text-secondary)]">Process</dt>
                      <dd className="min-w-0 text-right [overflow-wrap:anywhere]"><ProcessName row={row} /></dd></div>
                    <div className="flex justify-between gap-3"><dt className="text-[color:var(--fs-text-secondary)]">Progress</dt>
                      <dd className="min-w-0 text-right">{progressText(row.tasks_done, row.tasks_total)}</dd></div>
                    <div className="flex justify-between gap-3"><dt className="text-[color:var(--fs-text-secondary)]">Next due</dt>
                      <dd className="min-w-0 text-right"><NextDue row={row} today={today} /></dd></div>
                    <div className="flex items-center justify-between gap-3"><dt className="text-[color:var(--fs-text-secondary)]">Status</dt>
                      <dd className="min-w-0 text-right"><Status row={row} /></dd></div>
                  </dl>
                </li>)}</ul></div>
              {more.error && <Notice error>{more.error}</Notice>}
              {current.next && <button type="button" className={`${secondary} mt-3`} disabled={more.busy}
                onClick={() => void loadMore()}>{more.busy ? "Loading…" : "Load more"}</button>}
            </>}
    </Panel>
    {starting && !readonly && <StartProcessForm client={client} people={people} onClose={() => setStarting(false)}
      onStarted={processId => router.push(detailHref(processId))} />}
  </div>;
}

function ProcessName({ row }: { row: ProcessRow }) {
  return <>{processKindLabel(row.kind)}
    {row.name && <span className="block text-[color:var(--fs-text-secondary)]">{row.name}</span>}</>;
}

function NextDue({ row, today }: { row: ProcessRow; today: string }) {
  if (row.status !== "active") return <>—</>;
  return <span className={row.overdue ? "font-medium text-[color:var(--fs-danger)]" : undefined}>
    {dueText({ due_on: row.next_due_on, status: "todo" }, today)}</span>;
}

function Status({ row }: { row: ProcessRow }) {
  return <span className="inline-flex flex-wrap justify-end gap-1">
    <StatusLabel tone={processTone(row.status)}>{statusLabel(row.status)}</StatusLabel>
    {row.overdue && <StatusLabel tone="error">Overdue</StatusLabel>}
  </span>;
}
