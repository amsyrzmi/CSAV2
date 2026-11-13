"use client";

import { useCallback, useEffect, useId, useRef, useState, type RefObject } from "react";
import { useSearchParams } from "next/navigation";
import SlideOver from "@/components/console/fs/SlideOver";
import { api } from "@/lib/api";
import type { Client, Me } from "@/lib/identity-contracts";
import { consoleHref } from "@/lib/navigation";
import { usePublishContext } from "@/lib/page-context";
import {
  createMethod,
  getInvoice,
  getSettings,
  isNoAccess,
  isNoActiveMethod,
  isRefusal,
  isVersionConflict,
  listAttention,
  listInvoices,
  listMethods,
  openMarkPaid,
  paymentMessage,
  putSettings,
  setupMessage,
  staleMessage,
  STATUS_LABELS,
  updateMethod,
  voidInvoice,
  type InvoiceDetail,
  type InvoiceFilter,
  type MethodKind,
  type PaymentAttentionItem,
  type PaymentMethod,
  type PaymentSettings,
} from "@/lib/payments-api";
import {
  addReminderDay,
  appendPage,
  attentionCount,
  attentionGroups,
  canMarkPaid,
  canVoid,
  checkMethod,
  customerLabel,
  draftPreview,
  emptyMethodDraft,
  filterChips,
  firstPage,
  formatAmount,
  hasPayableMethod,
  invoiceName,
  leadSentence,
  listKey,
  markPaidQuestion,
  METHOD_OPTIONS,
  methodChanges,
  methodDraft,
  methodLabel,
  methodPreview,
  moreCursor,
  NEW_METHOD_KINDS,
  newMethodBody,
  pauseLine,
  paymentsView,
  receivedAt,
  reminderDayLabel,
  reminderDaysLabel,
  reminderLine,
  remindersSentence,
  settingsBody,
  settingsDraft,
  shownLeadDays,
  switchSavedLine,
  todayInput,
  upsertMethod,
  voidBody,
  type InvoiceList,
  type MethodDraft,
  type MethodErrors,
  type MethodField,
  type PaymentsView,
  type SettingsDraft,
  type SettingsErrors,
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
  // The view lives in the URL (?view=settings), so a link or a reload opens the same one.
  const view = paymentsView(useSearchParams().get("view"));
  // The drawer belongs to the invoices: ?invoice= is ignored on Settings, and switching to Settings closes it.
  const [openId, setOpenId] = useState<string | null>(view === "settings" ? null : initialInvoiceId || null);
  const [drawerView, setDrawerView] = useState(view);
  if (drawerView !== view) {
    setDrawerView(view);
    if (view === "settings") setOpenId(null);
  }
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
  return (
    <>
      <ViewSwitch tenantId={tenantId} view={view} />
      {view === "settings" ? <SettingsView tenantId={tenantId} admin={admin} onRefused={refused} /> : (
        <>
          <Attention tenantId={tenantId} revision={revision} onOpen={open} onRefused={refused} />
          <Invoices tenantId={tenantId} revision={revision} onOpen={open} onRefused={refused} />
        </>
      )}
      {openId && <InvoiceDrawer key={openId} tenantId={tenantId} id={openId} returnFocusTo={openerRef}
        onClose={() => setOpenId(null)} onChanged={() => setRevision((value) => value + 1)} />}
    </>
  );
}

type Opener = (invoiceId: string, opener: HTMLElement | null) => void;

const VIEWS: { key: PaymentsView; label: string; testId: string }[] = [
  { key: "invoices", label: "Invoices", testId: "payments-invoices-link" },
  { key: "settings", label: "Settings", testId: "payments-settings-link" },
];

/**
 * Invoices or Settings. Each is a real link (it opens in a new tab too); a plain click swaps the
 * view in place through the history API, which Next.js keeps in step with useSearchParams.
 */
function ViewSwitch({ tenantId, view }: { tenantId: string | null; view: PaymentsView }) {
  return (
    <nav aria-label="Payments views" className="mb-5 flex flex-wrap gap-2" data-testid="payments-views">
      {VIEWS.map(({ key, label, testId }) => {
        const href = consoleHref(tenantId !== null, "payments", {
          client_id: tenantId, view: key === "settings" ? "settings" : null,
        });
        return (
          <a key={key} href={href} data-testid={testId} aria-current={view === key ? "page" : undefined}
            className={view === key ? action : secondary}
            onClick={(event) => {
              if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
              event.preventDefault();
              if (view !== key) window.history.pushState(null, "", href);
            }}>
            {label}
          </a>
        );
      })}
    </nav>
  );
}

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

// ---------------------------------------------------------------- Settings and methods

const CONFLICT = "Someone else changed this. Check and save again.";
const errorText = "mt-1 text-xs text-[var(--fs-danger)]";

/** Settings and payment methods. Staff read them; only admins see controls that change them. */
function SettingsView({ tenantId, admin, onRefused }: {
  tenantId: string | null; admin: boolean; onRefused: (cause: unknown) => void;
}) {
  const [settings, setSettings] = useState<PaymentSettings | null>(null);
  const [methods, setMethods] = useState<PaymentMethod[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    Promise.all([getSettings(tenantId), listMethods(tenantId)])
      .then(([read, list]) => { if (live) { setSettings(read); setMethods(list); setError(""); } })
      .catch((cause) => { if (live) { setError(paymentMessage(cause)); onRefused(cause); } });
    return () => { live = false; };
  }, [tenantId, onRefused]);
  // After a version conflict the forms read the latest again before the next save.
  const rereadSettings = useCallback(async () => {
    const read = await getSettings(tenantId);
    setSettings(read);
    return read;
  }, [tenantId]);
  const rereadMethods = useCallback(async () => {
    const list = await listMethods(tenantId);
    setMethods(list);
    return list;
  }, [tenantId]);
  // The saved method shows at once; the reread then brings anything else that changed. If the
  // reread fails, the list keeps the saved method, so it still matches "Added." or "Saved.".
  const methodSaved = useCallback((saved: PaymentMethod) => {
    setMethods((current) => upsertMethod(current ?? [], saved));
    listMethods(tenantId).then(setMethods).catch(() => undefined);
  }, [tenantId]);
  if (error) return <Notice error>{error}</Notice>;
  if (!settings || !methods) return <Notice>Loading the payment settings…</Notice>;
  return (
    <>
      {admin
        ? <SettingsForm tenantId={tenantId} settings={settings} methods={methods} onSaved={setSettings}
          onConflict={rereadSettings} />
        : <SettingsSummary settings={settings} />}
      <Methods tenantId={tenantId} admin={admin} methods={methods} onSaved={methodSaved}
        onConflict={rereadMethods} />
    </>
  );
}

function SettingsSummary({ settings }: { settings: PaymentSettings }) {
  const titleId = useId();
  const days = reminderDaysLabel(settings.reminder_days);
  return (
    <section aria-labelledby={titleId} data-testid="payments-settings" className={card}>
      <h2 id={titleId} className={`${sectionTitle} mb-4`}>Invoices and reminders</h2>
      <dl className="grid gap-2 text-sm sm:grid-cols-[14rem_1fr]">
        <Detail label="Send invoices and reminders" value={settings.enabled ? "On" : "Off"}
          testId="settings-enabled-value" />
        <Detail label="Reminders" value={`${days.charAt(0).toUpperCase()}${days.slice(1)}`}
          testId="settings-reminders-value" />
        <Detail label="Overdue invoices" value={pauseLine(settings.suspend_after_days)} testId="settings-pause-value" />
      </dl>
      <p className={`${helpText} mt-3`} data-testid="payments-lead-sentence">{leadSentence(settings.lead_days)}</p>
      <p className={`${helpText} mt-1`}>Only admins can change these settings.</p>
    </section>
  );
}

function SettingsForm({ tenantId, settings, methods, onSaved, onConflict }: {
  tenantId: string | null; settings: PaymentSettings; methods: PaymentMethod[];
  onSaved: (settings: PaymentSettings) => void; onConflict: () => Promise<PaymentSettings>;
}) {
  const payable = hasPayableMethod(methods);
  const [draft, setDraft] = useState<SettingsDraft>(() => settingsDraft(settings));
  const [errors, setErrors] = useState<SettingsErrors>({});
  const [dayText, setDayText] = useState("");
  const [dayError, setDayError] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const titleId = useId();
  const switchHelp = useId();
  const leadHelp = useId();
  const daysHelp = useId();
  const pauseHelp = useId();
  // A refusal is about the form as it was: an edit, or a method added or changed, makes it stale.
  const [seenMethods, setSeenMethods] = useState(methods);
  if (seenMethods !== methods) {
    setSeenMethods(methods);
    setError("");
  }
  const change = (next: Partial<SettingsDraft>) => {
    setDraft((current) => ({ ...current, ...next }));
    setNotice("");
    setError("");
  };
  const savedLine = switchSavedLine(draft.enabled, settings.enabled);
  function addDay() {
    const result = addReminderDay(draft.reminder_days, dayText);
    if ("error" in result) { setDayError(result.error); return; }
    change({ reminder_days: result.days });
    setDayText("");
    setDayError("");
  }
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    const result = settingsBody(draft, settings.version);
    if ("errors" in result) { setErrors(result.errors); return; }
    setErrors({});
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const saved = await putSettings(tenantId, result.body);
      onSaved(saved);
      setDraft(settingsDraft(saved));
      setNotice(saved.enabled ? "Saved. Invoices and reminders are on." : "Saved.");
    } catch (cause) {
      if (isVersionConflict(cause)) {
        // Someone saved first: show what they saved, so the next save carries the new version.
        try {
          setDraft(settingsDraft(await onConflict()));
          setError(CONFLICT);
        } catch (reread) {
          setError(paymentMessage(reread));
        }
      } else {
        // Payments stayed off: the switch shows that, not the "on" the server refused.
        if (isNoActiveMethod(cause)) setDraft((current) => ({ ...current, enabled: settings.enabled }));
        setError(setupMessage(cause));
      }
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-labelledby={titleId} data-testid="payments-settings" className={card}>
      <h2 id={titleId} className={`${sectionTitle} mb-4`}>Invoices and reminders</h2>
      <form className="space-y-5" onSubmit={(event) => void save(event)} noValidate>
        <div>
          <label className="flex items-center gap-3 text-sm font-medium">
            <input type="checkbox" role="switch" className="size-5 accent-brand-600" data-testid="payments-enabled"
              checked={draft.enabled} aria-describedby={switchHelp}
              onChange={(event) => change({ enabled: event.target.checked })} />
            Send invoices and reminders
          </label>
          <div id={switchHelp}>
            {savedLine && <p className={`${helpText} font-medium`} data-testid="payments-enabled-saved">{savedLine}</p>}
            <p className={helpText} data-testid="payments-lead-sentence">
              {leadSentence(shownLeadDays(draft.lead_days, settings.lead_days))}</p>
            {!payable && <p className={helpText} data-testid="payments-enable-hint">
              Payments need an active bank transfer or DuitNow method first: add one under Payment methods.</p>}
          </div>
        </div>
        <div>
          <label className="block text-sm font-medium" htmlFor={`${leadHelp}-input`}>
            Days before each renewal to make the invoice</label>
          <input id={`${leadHelp}-input`} className={`${field} w-24`} type="number" min={1} max={30} step={1}
            inputMode="numeric" data-testid="settings-lead-days" value={draft.lead_days}
            aria-invalid={errors.lead_days ? true : undefined} aria-describedby={leadHelp}
            onChange={(event) => change({ lead_days: event.target.value })} />
          <p id={leadHelp} className={errors.lead_days ? errorText : helpText}>
            {errors.lead_days ?? "From 1 to 30 days."}</p>
        </div>
        <fieldset>
          <legend className="text-sm font-medium">Reminder days</legend>
          <p className={helpText} data-testid="settings-reminders-sentence">
            {remindersSentence(draft.reminder_days)}</p>
          <ul className="mt-2 flex flex-wrap gap-2" data-testid="settings-reminder-chips">
            {draft.reminder_days.map((day) => (
              <li key={day} data-testid="settings-reminder-chip"
                className="flex items-center gap-1 rounded-full border border-ink-300 py-1 pl-3 pr-1 text-sm">
                {reminderDayLabel(day)}
                <button type="button" className="rounded-full px-2 text-ink-600 hover:bg-ink-100"
                  aria-label={`Remove ${reminderDayLabel(day).toLowerCase()}`}
                  onClick={() => change({ reminder_days: draft.reminder_days.filter((value) => value !== day) })}>
                  ×</button>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <label className="block text-sm">Add a day
              <input className={`${field} w-24`} type="number" min={-30} max={60} step={1}
                data-testid="settings-reminder-input" value={dayText} aria-describedby={daysHelp}
                aria-invalid={dayError ? true : undefined}
                onChange={(event) => { setDayText(event.target.value); setDayError(""); }}
                onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); addDay(); } }} />
            </label>
            <button type="button" className={secondary} data-testid="settings-reminder-add" onClick={addDay}>Add</button>
          </div>
          <p id={daysHelp} className={dayError ? errorText : helpText} data-testid="settings-reminder-help">
            {dayError || "Days from the due date: -3 is 3 days before, 0 is the due day. Up to 6, from -30 to 60."}</p>
        </fieldset>
        <div>
          <label className="flex flex-wrap items-center gap-2 text-sm font-medium">
            Pause a subscription when an invoice is overdue for
            <input className={`${field} w-20`} type="number" min={1} max={90} step={1} inputMode="numeric"
              data-testid="settings-pause-days" value={draft.suspend_after_days}
              aria-invalid={errors.suspend_after_days ? true : undefined} aria-describedby={pauseHelp}
              onChange={(event) => change({ suspend_after_days: event.target.value })} />
            days
          </label>
          <p id={pauseHelp} className={errors.suspend_after_days ? errorText : helpText}>
            {errors.suspend_after_days ?? "Leave it blank to never pause. From 1 to 90 days."}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button className={action} disabled={busy} data-testid="settings-save">{busy ? "Saving…" : "Save settings"}</button>
        </div>
        {error && <Notice error testId="settings-error">{error}</Notice>}
        {notice && <Notice testId="settings-notice">{notice}</Notice>}
      </form>
    </section>
  );
}

function Methods({ tenantId, admin, methods, onSaved, onConflict }: {
  tenantId: string | null; admin: boolean; methods: PaymentMethod[];
  onSaved: (method: PaymentMethod) => void; onConflict: () => Promise<PaymentMethod[]>;
}) {
  // "new", a method id being edited, or null.
  const [editing, setEditing] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const titleId = useId();
  const done = (saved: PaymentMethod, text: string) => {
    setEditing(null);
    setNotice(text);
    onSaved(saved);
  };
  return (
    <section aria-labelledby={titleId} data-testid="payment-methods" className={card}>
      <h2 id={titleId} className={`${sectionTitle} mb-1`}>Payment methods</h2>
      <p className={`${helpText} mb-4`}>
        How customers are told to pay. Each method shows the line customers read. Customers are shown only the
        first four active bank transfer and DuitNow methods in this list. A method can be turned off but not
        deleted.</p>
      {notice && <div className="mb-4"><Notice testId="methods-notice">{notice}</Notice></div>}
      {methods.length === 0 && editing !== "new" && (
        <p className="mb-4 text-sm text-ink-600" data-testid="methods-empty">No payment methods yet.</p>
      )}
      <ul className="space-y-3">
        {methods.map((method) => (
          <li key={method.id} data-testid="payment-method" className="rounded-md border border-ink-200 p-3 text-sm">
            {editing === method.id ? (
              <MethodForm tenantId={tenantId} method={method} onCancel={() => setEditing(null)}
                onDone={(saved) => done(saved, "Saved.")} onConflict={async () => (await onConflict()).find(
                  (row) => row.id === method.id) ?? null} />
            ) : (
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-medium">
                    <span data-testid="method-label">{method.label}</span>
                    <span className="text-ink-600"> · {methodLabel(method.kind)}</span>
                    {!method.is_active && <span data-testid="method-off"
                      className="ml-2 rounded-full bg-ink-100 px-2 text-xs text-ink-700">Off</span>}
                  </p>
                  <p className="mt-1 break-words text-ink-700">
                    <span className="text-ink-500">Customers see: </span>
                    <span data-testid="method-preview">{methodPreview(method)}</span>
                  </p>
                </div>
                {admin && (
                  <button type="button" className={secondary} data-testid="method-edit"
                    aria-label={`Edit ${method.label}`}
                    onClick={() => { setEditing(method.id); setNotice(""); }}>Edit</button>
                )}
              </div>
            )}
          </li>
        ))}
      </ul>
      {admin && (editing === "new" ? (
        <div className="mt-4">
          <MethodForm tenantId={tenantId} method={null} onCancel={() => setEditing(null)}
            onDone={(saved) => done(saved, "Added.")} onConflict={async () => null} />
        </div>
      ) : (
        <button type="button" className={`${secondary} mt-4`} data-testid="method-add"
          onClick={() => { setEditing("new"); setNotice(""); }}>Add a method</button>
      ))}
    </section>
  );
}

const FIELD_LABELS: Record<Exclude<MethodField, "instructions">, string> = {
  label: "Label (customers see it in the list)",
  bank_name: "Bank",
  account_name: "Account name",
  account_number: "Account number",
  duitnow_id: "DuitNow ID",
};

function MethodForm({ tenantId, method, onCancel, onDone, onConflict }: {
  tenantId: string | null; method: PaymentMethod | null; onCancel: () => void;
  onDone: (method: PaymentMethod) => void; onConflict: () => Promise<PaymentMethod | null>;
}) {
  // The method as last read: a version conflict replaces it with the latest.
  const [base, setBase] = useState(method);
  const [draft, setDraft] = useState<MethodDraft>(() => (method ? methodDraft(method) : emptyMethodDraft("transfer")));
  const [shown, setShown] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const kindRef = useRef<HTMLSelectElement>(null);
  const labelRef = useRef<HTMLInputElement>(null);
  const formId = useId();
  // A new method starts at its kind; an edit at its label.
  useEffect(() => { (kindRef.current ?? labelRef.current)?.focus(); }, []);
  const errors: MethodErrors = shown ? checkMethod(draft) : {};
  const changes = base ? methodChanges(draft, base) : null;
  const change = (next: Partial<MethodDraft>) => setDraft((current) => ({ ...current, ...next }));
  async function save(event: React.FormEvent) {
    event.preventDefault();
    if (busy) return;
    setShown(true);
    if (Object.keys(checkMethod(draft)).length) return;
    setBusy(true);
    setError("");
    try {
      if (base) {
        if (!changes) { onCancel(); return; }
        onDone(await updateMethod(tenantId, base.id, changes));
      } else {
        const body = newMethodBody(draft);
        if (body) onDone(await createMethod(tenantId, body));
      }
    } catch (cause) {
      if (base && isVersionConflict(cause)) {
        try {
          const latest = await onConflict();
          if (latest) { setBase(latest); setDraft(methodDraft(latest)); }
          setError(CONFLICT);
        } catch (reread) {
          setError(paymentMessage(reread));
        }
      } else {
        setError(setupMessage(cause));
      }
    } finally {
      setBusy(false);
    }
  }
  // No field here has a maxLength: a pasted label, name or number that is too long is refused whole by
  // checkMethod, never cut short and saved as something else that customers would be shown.
  const input = (name: Exclude<MethodField, "instructions">, extra: { autoComplete?: string }) => (
    <div>
      <label className="block text-sm" htmlFor={`${formId}-${name}`}>{FIELD_LABELS[name]}</label>
      <input id={`${formId}-${name}`} className={field} data-testid={`method-${name.replaceAll("_", "-")}`}
        value={draft[name]} autoComplete={extra.autoComplete ?? "off"}
        ref={name === "label" ? labelRef : undefined}
        aria-invalid={errors[name] ? true : undefined}
        aria-describedby={errors[name] ? `${formId}-${name}-error` : undefined}
        onChange={(event) => change({ [name]: event.target.value })} />
      {errors[name] && <p id={`${formId}-${name}-error`} className={errorText}>{errors[name]}</p>}
    </div>
  );
  return (
    <form className="space-y-3" data-testid="method-form" onSubmit={(event) => void save(event)} noValidate>
      <h3 className="font-semibold">{base ? `Edit ${base.label}` : "Add a payment method"}</h3>
      {!base ? (
        <label className="block text-sm">Kind
          <select ref={kindRef} className={field} data-testid="method-kind" value={draft.kind}
            onChange={(event) => change({ kind: event.target.value as MethodKind })}>
            {NEW_METHOD_KINDS.map((kind) => <option key={kind.value} value={kind.value}>{kind.label}</option>)}
          </select>
        </label>
      ) : <p className="text-sm text-ink-600">{methodLabel(base.kind)}</p>}
      {input("label", {})}
      {draft.kind === "transfer" && <>
        {input("bank_name", {})}
        {input("account_name", {})}
        {input("account_number", {})}
      </>}
      {draft.kind === "qr" && <>
        {input("duitnow_id", {})}
        {input("account_name", {})}
      </>}
      <div>
        <label className="block text-sm" htmlFor={`${formId}-instructions`}>Extra instructions (optional)</label>
        <textarea id={`${formId}-instructions`} className={textarea} maxLength={500} data-testid="method-instructions"
          value={draft.instructions} aria-invalid={errors.instructions ? true : undefined}
          aria-describedby={`${formId}-instructions-help`}
          onChange={(event) => change({ instructions: event.target.value })} />
        <p id={`${formId}-instructions-help`} className={errors.instructions ? errorText : helpText}>
          {errors.instructions ?? "Shown after the details, up to 500 characters."}</p>
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" className="size-4 accent-brand-600" data-testid="method-active" checked={draft.is_active}
          onChange={(event) => change({ is_active: event.target.checked })} />
        Active: offer this method to customers
      </label>
      <p className="rounded-md bg-ink-50 p-2 text-sm break-words">
        <span className="text-ink-500">Customers see: </span>
        <span data-testid="method-form-preview">{draftPreview(draft)}</span>
      </p>
      <div className="flex flex-wrap gap-2">
        <button className={action} disabled={busy || (base !== null && changes === null)} data-testid="method-save">
          {busy ? "Saving…" : base ? "Save" : "Add method"}</button>
        <button type="button" className={secondary} disabled={busy} onClick={onCancel}>Cancel</button>
      </div>
      {error && <Notice error testId="method-error">{error}</Notice>}
    </form>
  );
}
