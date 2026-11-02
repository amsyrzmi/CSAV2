"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { hrClient } from "@/lib/hr-api";
import { scheduledOptions } from "@/lib/hr-pickers";
import { secondary, Notice, Panel } from "../ui";
import { Form, local, ref, value } from "./HrForm";
import type { HrTabProps } from "./HrShell";
import { MemberSelect, useMemberOptions } from "./Pickers";
import { RequestsPanel } from "./RequestsPanel";
import { useHrRunner, useHrSave } from "./useHrRunner";

type Candidate = { id: string; display_name: string; version: number; retain_until: string; consent_id: string };
type Details = Candidate & { cv_text: string; consent_evidence: string; notes: { by: string; note: string; at: string }[] };
type Interview = { id: string; candidate_id: string; starts_at: string; status: string };

/**
 * Recruitment: the private CV and interview tools for people with the recruitment area. Someone with
 * recruitment workflow grants but no area gets the recruitment requests instead.
 */
export function RecruitmentTab(props: HrTabProps) {
  if (!props.context.private_areas.includes("recruitment"))
    return <RequestsPanel {...props} domains={["recruitment"]} />;
  return <PrivateRecruitment {...props} />;
}

function PrivateRecruitment({ tenant, readonly, refreshContext }: HrTabProps) {
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const runner = useHrRunner();
  const { busy, error, message, run } = runner;
  const interviewers = useMemberOptions(tenant, "interviewers");
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [interviews, setInterviews] = useState<Interview[]>([]);
  const [details, setDetails] = useState<Details | null>(null);
  const load = useCallback(async () => {
    const [candidate, interview] = await Promise.all([client.records<Candidate>("candidates"), client.records<Interview>("interviews")]);
    setCandidates(candidate.items); setInterviews(interview.items);
  }, [client]);
  const save = useHrSave(runner, client, load, refreshContext);
  useEffect(() => { void run(load); }, [run, load]);
  const candidateOptions: [string, string][] = candidates.map(candidate => [candidate.id, candidate.display_name]);
  const names = new Map(candidateOptions);
  const interviewOptions = scheduledOptions(interviews, item => names.get(item.candidate_id) ?? "Interview");
  return <div className="space-y-5" aria-busy={busy}>
    {message && <Notice>{message}</Notice>}{error && <Notice error>{error}. Check saved records before retrying an uncertain write.</Notice>}
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
    <Form title="Cancel local interview" disabled={busy || readonly} fields={[{ name: "schedule", label: "Interview", options: interviewOptions }, ref("reason", "Cancellation reason")]}
      save={data => save({ operation: "interview.cancel", schedule_id: value(data, "schedule"), reason: value(data, "reason") })} />
    <Form title="Local interview schedule" disabled={busy || readonly} fields={[{ name: "candidate", label: "Candidate", options: candidateOptions },
      <MemberSelect key="interviewer" members={interviewers} name="interviewer" label="Interviewer" />,
      { name: "starts", label: "Start (Malaysia time)", type: "datetime-local" }, { name: "ends", label: "End (Malaysia time)", type: "datetime-local" },
      { name: "timezone", label: "Time zone", options: [["Asia/Kuala_Lumpur", "Kuala Lumpur"], ["Asia/Kuching", "Kuching"]] }]}
      save={data => save({ operation: "interview.schedule", candidate_id: value(data, "candidate"), interviewer_id: value(data, "interviewer"), starts_at: local(value(data, "starts")), ends_at: local(value(data, "ends")), timezone: value(data, "timezone") })}>
      <p className="text-sm sm:col-span-2">Creates a local appointment and reminder. External invitations require a configured calendar connection.</p>
    </Form>
  </div>;
}
