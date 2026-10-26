"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import type { Me } from "@/lib/identity-contracts";
import { hrClient, type AgentSwitch, type Confirmation, type Domain, type Employee,
  type HrContext, type Page, type Training, type Workflow } from "@/lib/hr-api";
import { usePublishContext } from "@/lib/page-context";
import { createProposal, type ProposalOut } from "@/lib/workspace-api";
import { action, field, secondary, Heading, Notice, Panel } from "./ui";
import { HrAutomation } from "./HrAutomation";
import { ProposalCard } from "./ProposalCard";

const labels: Record<string, string> = {
  hr_directory: "Employee directory", hr_training: "Training assistant",
  hr_recruitment: "Recruitment guide", hr_attendance: "Attendance and leave",
  hr_payroll: "Payroll workflow status", hr_assistant: "Staff chat (submitted messages use the configured model provider)",
};
const empty = <T,>(): Page<T> => ({ items: [], next_cursor: null });

function Input({ name, label, type = "text", maxLength = 160, required = true }: {
  name: string; label: string; type?: string; maxLength?: number; required?: boolean;
}) {
  return <label className="block text-sm">{label}<input name={name} type={type}
    maxLength={maxLength} required={required} className={`${field} mt-1`} /></label>;
}

/** Each tenant selection creates fresh state and transport; late responses cannot cross tenants. */
export function HrWorkspace({ me, initialTenant = "", initialSection = "training" }: {
  me: Me; initialTenant?: string; initialSection?: "directory" | "training";
}) {
  const [selection, setSelection] = useState(initialTenant || me.active_client_id || "");
  const [tenant, setTenant] = useState(me.role === "platform_admin" ? initialTenant : me.active_client_id ?? "");
  return <div className="space-y-6">
    <Heading title="People & HR" description="Training, recruitment, attendance, leave and payroll preparation." />
    {me.role === "platform_admin" && <form className="flex flex-wrap items-end gap-3" onSubmit={event => {
      event.preventDefault(); setTenant(selection);
    }}><label className="text-sm">Tenant membership<select className={`${field} mt-1`} value={selection}
      onChange={e => setSelection(e.target.value)} required><option value="">Choose a tenant</option>
      {me.memberships.map(m => <option key={m.client_id} value={m.client_id}>{m.client_id}</option>)}
    </select></label><button className={secondary}>Open HR</button></form>}
    {tenant ? <TenantHr key={`${tenant}:${initialSection}`} me={me} tenant={tenant} initialSection={initialSection} /> : <Notice>Select a tenant to continue.</Notice>}
  </div>;
}

function TenantHr({ me, tenant, initialSection }: { me: Me; tenant: string; initialSection: "directory" | "training" }) {
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const [context, setContext] = useState<HrContext | null>(null);
  const [tab, setTab] = useState<string>(initialSection);
  const [directory, setDirectory] = useState(empty<Employee>);
  const [training, setTraining] = useState(empty<Training>);
  const [workflows, setWorkflows] = useState(empty<Workflow>);
  const [agents, setAgents] = useState<AgentSwitch[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(true);
  const inFlight = useRef(true);
  const [automatic, setAutomatic] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [review, setReview] = useState<{ kind: "training" | "workflow"; id: string;
    title: string; version: number; action: string } | null>(null);
  const [domain, setDomain] = useState<Domain>("recruitment");
  const [proposal, setProposal] = useState<ProposalOut | null>(null);
  const writable = me.role !== "read_only";
  usePublishContext({ tab: "hr", section: tab,
    record: review?.kind === "training" ? { kind: "training_request", id: review.id } : null });
  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const ctx = await client.context();
        if (!active) return;
        setContext(ctx);
        if (initialSection === "directory" && ctx.actions.includes("directory.read")) {
          const page = await client.directory();
          if (active) setDirectory(page);
        } else if (initialSection === "training" && ctx.actions.includes("training.read")) {
          const page = await client.training();
          if (active) setTraining(page);
        }
      } catch (cause) {
        if (active) setError(cause instanceof Error ? cause.message : "HR request failed");
      } finally { if (active) { inFlight.current = false; setBusy(false); } }
    }
    void load();
    return () => { active = false; };
  }, [client, initialSection]);
  async function run(work: () => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError(""); setMessage("");
    try { await work(); } catch (cause) { setError(cause instanceof Error ? cause.message : "HR request failed"); }
    finally { inFlight.current = false; setBusy(false); }
  }
  async function refresh(target = tab, after?: string) {
    const ctx = await client.context(); setContext(ctx);
    if (target === "directory") {
      const page = await client.directory(after);
      setDirectory(old => ({ ...page, items: after ? [...old.items, ...page.items] : page.items }));
    } else if (target === "training") {
      const page = await client.training(after);
      setTraining(old => ({ ...page, items: after ? [...old.items, ...page.items] : page.items }));
    } else if (target === "workflows") {
      const page = await client.workflows(after);
      setWorkflows(old => ({ ...page, items: after ? [...old.items, ...page.items] : page.items }));
    } else { setAgents(await client.agents()); }
  }
  async function saved(message: string) {
    setMessage(message);
    try { await refresh(); }
    catch (cause) { setError(`Saved successfully; refreshing records failed: ${cause instanceof Error ? cause.message : "unavailable"}`); }
  }
  function choose(target: string) {
    setTab(target); setReview(null); setConfirmation(null); setProposal(null); void run(() => refresh(target));
  }
  async function trainingDraft(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = event.currentTarget; const data = new FormData(form);
    const content = { title: String(data.get("title")), learning_objective: String(data.get("objective")) };
    await run(async () => {
      if (automatic) await client.execute({ action: "training.prepare", content });
      else await client.draft(content);
      form.reset(); await saved("Training draft saved. Submit it when ready.");
    });
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
    if (review.kind === "training" && review.action === "submit") {
      // Submitting is confirmed through the shared proposal card, bound to the draft's version.
      await run(async () => {
        setProposal(await createProposal("training_submit", { action: "training_submit", request_id: review.id },
          undefined, undefined, tenant));
        setReview(null);
      });
      return;
    }
    const reason = String(new FormData(event.currentTarget).get("reason") ?? "").trim() || null;
    await run(async () => {
      if (review.kind === "training") {
        const command = { action: review.action, expected_version: review.version, decision_note: reason };
        if (automatic && ["approve", "reject"].includes(review.action)) {
          const result = await client.execute({ action: "training.transition", request_id: review.id, command });
          if ("kind" in result && result.kind === "confirmation_required") setConfirmation(result);
        } else await client.transition(review.id, command);
      } else await client.decide(review.id, { action: review.action, expected_version: review.version, reason });
      setReview(null); await saved("Request processed.");
    });
  }
  const next = tab === "directory" ? directory.next_cursor : tab === "training" ? training.next_cursor : workflows.next_cursor;
  return <div className="space-y-6" aria-busy={busy}>
    <div className="flex flex-wrap gap-2" aria-label="HR sections">
      {[["training", "Training"], ["directory", "Directory"], ["workflows", "HR requests"], ["agents", "Automation"], ["tools", "More HR tools"]].map(([key, label]) =>
        <button key={key} className={tab === key ? action : secondary} disabled={busy} aria-pressed={tab === key}
          onClick={() => choose(key)}>{label}</button>)}
      <button className={secondary} disabled={busy} onClick={() => void run(() => refresh())}>Refresh</button>
    </div>
    {!context && <Notice>Open a section or refresh to load your authorized HR records.</Notice>}
    {error && <Notice error>{error}. Refresh to check saved state before retrying. An identical retry keeps its request key.</Notice>}
    {message && <Notice>{message}</Notice>}
    {writable && <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={automatic}
      onChange={e => setAutomatic(e.target.checked)} />Use enabled staff agents for preparation. Human decisions still require review.</label>}
    {confirmation && <Panel title="Confirm the prepared training decision">
      <p>{confirmation.action} request {confirmation.request_id}, version {confirmation.expected_version}.</p>
      {confirmation.command.decision_note && <p className="my-2">{confirmation.command.decision_note}</p>}
      <p className="mb-3 text-sm">Expires {new Date(confirmation.expires_at).toLocaleString()}.</p>
      <button className={action} disabled={busy} onClick={() => void run(async () => {
        await client.confirm(confirmation.confirmation_id); setConfirmation(null); await saved("Decision saved.");
      })}>Confirm decision</button>
      <button className={`${secondary} ml-2`} disabled={busy} onClick={() => setConfirmation(null)}>Dismiss</button>
    </Panel>}
    {proposal && <ProposalCard key={proposal.id} proposal={proposal} tenantId={tenant}
      onSettled={() => void run(() => refresh())} />}
    {review && <Panel title={`Review: ${review.title}`}><form onSubmit={decide} className="space-y-3">
      <p>Action: {review.action}. Current version: {review.version}.</p>
      <Input name="reason" label="Decision reason" maxLength={500} required={review.action === "reject"} />
      <button className={action} disabled={busy}>Continue with {review.action}</button>
      <button className={`${secondary} ml-2`} type="button" disabled={busy} onClick={() => setReview(null)}>Cancel</button>
    </form></Panel>}
    {tab === "directory" && <Panel title="Authorized employee directory"><div className="overflow-x-auto">
      <table className="w-full text-left text-sm"><thead><tr>{["Name", "Title", "Department", "Work email", "Manager"].map(label => <th className="p-2" key={label}>{label}</th>)}</tr></thead>
        <tbody>{directory.items.map(item => <tr className="border-t border-ink-200" key={item.employee.employee_id}>
          <td className="p-2">{item.values.display_name ?? "—"}</td><td className="p-2">{item.values.job_title ?? "—"}</td>
          <td className="p-2">{item.values.department ?? "—"}</td><td className="p-2">{item.values.work_email ?? "—"}</td>
          <td className="p-2">{directory.items.find(manager => manager.employee.employee_id === item.values.assigned_manager?.employee_id)?.values.display_name ?? (item.values.assigned_manager ? "Assigned" : "—")}</td>
        </tr>)}</tbody></table></div></Panel>}
    {tab === "training" && <>
      {writable && context?.actions.includes("training.draft") && <Panel title="Request training"><form onSubmit={trainingDraft} className="space-y-4">
        <Input name="title" label="Training title" /><Input name="objective" label="Learning objective" maxLength={1000} />
        <button className={action} disabled={busy}>Save draft</button>
      </form></Panel>}
      <Panel title="Training requests"><div className="space-y-4">{training.items.length === 0 && <p>No requests loaded.</p>}
        {training.items.map(item => <article className="rounded-md border border-ink-200 p-4" key={item.request_id}>
          <h3 className="font-medium">{item.content.title}</h3><p className="my-2 text-sm">{item.content.learning_objective}</p>
          <p className="text-sm">{item.status} · version {item.version}</p>{item.decision?.note && <p className="text-sm">{item.decision.note}</p>}
          {writable && <div className="mt-3 flex flex-wrap gap-2">{[
            ...(item.status === "draft" && item.employee.employee_id === context?.employee?.employee_id ? ["submit"] : []),
            ...(["draft", "submitted"].includes(item.status) && item.employee.employee_id === context?.employee?.employee_id ? ["withdraw"] : []),
            ...(item.status === "submitted" && context?.actions.includes("training.decide") ? ["approve", "reject"] : []),
          ].map(verb => <button className={secondary} key={verb} disabled={busy} onClick={() => setReview({ kind: "training", id: item.request_id,
            title: item.content.title, version: item.version, action: verb })}>{verb}</button>)}</div>}
        </article>)}</div></Panel>
    </>}
    {tab === "workflows" && <>
      {writable && <Panel title="Prepare an HR request"><form onSubmit={workflowPrepare} className="space-y-4">
        <label className="block text-sm">Workflow<select className={`${field} mt-1`} value={domain} onChange={e => setDomain(e.target.value as Domain)}>
          <option value="recruitment">Recruitment interview guide</option><option value="attendance">Attendance or correction</option>
          <option value="leave">Leave request</option><option value="payroll">Payroll preparation (MYR)</option></select></label>
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
      <Panel title="HR requests awaiting or recording decisions"><div className="space-y-4">{workflows.items.length === 0 && <p>No requests loaded.</p>}
        {workflows.items.map(item => <article key={item.id} className="rounded-md border border-ink-200 p-4">
          <h3 className="font-medium capitalize">{item.domain} · {item.status}</h3><p className="mt-1 text-xs">Reference {item.id} · version {item.version}</p>
          <dl className="my-3 grid gap-2 text-sm sm:grid-cols-2">{Object.entries(item.content).filter(([key]) => key !== "domain").map(([key, value]) =>
            <div key={key}><dt className="text-ink-500">{key.replaceAll("_", " ")}</dt><dd>{Array.isArray(value) ? value.join(", ") : value ?? "—"}</dd></div>)}</dl>
          {item.interview_guide.length > 0 && <ol className="my-3 list-decimal space-y-2 pl-5 text-sm">{item.interview_guide.map((question, index) => <li key={index}>{question}</li>)}</ol>}
          {item.decision_reason && <p className="text-sm">Decision reason: {item.decision_reason}</p>}
          {writable && item.status === "pending" && <div className="flex flex-wrap gap-2">{[
            ...(item.created_by === me.user.id ? ["withdraw"] : []), "approve", "reject",
          ].map(verb => <button className={secondary} disabled={busy} key={verb} onClick={() => setReview({ kind: "workflow", id: item.id,
            title: `${item.domain} request ${item.id}`, version: item.version, action: verb })}>{verb}</button>)}</div>}
        </article>)}</div></Panel>
    </>}
    {tab === "tools" && <HrAutomation tenant={tenant} context={context} readonly={!writable} />}
    {tab === "agents" && <Panel title="Staff automation"><p className="mb-4 text-sm">Explicit client owners can enable these assistants. Existing HR permissions still apply to every action.</p>
      <div className="space-y-3">{agents.map(setting => <div key={setting.name} className="flex flex-wrap items-center justify-between gap-3 rounded-md border border-ink-200 p-4">
        <span>{labels[setting.name] ?? setting.name} · {setting.enabled ? "Enabled" : "Disabled"}</span>
        {writable && context?.client_owner && <button className={secondary} disabled={busy} onClick={() => void run(async () => {
          await client.switch({ ...setting, enabled: !setting.enabled }); await refresh("agents");
        })}>{setting.enabled ? "Disable" : "Enable"}</button>}
      </div>)}</div>
    </Panel>}
    {["directory", "training", "workflows"].includes(tab) && next && <button className={secondary} disabled={busy} onClick={() => void run(() => refresh(tab, next))}>Load more</button>}
  </div>;
}
