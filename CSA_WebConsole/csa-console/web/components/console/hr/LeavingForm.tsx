"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { hrClient } from "@/lib/hr-api";
import { klDate, PEOPLE_COPY } from "@/lib/hr-people";
import { peopleClient, type LeavingReason, type OrgChart, type Person } from "@/lib/hr-people-api";
import {
  leaveBody, leaveFormIssue, leavingErrorText, leaveUndoBody, RECORDS_COPY, rehireBody, rehireFormIssue, reportsMoveChoices,
} from "@/lib/hr-records";
import { action, field, secondary, Notice, Panel } from "../ui";
import { PersonSelect, usePeopleOptions } from "./Pickers";

type Problem = { text: string; code: string };
type ChartState = { chart: OrgChart | null } | null;

const failureOf = (cause: unknown) => cause instanceof ApiError ? { status: cause.status, code: cause.code } : null;

/** A problem line, with Reload when someone else changed the person first. */
function Problems({ problem, onReload }: { problem: Problem | null; onReload?: () => void }) {
  if (!problem) return null;
  return <div className="space-y-2 sm:col-span-2">
    <Notice error>{problem.text}</Notice>
    {problem.code === "person_changed" && onReload && <button type="button" className={secondary} onClick={onReload}>Reload</button>}
  </div>;
}

/**
 * Mark a person as leaving: last day, reason (and a note for Other), and, when they have reports, who
 * their reports move to (their own manager unless changed). One write at a time; the form keeps its
 * Idempotency-Key through the HR client, so choosing the button again after a lost answer is safe.
 */
export function LeavingForm({ tenant, person, onDone, onReload, onCancel }: {
  tenant: string; person: Person; onDone: () => void; onReload: () => void; onCancel: () => void;
}) {
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const people = usePeopleOptions(tenant);
  const [chartState, setChartState] = useState<ChartState>(null);
  const [reason, setReason] = useState<LeavingReason>("resigned");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const submitting = useRef(false);
  const today = klDate(new Date());

  useEffect(() => {
    let active = true;
    // Without the chart the form still asks where reports go, and the server decides.
    peopleClient(tenant).orgChart()
      .then(chart => { if (active) setChartState({ chart }); })
      .catch(() => { if (active) setChartState({ chart: null }); });
    return () => { active = false; };
  }, [tenant]);

  const ready = chartState !== null && people.loaded;
  const move = reportsMoveChoices({ options: people.options, chart: chartState?.chart ?? null, person });
  // A typed reference stands in when the people read failed, so the stored manager is its starting value.
  const typed = people.denied || people.failed;
  const defaultId = typed ? person.manager_id ?? "" : move.defaultId;

  async function submit(form: HTMLFormElement) {
    if (submitting.current || !ready) return;
    const data = new FormData(form);
    const input = {
      lastDay: String(data.get("last_day") ?? ""), reason, note: String(data.get("note") ?? ""),
    };
    const issue = leaveFormIssue(input, today);
    if (issue) { setProblem({ text: issue, code: "" }); return; }
    submitting.current = true; setBusy(true); setProblem(null);
    try {
      await client.operation(leaveBody({
        employeeId: person.employee_id, version: person.version ?? undefined, ...input,
        reportsNewManager: move.hasReports ? String(data.get("reports_new_manager") ?? "") : null,
      }));
    } catch (cause) {
      const failure = failureOf(cause);
      setProblem({ text: leavingErrorText(failure, "leave"), code: failure?.code ?? "" });
      return;
    } finally { submitting.current = false; setBusy(false); }
    onDone();
  }

  return <Panel title={`Mark ${person.display_name} as leaving`}>
    {!ready ? <Notice>Loading the form…</Notice>
      : <form onSubmit={event => { event.preventDefault(); void submit(event.currentTarget); }}>
        <fieldset disabled={busy} className="grid gap-4 sm:grid-cols-2">
          <label className="block text-sm">Last day
            <input className={`${field} mt-1`} name="last_day" type="date" min={today} required /></label>
          <label className="block text-sm">Reason
            <select className={`${field} mt-1`} name="reason" value={reason}
              onChange={event => setReason(event.target.value as LeavingReason)}>
              {RECORDS_COPY.reasons.map(([id, label]) => <option key={id} value={id}>{label}</option>)}
            </select></label>
          {reason === "other" && <label className="block text-sm sm:col-span-2">What is the reason?
            <input className={`${field} mt-1`} name="note" maxLength={200} required autoFocus /></label>}
          {move.hasReports && <div className="sm:col-span-2">
            <PersonSelect people={{ ...people, options: move.options }} name="reports_new_manager" label="Their reports move to"
              noneLabel={PEOPLE_COPY.noManager} required={false} defaultValue={defaultId} />
            <p className="mt-1 text-sm text-ink-500">Their reports move today, so no one is left without a manager.</p>
          </div>}
          <Problems problem={problem} onReload={onReload} />
          <div className="flex flex-wrap gap-2 sm:col-span-2">
            <button className={action}>Mark as leaving</button>
            <button type="button" className={secondary} onClick={onCancel}>Cancel</button>
          </div>
        </fieldset>
      </form>}
  </Panel>;
}

/** Rehire someone who has left, from a start date. Console access is a separate step in Settings. */
export function RehireForm({ tenant, person, onDone, onReload, onCancel }: {
  tenant: string; person: Person; onDone: (message: string) => void; onReload: () => void; onCancel: () => void;
}) {
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const submitting = useRef(false);

  async function submit(form: HTMLFormElement) {
    if (submitting.current) return;
    const startDate = String(new FormData(form).get("start_date") ?? "");
    const issue = rehireFormIssue(startDate);
    if (issue) { setProblem({ text: issue, code: "" }); return; }
    submitting.current = true; setBusy(true); setProblem(null);
    try {
      await client.operation(rehireBody({ employeeId: person.employee_id, version: person.version ?? undefined, startDate }));
    } catch (cause) {
      const failure = failureOf(cause);
      setProblem({ text: leavingErrorText(failure, "rehire"), code: failure?.code ?? "" });
      return;
    } finally { submitting.current = false; setBusy(false); }
    onDone("Rehired. Invite them again from Settings to give console access.");
  }

  return <Panel title={`Rehire ${person.display_name}`}>
    <form onSubmit={event => { event.preventDefault(); void submit(event.currentTarget); }}>
      <fieldset disabled={busy} className="grid gap-4 sm:grid-cols-2">
        <label className="block text-sm">Start date
          <input className={`${field} mt-1`} name="start_date" type="date" required defaultValue={klDate(new Date())} /></label>
        <Problems problem={problem} onReload={onReload} />
        <div className="flex flex-wrap gap-2 sm:col-span-2">
          <button className={action}>Rehire</button>
          <button type="button" className={secondary} onClick={onCancel}>Cancel</button>
        </div>
      </fieldset>
    </form>
  </Panel>;
}

/** Undo a leaving date while the last day has not passed. */
export function UndoLeaving({ tenant, person, onDone, onReload }: {
  tenant: string; person: Person; onDone: (message: string) => void; onReload: () => void;
}) {
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);
  const submitting = useRef(false);

  async function undo() {
    if (submitting.current) return;
    submitting.current = true; setBusy(true); setProblem(null);
    try {
      await client.operation(leaveUndoBody({ employeeId: person.employee_id, version: person.version ?? undefined }));
    } catch (cause) {
      const failure = failureOf(cause);
      setProblem({ text: leavingErrorText(failure, "undo"), code: failure?.code ?? "" });
      return;
    } finally { submitting.current = false; setBusy(false); }
    onDone("Leaving undone. They stay on.");
  }

  return <div className="mt-5 space-y-2">
    <button type="button" className={secondary} disabled={busy} onClick={() => void undo()}>Undo leaving</button>
    <Problems problem={problem} onReload={onReload} />
  </div>;
}
