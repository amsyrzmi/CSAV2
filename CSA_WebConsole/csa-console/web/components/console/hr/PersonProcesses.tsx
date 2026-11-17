"use client";
import Link from "next/link";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { hrClient } from "@/lib/hr-api";
import { hrHref } from "@/lib/hr-paths";
import { isDenied } from "@/lib/hr-pickers";
import {
  processClient, type PersonProcess, type PersonProcesses as Processes,
} from "@/lib/hr-process-api";
import { processErrorText, processLoadError, processSaveError } from "@/lib/hr-process-copy";
import {
  anchorDateRows, dueText, isDateText, noTemplateText, processKindLabel, processToday, progressText, statusLabel,
} from "@/lib/hr-process-view";
import StatusLabel from "../fs/StatusLabel";
import { action, field, secondary, Notice, Panel } from "../ui";
import { processTone } from "./ProcessList";
import { NOTICE_FOCUS } from "./TaskActions";

const card = "rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3";
const KINDS = ["onboarding", "offboarding"] as const;

/** `null` while loading; `hidden` when the read is not for this viewer (404). */
type Load = { data: Processes } | { hidden: true } | { failed: string } | null;

/**
 * A person's onboarding and offboarding: for the HR team, the person's manager and the person
 * themself, as much as the API returns to each (`view`). Per process the progress and the next
 * step; the dates the steps count from (HR and the person); for HR a link to each process, the
 * notice when no template matches and the probation end (`person.probation_set`). The manager and
 * the person never see notes or history: the API does not send them. Nothing shows for a viewer
 * the API does not serve, nor for a manager or the person while there is no process.
 */
export function PersonProcesses({ tenant, employeeId, version, readonly, platform, onChanged }: {
  tenant: string; employeeId: string;
  /** The person's people version: `expected_version` of the probation end. */
  version: number | null;
  readonly: boolean; platform: boolean;
  /** Read the person again after a change of theirs (the version moves). */
  onChanged: () => Promise<void>;
}) {
  // Memoised: the client is created once per tenant.
  const client = useMemo(() => processClient(tenant), [tenant]);
  const [load, setLoad] = useState<Load>(null);
  const [reads, setReads] = useState(0);
  const prefix = useId();
  useEffect(() => {
    let active = true;
    client.personProcesses(employeeId)
      .then(data => { if (active) setLoad({ data }); })
      .catch(cause => { if (active) setLoad(isDenied(cause) ? { hidden: true } : { failed: processLoadError(cause) }); });
    return () => { active = false; };
  }, [client, employeeId, reads]);
  const reread = useCallback(async () => { await onChanged(); setReads(count => count + 1); }, [onChanged]);

  if (!load || "hidden" in load) return null;
  const title = "Onboarding & offboarding";
  if ("failed" in load) return <div data-testid="hr-person-processes"><Panel title={title}>
    <div className="space-y-3">
      <Notice error>{load.failed}</Notice>
      <button type="button" className={secondary} onClick={() => setReads(count => count + 1)}>Try again</button>
    </div>
  </Panel></div>;
  const { data } = load;
  const hr = data.view === "hr";
  if (!hr && data.processes.length === 0) return null;
  const today = processToday();
  const dates = anchorDateRows(data.anchor_dates);
  const others = data.processes.filter(item => item.kind === "custom");
  const list = (items: PersonProcess[]) => <ul className="space-y-3">{items.map(item =>
    <ProcessItem key={item.process_id} item={item} today={today} titleId={`${prefix}-${item.process_id}`}
      href={hr ? hrHref({ tab: "processes", view: "detail", processId: item.process_id }, platform, tenant) : null} />)}</ul>;
  return <div data-testid="hr-person-processes"><Panel title={title}>
    <div className="space-y-5">
      {dates.length > 0 && <dl data-testid="hr-person-process-dates" className="grid gap-x-6 gap-y-2 text-sm sm:grid-cols-[max-content_1fr]">
        {dates.map(([label, value]) => <div key={label} className="contents">
          <dt className="text-[color:var(--fs-text-secondary)]">{label}</dt><dd className="font-medium">{value}</dd>
        </div>)}
      </dl>}
      {KINDS.map(kind => {
        const items = data.processes.filter(item => item.kind === kind);
        const headingId = `${prefix}-${kind}`;
        return <section key={kind} aria-labelledby={headingId} className="space-y-3">
          <h3 id={headingId} className="text-base font-semibold">{processKindLabel(kind)}</h3>
          {items.length > 0 ? list(items)
            : hr && data.no_matching_template?.[kind]
              ? <div data-testid={`hr-person-no-template-${kind}`}><Notice warning>
                {noTemplateText(kind)}
                {!readonly && <> <Link className="underline" href={hrHref({ tab: "processes", view: "new-template" }, platform, tenant)}>
                  Create a template</Link></>}
              </Notice></div>
              : <p className="text-sm text-[color:var(--fs-text-secondary)]">No {kind} yet.</p>}
        </section>;
      })}
      {others.length > 0 && <section aria-labelledby={`${prefix}-other`} className="space-y-3">
        <h3 id={`${prefix}-other`} className="text-base font-semibold">Other processes</h3>
        {list(others)}
      </section>}
      {hr && !readonly && <ProbationEnd tenant={tenant} employeeId={employeeId} version={version}
        current={data.anchor_dates?.probation_end ?? null} onSaved={reread} />}
    </div>
  </Panel></div>;
}

function ProcessItem({ item, today, titleId, href }: {
  item: PersonProcess; today: string; titleId: string; href: string | null;
}) {
  const next = item.status === "active" ? item.next_task : null;
  return <li data-testid={`hr-person-process-${item.process_id}`} className={`${card} space-y-2 text-sm`}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <p id={titleId} className="min-w-0 font-medium [overflow-wrap:anywhere]">{item.name ?? processKindLabel(item.kind)}</p>
      <StatusLabel tone={processTone(item.status)}>{statusLabel(item.status)}</StatusLabel>
    </div>
    <p>{progressText(item.tasks_done, item.tasks_total)}</p>
    {next && <div className="min-w-0">
      <p className="[overflow-wrap:anywhere]">Next: {next.title_en}</p>
      {next.title_ms && <p lang="ms" className="text-[color:var(--fs-text-secondary)] [overflow-wrap:anywhere]">{next.title_ms}</p>}
      <p className="text-[color:var(--fs-text-secondary)]">{dueText({ due_on: next.due_on, status: "todo" }, today)}</p>
    </div>}
    {href && <Link className="inline-block underline" href={href} aria-describedby={titleId}>Open process</Link>}
  </li>;
}

type Outcome = { message: string } | { error: string; stale: boolean };

/**
 * HR sets or clears the end of the person's probation with the person's version; a stale version
 * says so and offers Reload. Each answer moves focus to its notice, a repeated one too.
 */
function ProbationEnd({ tenant, employeeId, version, current, onSaved }: {
  tenant: string; employeeId: string; version: number | null; current: string | null; onSaved: () => Promise<void>;
}) {
  // Memoised: the client holds the Idempotency-Key of an uncertain write until it succeeds.
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const flight = useRef(false);
  const notice = useRef<HTMLDivElement>(null);
  const inputId = useId();
  // A new outcome object each time, so the same refusal twice still moves focus.
  useEffect(() => { if (outcome) notice.current?.focus(); }, [outcome]);

  async function save(end: string | null) {
    if (flight.current) return;
    if (end !== null && !isDateText(end)) {
      setOutcome({ error: current ? "Choose a date, or choose Clear." : "Choose a date.", stale: false });
      return;
    }
    // Without the person's version the save cannot be checked: read the person again first.
    if (version === null) { setOutcome({ error: processErrorText("person_changed"), stale: true }); return; }
    flight.current = true; setBusy(true); setOutcome(null);
    try {
      await client.operation({ operation: "person.probation_set", employee_id: employeeId, expected_version: version, probation_end: end });
    } catch (cause) {
      const stale = cause instanceof ApiError && cause.code === "person_changed";
      setOutcome({ error: processSaveError(cause, end === null ? "clear" : "save"), stale });
      flight.current = false; setBusy(false);
      return;
    }
    try { await onSaved(); } catch { /* The profile says when it could not reload. */ }
    flight.current = false; setBusy(false);
    setOutcome({ message: end === null ? "Probation end cleared." : "Probation end saved." });
  }

  async function reload() {
    if (flight.current) return;
    flight.current = true; setBusy(true);
    try { await onSaved(); } catch { /* As above. */ }
    flight.current = false; setBusy(false); setOutcome(null);
  }

  return <form className="space-y-3" aria-busy={busy} onSubmit={event => {
    event.preventDefault();
    void save(String(new FormData(event.currentTarget).get("probation_end") ?? "").trim());
  }}>
    {outcome && <div ref={notice} tabIndex={-1} data-testid="hr-probation-outcome" className={`space-y-2 ${NOTICE_FOCUS}`}>
      {"message" in outcome ? <Notice>{outcome.message}</Notice> : <Notice error>{outcome.error}</Notice>}
      {"stale" in outcome && outcome.stale && <button type="button" className={secondary} disabled={busy}
        onClick={() => void reload()}>Reload</button>}
    </div>}
    <div className="flex flex-wrap items-end gap-3">
      <div className="w-full sm:w-56">
        <label htmlFor={inputId} className="block text-sm">Probation end</label>
        <input key={current ?? ""} id={inputId} className={`${field} mt-1`} name="probation_end" type="date"
          defaultValue={current ?? ""} disabled={busy} />
      </div>
      <button className={action} disabled={busy}>Save</button>
      {current && <button type="button" className={secondary} disabled={busy} onClick={() => void save(null)}>Clear</button>}
    </div>
  </form>;
}
