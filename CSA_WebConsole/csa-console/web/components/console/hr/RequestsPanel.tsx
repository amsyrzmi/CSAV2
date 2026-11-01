"use client";
import { useEffect, useMemo, useState } from "react";
import { hrClient, type Domain, type Page, type Workflow } from "@/lib/hr-api";
import { requestActions } from "@/lib/hr-requests";
import { action, field, secondary, Notice, Panel } from "../ui";
import { Input } from "./HrForm";
import type { HrTabProps } from "./HrShell";
import { useHrRunner } from "./useHrRunner";

export const ALL_DOMAINS: Domain[] = ["recruitment", "attendance", "leave", "payroll"];
const DOMAIN_LABELS: Record<Domain, string> = {
  recruitment: "Recruitment interview guide", attendance: "Attendance or correction",
  leave: "Leave request", payroll: "Payroll preparation (MYR)",
};

/**
 * "HR requests": prepare a request, then withdraw, approve or reject it through review. Prepare and
 * approve/reject are offered only for the domains the viewer's workflow grants allow.
 */
export function RequestsPanel({ tenant, context, readonly, userId, refreshContext, domains }: HrTabProps & { domains: Domain[] }) {
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const { busy, error, message, setMessage, setError, run } = useHrRunner();
  const [workflows, setWorkflows] = useState<Page<Workflow>>({ items: [], next_cursor: null });
  const [automatic, setAutomatic] = useState(false);
  const [review, setReview] = useState<{ id: string; title: string; version: number; action: string } | null>(null);
  const writable = !readonly;
  const actions = requestActions(context.workflows, domains);
  const preparable = writable ? actions.prepare : [];
  const [chosen, setDomain] = useState<Domain | null>(null);
  // A refreshed context can take a grant away; fall back to the first domain still allowed.
  const domain = chosen !== null && preparable.includes(chosen) ? chosen : preparable[0];
  async function refresh(after?: string) {
    await refreshContext();
    const page = await client.workflows(after);
    setWorkflows(old => ({ ...page, items: after ? [...old.items, ...page.items] : page.items }));
  }
  useEffect(() => {
    void run(async () => setWorkflows(await client.workflows()));
  }, [client, run]);
  async function saved(text: string) {
    setMessage(text);
    try { await refresh(); }
    catch (cause) { setError(`Saved successfully; refreshing records failed: ${cause instanceof Error ? cause.message : "unavailable"}`); }
  }
  async function workflowPrepare(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
    const value = (name: string) => String(data.get(name) ?? "");
    const content: Record<string, unknown> = { domain, region: value("region") };
    if (domain === "recruitment") Object.assign(content, { role_title: value("role_title"),
      competencies: value("competencies").split(",").map(s => s.trim()).filter(Boolean) });
    if (domain === "attendance") Object.assign(content, { starts_at: `${value("starts_at")}:00+08:00`,
      ends_at: `${value("ends_at")}:00+08:00`, timezone: value("timezone"), reason: value("reason"),
      correction_of: value("correction_of") || null });
    if (domain === "leave") Object.assign(content, { starts_on: value("starts_on"), ends_on: value("ends_on"),
      leave_type: value("leave_type"), reason: value("reason") });
    if (domain === "payroll") Object.assign(content, { starts_on: value("starts_on"), ends_on: value("ends_on"),
      cycle: value("cycle"), currency: "MYR", legal_entity_reference: value("legal_entity_reference"),
      rule_set_reference: value("rule_set_reference") });
    await run(async () => {
      if (automatic && domain !== "payroll") await client.execute({ action: "workflow.prepare", content });
      else await client.prepare(content);
      form.reset(); await saved("Request saved for human review.");
    });
  }
  async function decide(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!review) return;
    const reason = String(new FormData(event.currentTarget).get("reason") ?? "").trim() || null;
    await run(async () => {
      await client.decide(review.id, { action: review.action, expected_version: review.version, reason });
      setReview(null); await saved("Request processed.");
    });
  }
  const shown = workflows.items.filter(item => domains.includes(item.domain));
  return <div className="space-y-6" aria-busy={busy}>
    <div className="flex flex-wrap gap-2">
      <button className={secondary} disabled={busy} onClick={() => void run(() => refresh())}>Refresh</button>
    </div>
    {error && <Notice error>{error}. Refresh to check saved state before retrying. An identical retry keeps its request key.</Notice>}
    {message && <Notice>{message}</Notice>}
    {preparable.length > 0 && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={automatic}
      onChange={e => setAutomatic(e.target.checked)} />Use enabled staff agents for preparation. Human decisions still require review.</label>}
    {review && <Panel title={`Review: ${review.title}`}><form onSubmit={decide} className="space-y-3">
      <p>Action: {review.action}. Current version: {review.version}.</p>
      <Input name="reason" label="Decision reason" maxLength={500} required={review.action === "reject"} />
      <button className={action} disabled={busy}>Continue with {review.action}</button>
      <button className={`${secondary} ml-2`} type="button" disabled={busy} onClick={() => setReview(null)}>Cancel</button>
    </form></Panel>}
    {preparable.length > 0 && <Panel title="Prepare an HR request"><form onSubmit={workflowPrepare} className="space-y-4">
      <label className="block text-sm">Workflow<select className={`${field} mt-1`} value={domain} onChange={e => setDomain(e.target.value as Domain)}>
        {preparable.map(item => <option key={item} value={item}>{DOMAIN_LABELS[item]}</option>)}</select></label>
      <label className="block text-sm">Workforce region<select name="region" className={`${field} mt-1`} required>
        <option value="peninsular_labuan">Peninsular Malaysia / Labuan</option><option value="sabah">Sabah</option><option value="sarawak">Sarawak</option></select></label>
      {domain === "recruitment" && <><Input name="role_title" label="Role title" /><Input name="competencies" label="Job competencies, separated by commas (up to eight)" maxLength={968} /></>}
      {domain === "attendance" && <><Input name="starts_at" type="datetime-local" label="Start (Malaysia time, UTC+08)" />
        <Input name="ends_at" type="datetime-local" label="End (Malaysia time, UTC+08)" />
        <label className="block text-sm">Time zone<select name="timezone" className={`${field} mt-1`}><option value="Asia/Kuala_Lumpur">Kuala Lumpur</option><option value="Asia/Kuching">Kuching</option></select></label>
        <Input name="correction_of" label="Original approved attendance reference (for corrections)" required={false} />
        <Input name="reason" label="Attendance or correction reason" maxLength={500} /></>}
      {(domain === "leave" || domain === "payroll") && <><Input name="starts_on" type="date" label="Period start" /><Input name="ends_on" type="date" label="Period end" /></>}
      {domain === "leave" && <><label className="block text-sm">Leave type<select name="leave_type" className={`${field} mt-1`}>
        <option value="annual">Annual</option><option value="sick">Sick</option><option value="unpaid">Unpaid</option><option value="other">Other</option></select></label>
        <Input name="reason" label="Reason (omit medical details)" maxLength={500} /></>}
      {domain === "payroll" && <><Notice>Prepare a local review request. Statutory calculation, bank payment and filing are not executed here.</Notice>
        <Input name="legal_entity_reference" label="Legal entity reference" maxLength={100} />
        <Input name="rule_set_reference" label="Reviewed payroll rule set reference" />
        <label className="block text-sm">Pay cycle<select name="cycle" className={`${field} mt-1`} required><option value="monthly">Monthly</option><option value="fortnightly">Fortnightly</option><option value="weekly">Weekly</option></select></label></>}
      <button className={action} disabled={busy}>Prepare request</button>
    </form></Panel>}
    <Panel title="HR requests awaiting or recording decisions"><div className="space-y-4">{shown.length === 0 && <p>No requests loaded.</p>}
      {shown.map(item => <article key={item.id} className="rounded-md border border-ink-200 p-4">
        <h3 className="font-medium capitalize">{item.domain} · {item.status}</h3><p className="mt-1 text-xs">Reference {item.id} · version {item.version}</p>
        <dl className="my-3 grid gap-2 text-sm sm:grid-cols-2">{Object.entries(item.content).filter(([key]) => key !== "domain").map(([key, value]) =>
          <div key={key}><dt className="text-ink-500">{key.replaceAll("_", " ")}</dt><dd>{Array.isArray(value) ? value.join(", ") : value ?? "—"}</dd></div>)}</dl>
        {item.interview_guide.length > 0 && <ol className="my-3 list-decimal space-y-2 pl-5 text-sm">{item.interview_guide.map((question, index) => <li key={index}>{question}</li>)}</ol>}
        {item.decision_reason && <p className="text-sm">Decision reason: {item.decision_reason}</p>}
        {writable && item.status === "pending" && <div className="flex flex-wrap gap-2">{[
          ...(item.created_by === userId ? ["withdraw"] : []), ...(actions.decides(item.domain) ? ["approve", "reject"] : []),
        ].map(verb => <button className={secondary} disabled={busy} key={verb} onClick={() => setReview({ id: item.id,
          title: `${item.domain} request ${item.id}`, version: item.version, action: verb })}>{verb}</button>)}</div>}
      </article>)}</div></Panel>
    {workflows.next_cursor && <button className={secondary} disabled={busy} onClick={() => void run(() => refresh(workflows.next_cursor ?? undefined))}>Load more</button>}
  </div>;
}
