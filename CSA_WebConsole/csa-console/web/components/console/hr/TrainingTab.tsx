"use client";
import { useEffect, useMemo, useState } from "react";
import { hrClient, type Confirmation, type Page, type Training } from "@/lib/hr-api";
import { usePublishContext } from "@/lib/page-context";
import { createProposal, type ProposalOut } from "@/lib/workspace-api";
import { ProposalCard } from "../ProposalCard";
import { action, secondary, Notice, Panel } from "../ui";
import { Input } from "./HrForm";
import type { HrTabProps } from "./HrShell";
import { useHrRunner } from "./useHrRunner";

/** The Training tab: request training, then submit, withdraw or decide through review. */
export function TrainingTab({ tenant, context, readonly, refreshContext }: HrTabProps) {
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const { busy, error, message, setMessage, setError, run } = useHrRunner();
  const [training, setTraining] = useState<Page<Training>>({ items: [], next_cursor: null });
  const [automatic, setAutomatic] = useState(false);
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [review, setReview] = useState<{ id: string; title: string; version: number; action: string } | null>(null);
  const [proposal, setProposal] = useState<ProposalOut | null>(null);
  const writable = !readonly;
  usePublishContext({ tab: "hr", section: "training", record: review ? { kind: "training_request", id: review.id } : null });
  async function refresh(after?: string) {
    await refreshContext();
    const page = await client.training(after);
    setTraining(old => ({ ...page, items: after ? [...old.items, ...page.items] : page.items }));
  }
  const canRead = context.actions.includes("training.read");
  useEffect(() => {
    if (canRead) void run(async () => setTraining(await client.training()));
  }, [client, run, canRead]);
  async function saved(text: string) {
    setMessage(text);
    try { await refresh(); }
    catch (cause) { setError(`Saved successfully; refreshing records failed: ${cause instanceof Error ? cause.message : "unavailable"}`); }
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
  async function decide(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!review) return;
    if (review.action === "submit") {
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
      const command = { action: review.action, expected_version: review.version, decision_note: reason };
      if (automatic && ["approve", "reject"].includes(review.action)) {
        const result = await client.execute({ action: "training.transition", request_id: review.id, command });
        if ("kind" in result && result.kind === "confirmation_required") setConfirmation(result);
      } else await client.transition(review.id, command);
      setReview(null); await saved("Request processed.");
    });
  }
  const mine = (employeeId: string) => employeeId === context.employee?.employee_id;
  return <div className="space-y-6" aria-busy={busy}>
    <div className="flex flex-wrap gap-2">
      <button className={secondary} disabled={busy} onClick={() => void run(() => refresh())}>Refresh</button>
    </div>
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
    {writable && context.actions.includes("training.draft") && <Panel title="Request training"><form onSubmit={trainingDraft} className="space-y-4">
      <Input name="title" label="Training title" /><Input name="objective" label="Learning objective" maxLength={1000} />
      <button className={action} disabled={busy}>Save draft</button>
    </form></Panel>}
    <Panel title="Training requests"><div className="space-y-4">{training.items.length === 0 && <p>No requests loaded.</p>}
      {training.items.map(item => <article className="rounded-md border border-ink-200 p-4" key={item.request_id}>
        <h3 className="font-medium">{item.content.title}</h3><p className="my-2 text-sm">{item.content.learning_objective}</p>
        <p className="text-sm">{item.status} · version {item.version}</p>{item.decision?.note && <p className="text-sm">{item.decision.note}</p>}
        {writable && <div className="mt-3 flex flex-wrap gap-2">{[
          ...(item.status === "draft" && mine(item.employee.employee_id) ? ["submit"] : []),
          ...(["draft", "submitted"].includes(item.status) && mine(item.employee.employee_id) ? ["withdraw"] : []),
          ...(item.status === "submitted" && context.actions.includes("training.decide") ? ["approve", "reject"] : []),
        ].map(verb => <button className={secondary} key={verb} disabled={busy} onClick={() => setReview({ id: item.request_id,
          title: item.content.title, version: item.version, action: verb })}>{verb}</button>)}</div>}
      </article>)}</div></Panel>
    {training.next_cursor && <button className={secondary} disabled={busy} onClick={() => void run(() => refresh(training.next_cursor ?? undefined))}>Load more</button>}
  </div>;
}
