"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Client, Me } from "@/lib/identity-contracts";
import {
  availableActions,
  createSubscription,
  dayAfter,
  decideRequest,
  eventSentence,
  formatDateTime,
  formatDay,
  getSubscription,
  listPlans,
  listRequests,
  money,
  proposeAction,
  requestSentence,
  searchCustomers,
  searchSubscriptions,
  STATUS_LABELS,
  createPlan,
  updatePlan,
  type ActionIn,
  type BillingInterval,
  type CustomerRef,
  type PlanOut,
  type RequestOut,
  type SubscriptionAction,
  type SubscriptionDetail,
  type SubscriptionOut,
  type SubscriptionStatus,
} from "@/lib/subscriptions-api";
import type { ProposalOut } from "@/lib/workspace-api";
import { ProposalCard } from "./ProposalCard";
import { action, field, Heading, Notice, Panel, secondary } from "./ui";

type Tab = "customers" | "plans" | "requests";
const TABS: { key: Tab; label: string }[] = [
  { key: "customers", label: "Customers" },
  { key: "plans", label: "Plans" },
  { key: "requests", label: "Requests" },
];
const ACTION_LABELS: Record<SubscriptionAction, string> = {
  pause: "Pause subscription",
  resume: "Resume subscription",
  change_plan: "Change plan",
  cancel_at_period_end: "Cancel at period end",
  cancel_now: "Cancel now",
  extend_renewal: "Extend renewal",
};
const INTERVALS: Record<BillingInterval, string> = { month: "a month", year: "a year" };
const textarea =
  "min-h-20 w-full rounded-md border border-ink-300 bg-white px-3 py-2 text-sm outline-none focus:border-brand-600";
const cell = "border-b border-ink-100 px-3 py-2 align-top";
const head = "border-b border-ink-200 px-3 py-2 text-left font-medium text-ink-600";

type PlanName = (code: string) => string;

function message(cause: unknown) {
  return cause instanceof Error ? cause.message : "The request failed. Try again.";
}

function customerName(customer: CustomerRef) {
  return customer.full_name || customer.phone || customer.email || "Unnamed customer";
}

/** A local calendar day at the time of day of `timeFrom` (else midnight), as an aware ISO time. */
function dayToInstant(day: string, timeFrom?: string | null): string {
  const [year, month, date] = day.split("-").map(Number);
  const base = timeFrom ? new Date(timeFrom) : null;
  return new Date(year, month - 1, date, base?.getHours() ?? 0, base?.getMinutes() ?? 0).toISOString();
}

export function SubscriptionsWorkspace({ me, initialClientId = "" }: { me: Me; initialClientId?: string }) {
  const platform = me.role === "platform_admin";
  const writer = platform || me.role === "tenant_admin" || me.role === "tenant_staff";
  const [clients, setClients] = useState<Client[] | null>(null);
  const [selected, setSelected] = useState(initialClientId);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!platform) return;
    const controller = new AbortController();
    api<Client[]>("/console/v1/admin/clients", { signal: controller.signal })
      .then(setClients)
      .catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [platform]);
  const clientId = platform ? selected : me.active_client_id;
  return (
    <>
      <Heading title="Subscriptions"
        description="Your customers' plans, the changes you make to them and the requests customers send." />
      {error && <div className="mb-5"><Notice error>{error}</Notice></div>}
      {platform && (
        <div className="mb-5">
          <label htmlFor="subscriptions-tenant" className="mb-2 block text-sm">Company</label>
          <select id="subscriptions-tenant" data-testid="subscriptions-tenant" className={field} value={selected}
            onChange={(event) => setSelected(event.target.value)}>
            <option value="">{clients ? "Select a company" : "Loading companies…"}</option>
            {clients?.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
          </select>
        </div>
      )}
      {!clientId
        ? <Notice>{platform ? "Select a company to see its subscriptions." : "Join a company to see its subscriptions."}</Notice>
        : <Board key={clientId} tenantId={platform ? clientId : null} writer={writer} />}
    </>
  );
}

function Board({ tenantId, writer }: { tenantId: string | null; writer: boolean }) {
  const [tab, setTab] = useState<Tab>("customers");
  const [plans, setPlans] = useState<PlanOut[]>([]);
  const [plansRevision, setPlansRevision] = useState(0);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    // Archived plans too: old timeline events and subscriptions still name them.
    listPlans(tenantId, true)
      .then((rows) => { if (live) { setPlans(rows); setError(""); } })
      .catch((cause) => { if (live) setError(message(cause)); });
    return () => { live = false; };
  }, [tenantId, plansRevision]);
  const names = new Map(plans.map((plan) => [plan.code, plan.name]));
  const planName: PlanName = (code) => names.get(code) ?? code;
  return (
    <>
      <div role="tablist" aria-label="Subscription sections" className="mb-5 flex flex-wrap gap-2">
        {TABS.map(({ key, label }) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key}
            data-testid={`subscriptions-tab-${key}`}
            className={tab === key ? action : secondary} onClick={() => setTab(key)}>{label}</button>
        ))}
      </div>
      {error && <div className="mb-5"><Notice error>{error}</Notice></div>}
      {tab === "customers" && <Customers tenantId={tenantId} writer={writer} plans={plans} planName={planName} />}
      {tab === "plans" && <Plans tenantId={tenantId} writer={writer} plans={plans}
        onChanged={() => setPlansRevision((value) => value + 1)} />}
      {tab === "requests" && <Requests tenantId={tenantId} writer={writer} planName={planName} />}
    </>
  );
}

// ---------------------------------------------------------------- Customers

interface Filters { q: string; status: SubscriptionStatus | ""; plan: string }

function Customers({ tenantId, writer, plans, planName }: {
  tenantId: string | null; writer: boolean; plans: PlanOut[]; planName: PlanName;
}) {
  const [draft, setDraft] = useState("");
  const [filters, setFilters] = useState<Filters>({ q: "", status: "", plan: "" });
  const [rows, setRows] = useState<SubscriptionOut[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let live = true;
    searchSubscriptions(tenantId, filters)
      .then((page) => { if (live) { setRows(page.items); setNext(page.next_cursor); setError(""); } })
      .catch((cause) => { if (live) setError(message(cause)); });
    return () => { live = false; };
  }, [tenantId, filters, revision]);
  async function more() {
    if (!next) return;
    try {
      const page = await searchSubscriptions(tenantId, { ...filters, before: next });
      setRows((current) => [...(current ?? []), ...page.items]);
      setNext(page.next_cursor);
    } catch (cause) {
      setError(message(cause));
    }
  }
  const filtered = Boolean(filters.q || filters.status || filters.plan);
  return (
    <Panel title="Customers">
      {notice && <div className="mb-4"><Notice>{notice}</Notice></div>}
      {writer && !adding && (
        <div className="mb-4"><button type="button" className={action} data-testid="subscription-add"
          onClick={() => { setAdding(true); setNotice(""); }}>Add subscription</button></div>
      )}
      {writer && adding && (
        <AddSubscription tenantId={tenantId} plans={plans} onCancel={() => setAdding(false)}
          onAdded={(text) => { setAdding(false); setNotice(text); setRevision((value) => value + 1); }} />
      )}
      <form className="mb-5 grid gap-3 md:grid-cols-[1fr_12rem_12rem_auto]" role="search"
        onSubmit={(event) => { event.preventDefault(); setFilters((current) => ({ ...current, q: draft.trim() })); }}>
        <label className="sr-only" htmlFor="subscriptions-search">Search customers</label>
        <input id="subscriptions-search" data-testid="subscriptions-search" className={field} type="search"
          maxLength={100} placeholder="Search by name, phone or email" value={draft}
          onChange={(event) => setDraft(event.target.value)} />
        <label className="sr-only" htmlFor="subscriptions-status">Status</label>
        <select id="subscriptions-status" data-testid="subscriptions-status" className={field} value={filters.status}
          onChange={(event) => setFilters((current) => ({ ...current, status: event.target.value as Filters["status"] }))}>
          <option value="">All statuses</option>
          {(Object.keys(STATUS_LABELS) as SubscriptionStatus[]).map((status) =>
            <option key={status} value={status}>{STATUS_LABELS[status]}</option>)}
        </select>
        <label className="sr-only" htmlFor="subscriptions-plan">Plan</label>
        <select id="subscriptions-plan" data-testid="subscriptions-plan" className={field} value={filters.plan}
          onChange={(event) => setFilters((current) => ({ ...current, plan: event.target.value }))}>
          <option value="">All plans</option>
          {plans.map((plan) => <option key={plan.code} value={plan.code}>{plan.name}</option>)}
        </select>
        <button className={action} data-testid="subscriptions-search-submit">Search</button>
      </form>
      {error && <div className="mb-4"><Notice error>{error}</Notice></div>}
      {rows === null ? (!error && <Notice>Loading subscriptions…</Notice>) : rows.length === 0 ? (
        <Notice>{filtered
          ? "No subscriptions match. Try another name, phone or email, or clear the filters."
          : writer
            ? "Subscriptions will appear here. Add a subscription to put a customer on one of your plans."
            : "Subscriptions will appear here once your team adds a customer to a plan."}</Notice>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[40rem] text-sm" data-testid="subscriptions-table">
            <thead><tr>
              <th className={head}>Customer</th><th className={head}>Plan</th><th className={head}>Status</th>
              <th className={head}>Renews on</th><th className={head}>Price</th>
            </tr></thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} data-testid="subscription-row" className="cursor-pointer hover:bg-ink-50"
                  onClick={() => setOpenId(row.id)}>
                  <td className={cell}>
                    <button type="button" className="text-left font-medium text-brand-700 hover:underline"
                      onClick={(event) => { event.stopPropagation(); setOpenId(row.id); }}>
                      {customerName(row.customer)}
                    </button>
                    {row.customer.full_name && (row.customer.phone || row.customer.email) && (
                      <p className="text-xs text-ink-500">{row.customer.phone || row.customer.email}</p>
                    )}
                  </td>
                  <td className={cell}>{row.plan_name || planName(row.plan)}</td>
                  <td className={cell}>
                    <span data-testid="subscription-status">{STATUS_LABELS[row.status]}</span>
                    {row.cancel_at_period_end && row.status === "active" &&
                      <p className="text-xs text-ink-500">Cancels at period end</p>}
                  </td>
                  <td className={cell}>{row.status === "cancelled" ? "—" : formatDay(row.renews_at)}</td>
                  <td className={cell}>{money(row.price, row.currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {next && <div className="mt-4"><button type="button" className={secondary} onClick={() => void more()}>
        Show more customers</button></div>}
      {openId && <Drawer key={openId} tenantId={tenantId} id={openId} writer={writer} plans={plans}
        planName={planName} onClose={() => setOpenId(null)} onChanged={() => setRevision((value) => value + 1)} />}
    </Panel>
  );
}

function AddSubscription({ tenantId, plans, onAdded, onCancel }: {
  tenantId: string | null; plans: PlanOut[]; onAdded: (text: string) => void; onCancel: () => void;
}) {
  const [term, setTerm] = useState("");
  const [found, setFound] = useState<CustomerRef[] | null>(null);
  const [chosen, setChosen] = useState<CustomerRef | null>(null);
  const [planCode, setPlanCode] = useState("");
  const [startsOn, setStartsOn] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const choices = plans.filter((plan) => plan.is_active);
  async function find() {
    setError("");
    try {
      setFound(await searchCustomers(tenantId, term));
    } catch (cause) {
      setError(message(cause));
    }
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!chosen || !planCode) return;
    setBusy(true);
    setError("");
    try {
      await createSubscription(tenantId, {
        contact_id: chosen.id, plan_code: planCode, ...(startsOn ? { started_at: dayToInstant(startsOn) } : {}),
      });
      const plan = choices.find((item) => item.code === planCode);
      onAdded(`Added. ${customerName(chosen)} is on the ${plan?.name ?? planCode} plan.`);
    } catch (cause) {
      setError(message(cause));
      setBusy(false);
    }
  }
  const contact = (who: CustomerRef) => (who.full_name && (who.phone || who.email) ? ` · ${who.phone || who.email}` : "");
  return (
    <form className="mb-5 space-y-3 rounded-md border border-ink-200 p-4" data-testid="subscription-add-form"
      onSubmit={(event) => void submit(event)}>
      <h3 className="font-semibold">Add subscription</h3>
      {chosen ? (
        <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
          <p data-testid="add-customer-chosen">Customer: <span className="font-medium">{customerName(chosen)}</span>
            <span className="text-ink-500">{contact(chosen)}</span></p>
          <button type="button" className={secondary} disabled={busy}
            onClick={() => setChosen(null)}>Choose another customer</button>
        </div>
      ) : (
        <div className="space-y-2">
          <label className="block text-sm" htmlFor="add-customer-search">Customer</label>
          <div className="flex flex-wrap gap-2">
            <input id="add-customer-search" data-testid="add-customer-search" className={`${field} min-w-0 flex-1`}
              type="search" maxLength={100} placeholder="Name, phone or email" value={term}
              onChange={(event) => setTerm(event.target.value)}
              onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); void find(); } }} />
            <button type="button" className={secondary} data-testid="add-customer-find"
              onClick={() => void find()}>Find customers</button>
          </div>
          {found && (found.length === 0
            ? <p className="text-sm text-ink-600">No customers match. Try another name, phone or email.</p>
            : (
              <ul className="divide-y divide-ink-100 rounded-md border border-ink-200 text-sm">
                {found.map((who) => (
                  <li key={who.id}>
                    <button type="button" data-testid="add-customer-option"
                      className="w-full px-3 py-2 text-left hover:bg-ink-50" onClick={() => setChosen(who)}>
                      <span className="font-medium">{customerName(who)}</span>
                      <span className="text-ink-500">{contact(who)}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ))}
        </div>
      )}
      <label className="block text-sm">Plan
        <select className={field} required data-testid="add-plan" value={planCode}
          onChange={(event) => setPlanCode(event.target.value)}>
          <option value="">Select a plan</option>
          {choices.map((plan) => <option key={plan.code} value={plan.code}>
            {plan.name} · {money(plan.price, plan.currency)} {INTERVALS[plan.billing_interval]}</option>)}
        </select>
      </label>
      {choices.length === 0 && <p className="text-sm text-ink-600">Add a plan in the Plans tab first.</p>}
      <label className="block text-sm">Start on (optional; leave empty to start today)
        <input className={field} type="date" data-testid="add-start" value={startsOn}
          onChange={(event) => setStartsOn(event.target.value)} />
      </label>
      <p className="text-xs text-ink-500">The first renewal is one billing period after the start date.</p>
      {error && <Notice error>{error}</Notice>}
      <div className="flex flex-wrap gap-2">
        <button className={action} disabled={busy || !chosen || !planCode} data-testid="add-submit">
          {busy ? "Adding…" : "Add subscription"}</button>
        <button type="button" className={secondary} disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

function Drawer({ tenantId, id, writer, plans, planName, onClose, onChanged }: {
  tenantId: string | null; id: string; writer: boolean; plans: PlanOut[]; planName: PlanName;
  onClose: () => void; onChanged: () => void;
}) {
  const [detail, setDetail] = useState<SubscriptionDetail | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [form, setForm] = useState<SubscriptionAction | null>(null);
  const [proposal, setProposal] = useState<ProposalOut | null>(null);
  useEffect(() => {
    let live = true;
    getSubscription(tenantId, id)
      .then((value) => { if (live) { setDetail(value); setError(""); } })
      .catch((cause) => { if (live) setError(message(cause)); });
    return () => { live = false; };
  }, [tenantId, id, revision]);
  const subscription = detail?.subscription;
  const name = subscription ? customerName(subscription.customer) : "Subscription";
  const pending = proposal?.status === "requested";
  const events = [...(detail?.events ?? [])].sort(
    (left, right) => Date.parse(right.created_at) - Date.parse(left.created_at) || right.id - left.id);
  return (
    <aside role="dialog" aria-label={`Subscription for ${name}`} data-testid="subscription-drawer"
      className="fixed inset-y-0 right-0 z-40 w-full max-w-lg overflow-y-auto border-l border-ink-200 bg-white p-5 shadow-xl sm:p-6">
      <div className="mb-4 flex items-start justify-between gap-3">
        <h2 className="text-lg font-semibold">{name}</h2>
        <button type="button" className={secondary} data-testid="subscription-close" onClick={onClose}>Close</button>
      </div>
      {error && <div className="mb-4"><Notice error>{error}</Notice></div>}
      {!subscription ? (!error && <Notice>Loading the subscription…</Notice>) : (
        <>
          <dl className="grid gap-2 text-sm sm:grid-cols-[9rem_1fr]" data-testid="subscription-details">
            <Detail label="Phone" value={subscription.customer.phone} />
            <Detail label="Email" value={subscription.customer.email} />
            <Detail label="Plan" value={subscription.plan_name || planName(subscription.plan)} />
            <Detail label="Status" value={STATUS_LABELS[subscription.status]} testId="subscription-detail-status" />
            <Detail label="Price" value={money(subscription.price, subscription.currency)} />
            <Detail label="Started on" value={formatDay(subscription.started_at)} />
            {subscription.status !== "cancelled" && <Detail label="Renews on" value={formatDay(subscription.renews_at)} />}
            {subscription.paused_at && <Detail label="Paused on" value={formatDay(subscription.paused_at)} />}
            {subscription.status === "paused" && <Detail label="Resumes on"
              value={subscription.resume_at ? formatDay(subscription.resume_at) : "When someone resumes it"} />}
            {subscription.cancelled_at && <Detail label="Cancelled on" value={formatDay(subscription.cancelled_at)} />}
            {subscription.pending_plan && <Detail label="Scheduled change"
              value={`Moves to ${planName(subscription.pending_plan)} on ${formatDay(subscription.pending_effective_at)}`} />}
            {subscription.cancel_at_period_end && <Detail label="Cancellation"
              value={`Cancels when the period ends on ${formatDay(subscription.renews_at)}`} />}
          </dl>
          {writer && !pending && (
            <div className="mt-5 flex flex-wrap gap-2" data-testid="subscription-actions">
              {availableActions(subscription).map((kind) => (
                <button key={kind} type="button" className={kind === "cancel_now" ? secondary : action}
                  data-testid={`subscription-action-${kind}`} aria-pressed={form === kind}
                  onClick={() => { setForm(kind); setProposal(null); }}>{ACTION_LABELS[kind]}</button>
              ))}
            </div>
          )}
          {writer && form && !pending && (
            <ActionForm key={form} kind={form} subscription={subscription} plans={plans}
              onCancel={() => setForm(null)}
              onSubmit={async (body) => {
                const created = await proposeAction(tenantId, subscription.id, body);
                setProposal(created);
                setForm(null);
              }} />
          )}
          {proposal && (
            <ProposalCard key={proposal.id} proposal={proposal} tenantId={tenantId}
              onSettled={(settled) => { setProposal(settled); setRevision((value) => value + 1); onChanged(); }} />
          )}
          <h3 className="mb-3 mt-6 font-semibold">Timeline</h3>
          {events.length === 0 ? <p className="text-sm text-ink-500">Changes to this subscription will appear here.</p> : (
            <ol className="space-y-3 text-sm" data-testid="subscription-timeline">
              {events.map((event) => (
                <li key={event.id} className="border-l-2 border-brand-200 pl-3">
                  <p className="text-xs text-ink-500">
                    <time dateTime={event.created_at}>{formatDateTime(event.created_at)}</time>
                    {event.actor.startsWith("system:") ? " · Automatic" : ""}
                  </p>
                  <p>{eventSentence(event, planName)}</p>
                </li>
              ))}
            </ol>
          )}
        </>
      )}
    </aside>
  );
}

function Detail({ label, value, testId }: { label: string; value: string | null; testId?: string }) {
  return (
    <>
      <dt className="text-ink-500">{label}</dt>
      <dd className="break-words" data-testid={testId}>{value || "—"}</dd>
    </>
  );
}

function ActionForm({ kind, subscription, plans, onSubmit, onCancel }: {
  kind: SubscriptionAction; subscription: SubscriptionOut; plans: PlanOut[];
  onSubmit: (body: ActionIn) => Promise<void>; onCancel: () => void;
}) {
  const [resumeOn, setResumeOn] = useState("");
  const [planCode, setPlanCode] = useState("");
  const [startsOn, setStartsOn] = useState("");
  const [price, setPrice] = useState("");
  const [renewsOn, setRenewsOn] = useState("");
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const choices = plans.filter((plan) => plan.is_active);
  const special = kind === "change_plan" && price.trim() !== "";
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const body: ActionIn = { action: kind, reason: reason.trim() || null };
    if (kind === "pause" && resumeOn) body.resume_at = resumeOn;
    if (kind === "change_plan") {
      body.plan_code = planCode;
      if (startsOn) body.effective_at = dayToInstant(startsOn);
      if (special) body.price_override = price.trim();
    }
    if (kind === "extend_renewal") body.renews_at = dayToInstant(renewsOn, subscription.renews_at);
    setBusy(true);
    setError("");
    try {
      await onSubmit(body);
    } catch (cause) {
      setError(message(cause));
      setBusy(false);
    }
  }
  return (
    <form className="mt-4 space-y-3 rounded-md border border-ink-200 p-4" data-testid="subscription-action-form"
      onSubmit={(event) => void submit(event)}>
      <h3 className="font-semibold">{ACTION_LABELS[kind]}</h3>
      {kind === "pause" && (
        <label className="block text-sm">Resume on (optional)
          <input className={field} type="date" min={dayAfter()} data-testid="action-resume-at" value={resumeOn}
            onChange={(event) => setResumeOn(event.target.value)} />
        </label>
      )}
      {kind === "change_plan" && (
        <>
          <label className="block text-sm">New plan
            <select className={field} required data-testid="action-plan" value={planCode}
              onChange={(event) => setPlanCode(event.target.value)}>
              <option value="">Select a plan</option>
              {choices.map((plan) => <option key={plan.code} value={plan.code}>
                {plan.name} · {money(plan.price, plan.currency)} {INTERVALS[plan.billing_interval]}</option>)}
            </select>
          </label>
          <label className="block text-sm">Start on (optional; leave empty to change it now)
            <input className={field} type="date" data-testid="action-effective-at" value={startsOn}
              onChange={(event) => setStartsOn(event.target.value)} />
          </label>
          <label className="block text-sm">Special price in RM (optional; starts now)
            <input className={field} type="number" min="0" step="0.01" inputMode="decimal"
              data-testid="action-price" value={price} onChange={(event) => setPrice(event.target.value)} />
          </label>
        </>
      )}
      {kind === "extend_renewal" && (
        <label className="block text-sm">New renewal date
          <input className={field} type="date" required data-testid="action-renews-at" value={renewsOn}
            onChange={(event) => setRenewsOn(event.target.value)} />
        </label>
      )}
      <label className="block text-sm">{special ? "Reason (required for a special price)" : "Reason (optional)"}
        <textarea className={textarea} maxLength={500} required={special} data-testid="action-reason" value={reason}
          onChange={(event) => setReason(event.target.value)} />
      </label>
      {error && <Notice error>{error}</Notice>}
      <p className="text-xs text-ink-500">Nothing changes until you confirm the card that appears next.</p>
      <div className="flex flex-wrap gap-2">
        <button className={action} disabled={busy} data-testid="action-submit">
          {busy ? "Preparing…" : "Review change"}</button>
        <button type="button" className={secondary} disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------- Plans

interface PlanDraft { code: string; name: string; price: string; interval: BillingInterval; features: string }
const EMPTY_PLAN: PlanDraft = { code: "", name: "", price: "", interval: "month", features: "" };

const featureList = (text: string) => text.split("\n").map((line) => line.trim()).filter(Boolean);

function Plans({ tenantId, writer, plans, onChanged }: {
  tenantId: string | null; writer: boolean; plans: PlanOut[]; onChanged: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState<PlanDraft>(EMPTY_PLAN);
  const [editing, setEditing] = useState<string | null>(null);
  const [edit, setEdit] = useState<PlanDraft>(EMPTY_PLAN);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function run(call: () => Promise<unknown>, after: () => void) {
    setBusy(true);
    setError("");
    try {
      await call();
      after();
      onChanged();
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }
  function startEdit(plan: PlanOut) {
    setEditing(plan.code);
    setEdit({ code: plan.code, name: plan.name, price: String(plan.price), interval: plan.billing_interval,
      features: plan.features.join("\n") });
  }
  return (
    <Panel title="Plans">
      {error && <div className="mb-4"><Notice error>{error}</Notice></div>}
      {writer && !adding && (
        <div className="mb-4"><button type="button" className={action} data-testid="plan-add"
          onClick={() => { setAdding(true); setDraft(EMPTY_PLAN); }}>Add plan</button></div>
      )}
      {writer && adding && (
        <form className="mb-5 grid gap-3 rounded-md border border-ink-200 p-4 md:grid-cols-2" data-testid="plan-form"
          onSubmit={(event) => {
            event.preventDefault();
            void run(() => createPlan(tenantId, {
              code: draft.code.trim(), name: draft.name.trim(), price: draft.price.trim(),
              billing_interval: draft.interval, features: featureList(draft.features),
            }), () => setAdding(false));
          }}>
          <label className="block text-sm">Plan name
            <input className={field} required maxLength={120} data-testid="plan-name" value={draft.name}
              onChange={(event) => setDraft({ ...draft, name: event.target.value })} />
          </label>
          <label className="block text-sm">Short code (lowercase letters, numbers, - or _; cannot change later)
            <input className={field} required pattern="[a-z0-9_\-]{1,64}" maxLength={64} data-testid="plan-code"
              value={draft.code} onChange={(event) => setDraft({ ...draft, code: event.target.value })} />
          </label>
          <PlanFields draft={draft} onChange={setDraft} prefix="plan" />
          <div className="flex flex-wrap gap-2 md:col-span-2">
            <button className={action} disabled={busy} data-testid="plan-save">Save plan</button>
            <button type="button" className={secondary} disabled={busy} onClick={() => setAdding(false)}>Cancel</button>
          </div>
        </form>
      )}
      {plans.length === 0 ? (
        <Notice>{writer ? "No plans yet. Add your first plan so customers can subscribe."
          : "Plans will appear here once your team adds them."}</Notice>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[40rem] text-sm" data-testid="plans-table">
            <thead><tr>
              <th className={head}>Plan</th><th className={head}>Price</th><th className={head}>Features</th>
              <th className={head}>Status</th>{writer && <th className={head}><span className="sr-only">Actions</span></th>}
            </tr></thead>
            <tbody>
              {plans.map((plan) => editing === plan.code ? (
                <tr key={plan.code} data-testid={`plan-row-${plan.code}`}>
                  <td className={cell} colSpan={writer ? 5 : 4}>
                    <form className="grid gap-3 md:grid-cols-2" onSubmit={(event) => {
                      event.preventDefault();
                      void run(() => updatePlan(tenantId, plan.code, {
                        name: edit.name.trim(), price: edit.price.trim(), billing_interval: edit.interval,
                        features: featureList(edit.features),
                      }), () => setEditing(null));
                    }}>
                      <label className="block text-sm md:col-span-2">Plan name
                        <input className={field} required maxLength={120} data-testid="plan-edit-name" value={edit.name}
                          onChange={(event) => setEdit({ ...edit, name: event.target.value })} />
                      </label>
                      <PlanFields draft={edit} onChange={setEdit} prefix="plan-edit" />
                      <div className="flex flex-wrap gap-2 md:col-span-2">
                        <button className={action} disabled={busy} data-testid="plan-edit-save">Save changes</button>
                        <button type="button" className={secondary} disabled={busy}
                          onClick={() => setEditing(null)}>Cancel</button>
                      </div>
                    </form>
                  </td>
                </tr>
              ) : (
                <tr key={plan.code} data-testid={`plan-row-${plan.code}`}>
                  <td className={cell}><p className="font-medium">{plan.name}</p>
                    <p className="text-xs text-ink-500">Code: {plan.code}</p></td>
                  <td className={cell}>{money(plan.price, plan.currency)} {INTERVALS[plan.billing_interval]}</td>
                  <td className={cell}>{plan.features.length ? plan.features.join(", ") : "—"}</td>
                  <td className={cell}>{plan.is_active ? "Available" : "Archived"}</td>
                  {writer && (
                    <td className={`${cell} whitespace-nowrap text-right`}>
                      <div className="flex justify-end gap-2">
                        <button type="button" className={secondary} disabled={busy}
                          data-testid={`plan-edit-${plan.code}`} onClick={() => startEdit(plan)}>Edit</button>
                        <button type="button" className={secondary} disabled={busy}
                          data-testid={`plan-archive-${plan.code}`}
                          onClick={() => void run(() => updatePlan(tenantId, plan.code, { is_active: !plan.is_active }),
                            () => undefined)}>{plan.is_active ? "Archive" : "Restore"}</button>
                      </div>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="mt-4 text-xs text-ink-500">Archived plans stay on existing subscriptions but cannot be chosen for new ones.</p>
    </Panel>
  );
}

function PlanFields({ draft, onChange, prefix }: {
  draft: PlanDraft; onChange: (next: PlanDraft) => void; prefix: string;
}) {
  return (
    <>
      <label className="block text-sm">Price in RM
        <input className={field} type="number" min="0" step="0.01" inputMode="decimal" required
          data-testid={`${prefix}-price`} value={draft.price}
          onChange={(event) => onChange({ ...draft, price: event.target.value })} />
      </label>
      <label className="block text-sm">Billed
        <select className={field} data-testid={`${prefix}-interval`} value={draft.interval}
          onChange={(event) => onChange({ ...draft, interval: event.target.value as BillingInterval })}>
          <option value="month">Every month</option>
          <option value="year">Every year</option>
        </select>
      </label>
      <label className="block text-sm md:col-span-2">Features (one per line, optional)
        <textarea className={textarea} data-testid={`${prefix}-features`} value={draft.features}
          onChange={(event) => onChange({ ...draft, features: event.target.value })} />
      </label>
    </>
  );
}

// ---------------------------------------------------------------- Requests

function Requests({ tenantId, writer, planName }: { tenantId: string | null; writer: boolean; planName: PlanName }) {
  const [revision, setRevision] = useState(0);
  const [done, setDone] = useState("");
  return (
    <div className="space-y-5">
      {done && <Notice>{done}</Notice>}
      <Panel title="Open requests">
        <RequestList tenantId={tenantId} status="open" writer={writer} planName={planName} revision={revision}
          onDecided={(text) => { setDone(text); setRevision((value) => value + 1); }} />
      </Panel>
      <Panel title="Decided requests">
        <RequestList tenantId={tenantId} status="decided" writer={false} planName={planName} revision={revision}
          onDecided={() => undefined} />
      </Panel>
    </div>
  );
}

function RequestList({ tenantId, status, writer, planName, revision, onDecided }: {
  tenantId: string | null; status: "open" | "decided"; writer: boolean; planName: PlanName;
  revision: number; onDecided: (text: string) => void;
}) {
  const [rows, setRows] = useState<RequestOut[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    listRequests(tenantId, status)
      .then((page) => { if (live) { setRows(page.items); setNext(page.next_cursor); setError(""); } })
      .catch((cause) => { if (live) setError(message(cause)); });
    return () => { live = false; };
  }, [tenantId, status, revision]);
  async function more() {
    if (!next) return;
    try {
      const page = await listRequests(tenantId, status, next);
      setRows((current) => [...(current ?? []), ...page.items]);
      setNext(page.next_cursor);
    } catch (cause) {
      setError(message(cause));
    }
  }
  if (rows === null) return error ? <Notice error>{error}</Notice> : <Notice>Loading requests…</Notice>;
  return (
    <>
      {error && <div className="mb-4"><Notice error>{error}</Notice></div>}
      {rows.length === 0 ? (
        <Notice>{status === "open"
          ? "Requests from customers to pause, cancel or change their plan will appear here."
          : "Requests you approve or decline will appear here."}</Notice>
      ) : (
        <ul className="divide-y divide-ink-100" data-testid={`requests-${status}`}>
          {rows.map((row) => status === "open"
            ? <OpenRequest key={row.id} row={row} tenantId={tenantId} writer={writer} planName={planName}
                onDecided={onDecided} />
            : <DecidedRequest key={row.id} row={row} planName={planName} />)}
        </ul>
      )}
      {next && <div className="mt-4"><button type="button" className={secondary} onClick={() => void more()}>
        Show more requests</button></div>}
    </>
  );
}

function RequestSummary({ row, planName }: { row: RequestOut; planName: PlanName }) {
  return (
    <div className="min-w-0">
      <p className="font-medium">{customerName(row.customer)}</p>
      <p>{requestSentence(row, planName)}</p>
      {row.reason && <p className="text-ink-600">Reason: {row.reason}</p>}
      <p className="text-xs text-ink-500">Requested {formatDateTime(row.requested_at)}</p>
    </div>
  );
}

function OpenRequest({ row, tenantId, writer, planName, onDecided }: {
  row: RequestOut; tenantId: string | null; writer: boolean; planName: PlanName;
  onDecided: (text: string) => void;
}) {
  const [form, setForm] = useState<"approve" | "decline" | null>(null);
  const [resumeOn, setResumeOn] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const pause = row.action === "request_pause";
  async function decide(decision: "approve" | "decline") {
    setBusy(true);
    setError("");
    try {
      await decideRequest(tenantId, row.id, {
        decision,
        note: note.trim() || null,
        ...(decision === "approve" && pause && resumeOn ? { resume_at: resumeOn } : {}),
      });
      onDecided(decision === "approve"
        ? `Approved. ${customerName(row.customer)}'s subscription has been updated.`
        : `Declined. Nothing changed on ${customerName(row.customer)}'s subscription.`);
    } catch (cause) {
      setError(message(cause));
      setBusy(false);
    }
  }
  return (
    <li className="py-3 text-sm" data-testid="request-open">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <RequestSummary row={row} planName={planName} />
        {writer && !form && (
          <div className="flex flex-wrap gap-2">
            {row.subscription_id && (
              <button type="button" className={action} disabled={busy} data-testid="request-approve"
                onClick={() => (pause ? setForm("approve") : void decide("approve"))}>
                {busy ? "Approving…" : "Approve"}</button>
            )}
            <button type="button" className={secondary} disabled={busy} data-testid="request-decline"
              onClick={() => setForm("decline")}>Decline</button>
          </div>
        )}
      </div>
      {!row.subscription_id && (
        <p className="mt-2 text-ink-600">This request is not linked to a subscription, so it can only be declined.</p>
      )}
      {writer && form && (
        <form className="mt-3 space-y-3 rounded-md border border-ink-200 p-4" data-testid="request-form"
          onSubmit={(event) => { event.preventDefault(); void decide(form); }}>
          {form === "approve" && (
            <label className="block">Resume on (optional)
              <input className={field} type="date" min={dayAfter()} data-testid="request-resume-at" value={resumeOn}
                onChange={(event) => setResumeOn(event.target.value)} />
            </label>
          )}
          <label className="block">Note (optional)
            <input className={field} maxLength={1000} data-testid="request-note" value={note}
              onChange={(event) => setNote(event.target.value)} />
          </label>
          <div className="flex flex-wrap gap-2">
            <button className={action} disabled={busy} data-testid="request-confirm">
              {form === "approve" ? (busy ? "Approving…" : "Approve request") : (busy ? "Declining…" : "Decline request")}
            </button>
            <button type="button" className={secondary} disabled={busy} onClick={() => setForm(null)}>Cancel</button>
          </div>
        </form>
      )}
      {error && <div className="mt-3"><Notice error>{error}</Notice></div>}
    </li>
  );
}

function DecidedRequest({ row, planName }: { row: RequestOut; planName: PlanName }) {
  return (
    <li className="flex flex-wrap items-start justify-between gap-3 py-3 text-sm" data-testid="request-decided">
      <RequestSummary row={row} planName={planName} />
      <div className="text-right">
        <p className="font-medium">{row.decision === "approved" ? "Approved" : "Declined"}</p>
        {row.decided_at && <p className="text-xs text-ink-500">{formatDateTime(row.decided_at)}</p>}
        {row.note && <p className="text-ink-600">Note: {row.note}</p>}
      </div>
    </li>
  );
}
