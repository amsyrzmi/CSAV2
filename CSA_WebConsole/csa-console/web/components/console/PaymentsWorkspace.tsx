"use client";

import { useCallback, useEffect, useId, useRef, useState, type RefObject } from "react";
import SlideOver from "@/components/console/fs/SlideOver";
import { api } from "@/lib/api";
import type { Client, Me } from "@/lib/identity-contracts";
import { consoleHref } from "@/lib/navigation";
import { usePublishContext } from "@/lib/page-context";
import {
  getInvoice,
  isNoAccess,
  isRefusal,
  listAttention,
  listInvoices,
  openMarkPaid,
  paymentMessage,
  staleMessage,
  STATUS_LABELS,
  voidInvoice,
  type InvoiceDetail,
  type InvoiceFilter,
  type MethodKind,
  type PaymentAttentionItem,
} from "@/lib/payments-api";
import {
  appendPage,
  attentionCount,
  attentionGroups,
  canMarkPaid,
  canVoid,
  customerLabel,
  filterChips,
  firstPage,
  formatAmount,
  invoiceName,
  listKey,
  markPaidQuestion,
  METHOD_OPTIONS,
  methodLabel,
  moreCursor,
  receivedAt,
  reminderLine,
  todayInput,
  voidBody,
  type InvoiceList,
} from "@/lib/payments-view";
import { formatDateTime, formatDay } from "@/lib/subscriptions-api";
import { StatusChips } from "./kit";
import { action, field, Heading, Notice, secondary } from "./ui";

const cell = "border-b border-ink-100 px-3 py-2 align-top";
/** On a phone each invoice is one small block instead of a table that scrolls sideways. */
const phoneCell = "max-sm:border-b-0 max-sm:px-0 max-sm:py-0 max-sm:before:block max-sm:before:text-xs max-sm:before:text-ink-600 max-sm:before:content-[attr(data-label)]";
const head = "border-b border-ink-200 px-3 py-2 text-left font-medium text-ink-600";
const textarea =
  "min-h-20 w-full rounded-md border border-ink-300 bg-[var(--fs-surface)] px-3 py-2 text-sm outline-none focus:border-brand-600";
const helpText = "mt-1 text-xs text-ink-600";
const card = "card mb-5 rounded-[14px] p-5 sm:p-6";
const sectionTitle = "font-(family-name:--fs-font-heading) text-base font-semibold";
/** How long the search box waits after the last key before it asks the server. */
const SEARCH_DELAY_MS = 300;

const NO_ACCESS = "Payments is for admins and staff. Ask an admin if you need to see invoices.";

export function PaymentsWorkspace({ me, initialClientId = "", initialInvoiceId = "" }: {
  me: Me; initialClientId?: string; initialInvoiceId?: string;
}) {
  const platform = me.role === "platform_admin";
  const writer = platform || me.role === "tenant_admin" || me.role === "tenant_staff";
  const admin = platform || me.role === "tenant_admin";
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
      <Heading title="Payments"
        description="Invoices for your customers' subscriptions: record a payment that arrived, void a mistake and see what needs a nudge." />
      {error && <div className="mb-5"><Notice error>{error}</Notice></div>}
      {!writer ? <Notice testId="payments-no-access">{NO_ACCESS}</Notice> : (
        <>
          {platform && (
            <div className="mb-5">
              <label htmlFor="payments-tenant" className="mb-2 block text-sm">Company</label>
              <select id="payments-tenant" data-testid="payments-tenant" className={field} value={selected}
                onChange={(event) => setSelected(event.target.value)}>
                <option value="">{clients ? "Select a company" : "Loading companies…"}</option>
                {clients?.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
              </select>
            </div>
          )}
          {!clientId
            ? <Notice>{platform ? "Select a company to see its invoices." : "Join a company to see its invoices."}</Notice>
            : <Board key={clientId} tenantId={platform ? clientId : null} admin={admin}
              initialInvoiceId={!platform || clientId === initialClientId ? initialInvoiceId : ""} />}
        </>
      )}
    </>
  );
}

function Board({ tenantId, admin, initialInvoiceId }: {
  tenantId: string | null; admin: boolean; initialInvoiceId: string;
}) {
  usePublishContext({ tab: "subscriptions", section: "payments" });
  // A 403 for this login is final: the page says so instead of drawing empty lists.
  const [forbidden, setForbidden] = useState(false);
  const [refusal, setRefusal] = useState("");
  const [revision, setRevision] = useState(0);
  const [openId, setOpenId] = useState<string | null>(initialInvoiceId || null);
  const openerRef = useRef<HTMLElement | null>(null);
  const refused = useCallback((cause: unknown) => {
    if (isNoAccess(cause)) setForbidden(true);
    else if (isRefusal(cause)) setRefusal(paymentMessage(cause));
  }, []);
  const open = (invoiceId: string, opener: HTMLElement | null) => {
    openerRef.current = opener;
    setOpenId(invoiceId);
  };
  if (forbidden) return <Notice testId="payments-no-access">{NO_ACCESS}</Notice>;
  if (refusal) return <Notice error testId="payments-refused">{refusal}</Notice>;
  const settingsHref = consoleHref(tenantId !== null, "payments/settings", { client_id: tenantId });
  return (
    <>
      {admin && (
        <div className="mb-5 flex justify-end">
          {/* The settings page is its own route (how customers pay, reminders). */}
          <a href={settingsHref} className={secondary} data-testid="payments-settings-link">Settings</a>
        </div>
      )}
      <Attention tenantId={tenantId} revision={revision} onOpen={open} onRefused={refused} />
      <Invoices tenantId={tenantId} revision={revision} onOpen={open} onRefused={refused} />
      {openId && <InvoiceDrawer key={openId} tenantId={tenantId} id={openId} returnFocusTo={openerRef}
        onClose={() => setOpenId(null)} onChanged={() => setRevision((value) => value + 1)} />}
    </>
  );
}

type Opener = (invoiceId: string, opener: HTMLElement | null) => void;

// ---------------------------------------------------------------- Needs attention

function Attention({ tenantId, revision, onOpen, onRefused }: {
  tenantId: string | null; revision: number; onOpen: Opener; onRefused: (cause: unknown) => void;
}) {
  const [items, setItems] = useState<PaymentAttentionItem[] | null>(null);
  const [error, setError] = useState("");
  const titleId = useId();
  useEffect(() => {
    let live = true;
    listAttention(tenantId)
      .then((page) => { if (live) { setItems(page.items); setError(""); } })
      .catch((cause) => { if (live) { setError(paymentMessage(cause)); onRefused(cause); } });
    return () => { live = false; };
  }, [tenantId, revision, onRefused]);
  const count = items ? attentionCount(items) : 0;
  return (
    <section aria-labelledby={titleId} data-testid="payments-attention" className={card}>
      <h2 id={titleId} className={`${sectionTitle} mb-4 flex items-center gap-2`}>
        Needs attention
        {count > 0 && (
          <span data-testid="attention-badge"
            className="rounded-full bg-brand-600 px-2 text-xs font-semibold text-[color:var(--fs-on-primary)]">
            <span className="sr-only">Invoices: </span>{count}
          </span>
        )}
      </h2>
      {error ? <Notice error>{error}</Notice> : items === null ? <Notice>Loading what needs attention…</Notice>
        : count === 0 ? <p className="text-sm text-ink-600" data-testid="attention-empty">Nothing needs attention right now.</p> : (
          <div className="space-y-4">
            {attentionGroups(items).filter((group) => group.items.length > 0).map((group) => (
              <div key={group.kind} data-testid={`attention-group-${group.kind}`}>
                <h3 className="mb-1 text-sm font-semibold">{group.label}</h3>
                <ul className="divide-y divide-ink-100 rounded-md border border-ink-200 text-sm">
                  {group.items.map((item) => (
                    <li key={`${group.kind}-${item.invoice.id}`}>
                      <button type="button" data-testid="attention-row"
                        className="flex w-full flex-wrap items-baseline justify-between gap-x-3 px-3 py-2 text-left hover:bg-ink-50"
                        onClick={(event) => onOpen(item.invoice.id, event.currentTarget)}>
                        <span>
                          <span className="font-medium text-brand-700">{invoiceName(item.invoice)}</span>
                          <span className="text-ink-600"> · {customerLabel(item.invoice)} · {
                            formatAmount(item.invoice.amount, item.invoice.currency)}</span>
                        </span>
                        <span className="text-xs text-ink-500">Since {formatDay(item.since)}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
    </section>
  );
}

// ---------------------------------------------------------------- Invoices

function Invoices({ tenantId, revision, onOpen, onRefused }: {
  tenantId: string | null; revision: number; onOpen: Opener; onRefused: (cause: unknown) => void;
}) {
  const [status, setStatus] = useState<InvoiceFilter>("open");
  const [draft, setDraft] = useState("");
  const [q, setQ] = useState("");
  // The rows carry the filter they were read for, so a late page for an old filter is dropped.
  const [list, setList] = useState<InvoiceList | null>(null);
  const [error, setError] = useState("");
  // The key of the list a "Load more" is out for, if any.
  const [loadingMore, setLoadingMore] = useState<string | null>(null);
  const titleId = useId();
  const key = listKey(tenantId, status, q, revision);
  const rows = list?.rows ?? null;
  const next = moreCursor(list, key);
  // The box asks the server once typing pauses, not on every key.
  useEffect(() => {
    const timer = setTimeout(() => setQ(draft.trim()), SEARCH_DELAY_MS);
    return () => clearTimeout(timer);
  }, [draft]);
  useEffect(() => {
    let live = true;
    const read = listKey(tenantId, status, q, revision);
    listInvoices(tenantId, { status, q })
      .then((page) => { if (live) { setList(firstPage(read, page)); setError(""); } })
      .catch((cause) => { if (live) { setError(paymentMessage(cause)); onRefused(cause); } });
    return () => { live = false; };
  }, [tenantId, status, q, revision, onRefused]);
  // Answers for an older filter (the chip or search changed while it was out) are dropped.
  const current = useRef(key);
  useEffect(() => { current.current = key; }, [key]);
  async function more() {
    if (!next || loadingMore === key) return;
    const asked = key;
    setLoadingMore(asked);
    try {
      const page = await listInvoices(tenantId, { status, q, before: next });
      if (current.current !== asked) return;
      setList((shown) => appendPage(shown, asked, page));
      setError("");
    } catch (cause) {
      if (current.current !== asked) return;
      setError(paymentMessage(cause));
      onRefused(cause);
    } finally {
      setLoadingMore((out) => (out === asked ? null : out));
    }
  }
  const searching = Boolean(q);
  return (
    <section aria-labelledby={titleId} data-testid="payments-invoices" className={card}>
      <h2 id={titleId} className={`${sectionTitle} mb-5`}>Invoices</h2>
      <div className="mb-4">
        <p className="mb-1 text-sm font-medium">Show</p>
        <div className="min-w-0 max-sm:[&>[role=group]]:flex-nowrap max-sm:[&>[role=group]]:overflow-x-auto max-sm:[&>[role=group]]:overscroll-x-contain max-sm:[&>[role=group]]:py-1 max-sm:[&>[role=group]>button]:shrink-0">
          <StatusChips<InvoiceFilter> mode="single" testId="invoices-status" ariaLabel="Show invoices"
            options={filterChips()} value={status} onChange={(picked) => setStatus(picked as InvoiceFilter)} />
        </div>
      </div>
      <form role="search" className="mb-4" onSubmit={(event) => { event.preventDefault(); setQ(draft.trim()); }}>
        <label className="mb-1 block text-sm font-medium" htmlFor="invoices-search">Search invoices</label>
        <input id="invoices-search" data-testid="invoices-search" className={`${field} sm:max-w-md`} type="search"
          maxLength={100} placeholder="Search by reference or customer" value={draft}
          onChange={(event) => setDraft(event.target.value)} />
      </form>
      {error && <div className="mb-4"><Notice error>{error}</Notice></div>}
      {rows === null ? (!error && <Notice>Loading invoices…</Notice>) : rows.length === 0 ? (
        <Notice testId="invoices-empty">{searching || status !== "open"
          ? "No invoices match. Try another reference or name, or another choice above."
          : "No open invoices. Invoices appear here when a subscription is billed."}</Notice>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm sm:min-w-[44rem]" data-testid="invoices-table">
            <thead className="max-sm:sr-only"><tr>
              <th className={head}>Reference</th><th className={head}>Customer</th><th className={head}>Plan</th>
              <th className={head}>Amount</th><th className={head}>Due</th><th className={head}>Status</th>
            </tr></thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} data-testid="invoice-row"
                  className="cursor-pointer hover:bg-ink-50 max-sm:grid max-sm:grid-cols-2 max-sm:gap-x-3 max-sm:gap-y-2 max-sm:border-b max-sm:border-ink-100 max-sm:px-1 max-sm:py-3"
                  onClick={(event) => onOpen(row.id, event.currentTarget.querySelector("button"))}>
                  <td className={`${cell} max-sm:col-span-2 max-sm:border-b-0 max-sm:p-0`}>
                    <button type="button" className="text-left font-medium text-brand-700 hover:underline"
                      onClick={(event) => { event.stopPropagation(); onOpen(row.id, event.currentTarget); }}>
                      {invoiceName(row)}
                    </button>
                  </td>
                  <td data-label="Customer" className={`${cell} ${phoneCell}`}>{customerLabel(row)}</td>
                  <td data-label="Plan" className={`${cell} ${phoneCell}`}>{row.plan_name || "—"}</td>
                  <td data-label="Amount" className={`${cell} ${phoneCell}`} data-testid="invoice-amount">
                    {formatAmount(row.amount, row.currency)}</td>
                  <td data-label="Due" className={`${cell} ${phoneCell}`}>{formatDay(row.due_at)}</td>
                  <td data-label="Status" className={`${cell} ${phoneCell}`}>
                    <span data-testid="invoice-status">{STATUS_LABELS[row.status]}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {next && (
        <div className="mt-4">
          <button type="button" className={secondary} data-testid="invoices-more" disabled={loadingMore === key}
            onClick={() => void more()}>{loadingMore === key ? "Loading…" : "Load more"}</button>
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- The invoice drawer

function InvoiceDrawer({ tenantId, id, returnFocusTo, onClose, onChanged }: {
  tenantId: string | null; id: string; returnFocusTo: RefObject<HTMLElement | null>;
  onClose: () => void; onChanged: () => void;
}) {
  // Mounted closed, opened by the effect (see the Subscriptions drawer for why).
  const [shown, setShown] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(frame);
  }, []);
  const [detail, setDetail] = useState<InvoiceDetail | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [form, setForm] = useState<"pay" | "void" | null>(null);
  const [notice, setNotice] = useState("");
  // Why the last action was refused when someone else changed the invoice first. The form that
  // got the refusal closes, so the drawer keeps the sentence on show over the fresh read.
  const [staleNotice, setStaleNotice] = useState("");
  useEffect(() => {
    let live = true;
    getInvoice(tenantId, id)
      .then((value) => { if (live) { setDetail(value); setError(""); } })
      .catch((cause) => { if (live) setError(paymentMessage(cause)); });
    return () => { live = false; };
  }, [tenantId, id, revision]);
  function settled(value: InvoiceDetail, text: string) {
    setDetail(value);
    setForm(null);
    setNotice(text);
    setStaleNotice("");
    onChanged();
  }
  // Someone else changed it first: say so, and read it again so the drawer shows where it stands.
  const stale = (text: string) => {
    setForm(null);
    setNotice("");
    setStaleNotice(text);
    setRevision((value) => value + 1);
    onChanged();
  };
  const startForm = (kind: "pay" | "void") => { setForm(kind); setNotice(""); setStaleNotice(""); };
  const title = detail ? `Invoice ${invoiceName(detail)}` : "Invoice";
  return (
    <SlideOver open={shown} onClose={onClose} title={title} returnFocusTo={returnFocusTo} widthClass="max-w-lg"
      testId="invoice-drawer" closeTestId="invoice-close">
      {error && <div className="mb-4"><Notice error>{error}</Notice></div>}
      {notice && <div className="mb-4"><Notice testId="invoice-notice">{notice}</Notice></div>}
      {staleNotice && <div className="mb-4"><Notice warning testId="invoice-stale">{staleNotice}</Notice></div>}
      {!detail ? (!error && <Notice>Loading the invoice…</Notice>) : (
        <div className="space-y-4">
          <DrawerSection id="details" title="Details">
            <dl className="grid gap-2 text-sm sm:grid-cols-[9rem_1fr]">
              <Detail label="Customer" value={customerLabel(detail)} />
              <Detail label="Plan" value={detail.plan_name} />
              <Detail label="Amount" value={formatAmount(detail.amount, detail.currency)} testId="invoice-detail-amount" />
              <Detail label="Status" value={STATUS_LABELS[detail.status]} testId="invoice-detail-status" />
              <Detail label="Issued on" value={formatDay(detail.issued_at)} />
              <Detail label="Due on" value={formatDay(detail.due_at)} />
              {detail.period_start && <Detail label="Covers"
                value={`${formatDay(detail.period_start)} to ${formatDay(detail.period_end)}`} />}
              {detail.paid_at && <Detail label="Paid on" value={formatDay(detail.paid_at)} />}
              {detail.voided_at && <Detail label="Voided on" value={formatDay(detail.voided_at)} />}
              {detail.void_reason && <Detail label="Why it was voided (only staff see this)"
                value={detail.void_reason} testId="invoice-void-reason" />}
            </dl>
          </DrawerSection>
          {(detail.claimed_at || detail.claim_note) && (
            <DrawerSection id="claim" title="What the customer said">
              {/* Customer-typed: rendered as text, never as markup. */}
              {detail.claim_note
                ? <p data-testid="invoice-claim-note" className="whitespace-pre-wrap break-words text-sm">{detail.claim_note}</p>
                : <p className="text-sm text-ink-600">They said they paid but added no note.</p>}
              <p className="mt-1 text-xs text-ink-500">
                {detail.claimed_at ? `Said paid on ${formatDateTime(detail.claimed_at)}` : ""}
                {detail.method_kind ? ` · by ${methodLabel(detail.method_kind)}` : ""}
              </p>
            </DrawerSection>
          )}
          <DrawerSection id="reminders" title="Reminders">
            {detail.reminders.length === 0 ? <p className="text-sm text-ink-600">No reminders yet.</p> : (
              <ul className="space-y-1 text-sm" data-testid="invoice-reminders">
                {detail.reminders.map((reminder) => (
                  <li key={`${reminder.step}-${reminder.created_at}`} data-testid="invoice-reminder">{reminderLine(reminder)}</li>
                ))}
              </ul>
            )}
          </DrawerSection>
          {detail.payment && (
            <DrawerSection id="payment" title="Payment">
              <dl className="grid gap-2 text-sm sm:grid-cols-[9rem_1fr]" data-testid="invoice-payment">
                <Detail label="Amount" value={formatAmount(detail.payment.amount, detail.payment.currency)} />
                <Detail label="Received on" value={formatDay(detail.payment.received_at)} />
                <Detail label="Method" value={methodLabel(detail.payment.method_kind)} />
                <Detail label="Recorded by" value={detail.payment.created_by_name ?? "Automatic"} />
                {detail.payment.note && <Detail label="Note (only staff see this)" value={detail.payment.note}
                  testId="invoice-payment-note" />}
              </dl>
            </DrawerSection>
          )}
          {!form && (canMarkPaid(detail.status) || canVoid(detail.status)) && (
            <div className="flex flex-wrap gap-2 border-t border-[color:var(--fs-border)] pt-4" data-testid="invoice-actions">
              {canMarkPaid(detail.status) && <button type="button" className={action} data-testid="invoice-mark-paid"
                onClick={() => startForm("pay")}>Mark paid</button>}
              {canVoid(detail.status) && <button type="button" className={secondary} data-testid="invoice-void"
                onClick={() => startForm("void")}>Void</button>}
            </div>
          )}
          {form === "pay" && <MarkPaidForm tenantId={tenantId} invoice={detail} onCancel={() => setForm(null)}
            onStale={stale} onDone={(value) => settled(value, "Marked paid. The payment is recorded.")} />}
          {form === "void" && <VoidForm tenantId={tenantId} invoice={detail} onCancel={() => setForm(null)}
            onStale={stale} onDone={(value) => settled(value, "Voided. The customer is not sent your reason.")} />}
        </div>
      )}
    </SlideOver>
  );
}

function DrawerSection({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section aria-label={title} data-testid={`invoice-section-${id}`}
      className="border-t border-[color:var(--fs-border)] pt-4 first:border-t-0 first:pt-0">
      <h3 className="mb-2 font-semibold">{title}</h3>
      {children}
    </section>
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

function MarkPaidForm({ tenantId, invoice, onCancel, onDone, onStale }: {
  tenantId: string | null; invoice: InvoiceDetail; onCancel: () => void;
  onDone: (value: InvoiceDetail) => void; onStale: (message: string) => void;
}) {
  // One request key per opened form: a retry or a second click replays it, so the server
  // records this payment at most once.
  const [session] = useState(() => openMarkPaid(tenantId, invoice.id));
  const [method, setMethod] = useState<MethodKind>(invoice.method_kind ?? "transfer");
  const [day, setDay] = useState(() => todayInput());
  const [note, setNote] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const firstField = useRef<HTMLSelectElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const noteHelp = useId();
  useEffect(() => {
    (confirming ? confirmRef.current : firstField.current)?.focus();
  }, [confirming]);
  async function confirm() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      onDone(await session.submit({ method_kind: method, received_at: receivedAt(day), note: note.trim() || null }));
    } catch (cause) {
      // Someone else paid or voided it first: this form closes and the drawer says why.
      const stale = staleMessage(cause);
      if (stale) {
        onStale(stale);
        return;
      }
      setError(paymentMessage(cause));
      setBusy(false);
    }
  }
  const reference = invoiceName(invoice);
  return (
    <form className="space-y-3 rounded-md border border-ink-200 p-4" data-testid="mark-paid-form"
      onSubmit={(event) => { event.preventDefault(); if (confirming) void confirm(); else setConfirming(true); }}>
      <h3 className="font-semibold">Mark paid</h3>
      {!confirming ? (
        <>
          <p className={helpText}>Record a payment that has arrived. The amount is the invoice&apos;s: {
            formatAmount(invoice.amount, invoice.currency)}.</p>
          <label className="block text-sm">How they paid
            <select ref={firstField} className={field} data-testid="mark-paid-method" value={method}
              onChange={(event) => setMethod(event.target.value as MethodKind)}>
              {METHOD_OPTIONS.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
            </select>
          </label>
          <label className="block text-sm">Paid on
            <input className={field} type="date" required max={todayInput()} data-testid="mark-paid-date" value={day}
              onChange={(event) => setDay(event.target.value)} />
          </label>
          <label className="block text-sm">Note (optional)
            <textarea className={textarea} maxLength={300} data-testid="mark-paid-note" value={note}
              aria-describedby={noteHelp} onChange={(event) => setNote(event.target.value)} />
          </label>
          <p id={noteHelp} className={helpText}>Only staff see this.</p>
          <div className="flex flex-wrap gap-2">
            <button className={action} disabled={!day} data-testid="mark-paid-continue">Continue</button>
            <button type="button" className={secondary} onClick={onCancel}>Cancel</button>
          </div>
        </>
      ) : (
        <div data-testid="mark-paid-confirm-step" className="space-y-3">
          <p data-testid="mark-paid-question" className="text-sm font-medium">{markPaidQuestion(reference)}</p>
          <p className="text-xs text-ink-600">
            {formatAmount(invoice.amount, invoice.currency)} by {methodLabel(method)}, paid on {formatDay(day)}.
          </p>
          <div className="flex flex-wrap gap-2">
            <button ref={confirmRef} className={action} disabled={busy} data-testid="mark-paid-confirm">
              {busy ? "Marking paid…" : "Mark paid"}</button>
            <button type="button" className={secondary} disabled={busy}
              onClick={() => { setConfirming(false); setError(""); }}>Back</button>
          </div>
        </div>
      )}
      {error && <Notice error testId="mark-paid-error">{error}</Notice>}
    </form>
  );
}

function VoidForm({ tenantId, invoice, onCancel, onDone, onStale }: {
  tenantId: string | null; invoice: InvoiceDetail; onCancel: () => void;
  onDone: (value: InvoiceDetail) => void; onStale: (message: string) => void;
}) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const help = useId();
  useEffect(() => { reasonRef.current?.focus(); }, []);
  const body = voidBody(reason);
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!body || busy) return;
    setBusy(true);
    setError("");
    try {
      onDone(await voidInvoice(tenantId, invoice.id, body));
    } catch (cause) {
      const stale = staleMessage(cause);
      if (stale) {
        onStale(stale);
        return;
      }
      setError(paymentMessage(cause));
      setBusy(false);
    }
  }
  return (
    <form className="space-y-3 rounded-md border border-ink-200 p-4" data-testid="void-form"
      onSubmit={(event) => void submit(event)}>
      <h3 className="font-semibold">Void invoice {invoiceName(invoice)}</h3>
      <label className="block text-sm">Reason (required; only staff see this)
        <textarea ref={reasonRef} className={textarea} required maxLength={300} data-testid="void-reason" value={reason}
          aria-describedby={help} onChange={(event) => setReason(event.target.value)} />
      </label>
      <p id={help} className={helpText}>A void invoice can&apos;t be paid. The customer is not sent your reason.</p>
      <div className="flex flex-wrap gap-2">
        <button className={action} disabled={!body || busy} data-testid="void-confirm">
          {busy ? "Voiding…" : "Void invoice"}</button>
        <button type="button" className={secondary} disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
      {error && <Notice error testId="void-error">{error}</Notice>}
    </form>
  );
}
