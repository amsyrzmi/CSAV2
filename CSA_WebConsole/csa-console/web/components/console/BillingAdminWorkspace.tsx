"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { fetchOverview, type AdminOverviewOut } from "@/lib/billing-admin-api";
import {
  costPerExecutionText, cycleOptions, marginText, planStatus, rawCostText, tokenText, warningSentences, workbookNote,
} from "@/lib/billing-admin-view";
import { countText, cycleLabel, formatRm } from "@/lib/credit-view";
import { PlanEditor } from "./billing/PlanEditor";
import { FxForm, PriceForm } from "./billing/RateForms";
import { field, Heading, Notice, secondary } from "./ui";

const CARD = "rounded-[14px] border border-[var(--fs-border)] bg-[var(--fs-surface)] shadow-[var(--fs-shadow-card)]";
const cell = "border-b border-ink-100 px-3 py-2 align-top";
const head = "border-b border-ink-200 px-3 py-2 text-left align-bottom font-medium text-ink-600";
const amount = `${cell} text-right tabular-nums whitespace-nowrap`;
const amountHead = `${head} text-right`;

function message(cause: unknown): string {
  return cause instanceof Error && cause.message ? cause.message : "We could not load billing. Try again.";
}

/**
 * The platform team's Billing page: every company's usage, raw cost, tokens, bill and AI margin
 * for one cycle, each company's billing plan, and the price and FX forms. Platform admins only:
 * the page, the navigation and the API all check. Money is shown exactly as the API sends it.
 */
export function BillingAdminWorkspace() {
  const cycles = useMemo(() => cycleOptions(new Date()), []);
  // "" is the current cycle: the server picks it, so the first read needs no date.
  const [picked, setPicked] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [overview, setOverview] = useState<AdminOverviewOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<string | null>(null);
  const editor = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetchOverview(picked || null, controller.signal)
      .then((found) => { setOverview(found); setLoading(false); })
      .catch((cause) => {
        if (controller.signal.aborted) return;
        // A failed read must not leave another cycle's figures looking current.
        setOverview(null);
        setError(message(cause));
        setLoading(false);
      });
    return () => controller.abort();
  }, [picked, attempt]);

  useEffect(() => {
    if (editing) editor.current?.focus();
  }, [editing]);

  function reload() {
    setError("");
    setLoading(true);
    setAttempt((value) => value + 1);
  }

  function pick(cycle: string) {
    setError("");
    setLoading(true);
    setPicked(cycle);
  }

  const selected = picked || overview?.cycle_start || "";
  const company = overview?.companies.find((c) => c.client_id === editing) ?? null;
  return (
    <>
      <Heading
        title="Billing"
        description="Every company's usage, raw cost and AI margin for one billing cycle, their billing plans, and the model prices and exchange rate behind them. Platform team only."
      />
      <div className="mb-5 max-w-md">
        <label htmlFor="billing-cycle" className="mb-2 block text-sm">Billing cycle</label>
        <select id="billing-cycle" data-testid="billing-cycle" className={field} value={selected}
          disabled={loading && !overview} onChange={(event) => pick(event.target.value)}>
          {!selected && <option value="">Current cycle</option>}
          {cycles.map((cycle) => <option key={cycle.start} value={cycle.start}>{cycle.label}</option>)}
        </select>
      </div>
      {error ? (
        <div className="mb-6 space-y-3" data-testid="billing-error">
          <Notice error>{error}</Notice>
          <button type="button" className={secondary} onClick={reload}>Try again</button>
        </div>
      ) : !overview ? (
        <div role="status" aria-busy="true" className="mb-6" data-testid="billing-loading"><Notice>Loading billing...</Notice></div>
      ) : (
        <div aria-busy={loading} className={`mb-6 ${loading ? "opacity-60 transition-opacity" : "transition-opacity"}`}>
          <CompanyTable overview={overview} editing={editing} onEdit={setEditing} />
        </div>
      )}
      {company && (
        <section id="billing-plan-editor" className={`${CARD} mb-6 p-5 sm:p-6`} data-testid="billing-plan-editor" aria-labelledby="billing-plan-title">
          <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 id="billing-plan-title" ref={editor} tabIndex={-1}
                className="font-(family-name:--fs-font-heading) text-base font-semibold">
                Billing plan: {company.name}
              </h2>
              <p className="mt-1 text-sm text-ink-600">Now: {planStatus(company)}</p>
            </div>
            <button type="button" className={secondary} onClick={() => setEditing(null)}>Close</button>
          </div>
          <PlanEditor key={company.client_id} clientId={company.client_id} name={company.name}
            stage={company.stage} bundle={company.bundle} terminatedOn={company.terminated_on} onSaved={reload} />
        </section>
      )}
      <div className="grid items-start gap-5 xl:grid-cols-[repeat(2,minmax(0,1fr))]">
        <PriceForm onSaved={reload} />
        <FxForm onSaved={reload} />
      </div>
    </>
  );
}

const COLUMNS = 12;

function CompanyTable({ overview, editing, onEdit }: {
  overview: AdminOverviewOut; editing: string | null; onEdit: (clientId: string) => void;
}) {
  const dates = cycleLabel(overview.cycle_start, overview.cycle_end);
  return (
    <section className={`${CARD} p-4 sm:p-5`} data-testid="billing-companies" aria-labelledby="billing-companies-title">
      <h2 id="billing-companies-title" className="font-(family-name:--fs-font-heading) text-base font-semibold">
        Companies, {dates}
      </h2>
      <p className="mt-1 mb-3 text-sm text-ink-600" data-testid="billing-final">
        {overview.final ? "Closed cycle: these are the final figures." : "Open cycle: figures so far, final when the cycle ends."}
      </p>
      {overview.companies.length === 0 ? (
        <p className="text-sm text-ink-600" data-testid="billing-empty">No companies yet. Each company appears here once it is created on the Tenants page.</p>
      ) : (
        // `relative` keeps visually hidden text inside the scroll box, so the page never scrolls sideways.
        // Focusable so keyboard users can scroll it to the columns past the right edge.
        <div className="relative overflow-x-auto focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]" role="region" tabIndex={0} aria-labelledby="billing-companies-title">
          <table className="w-full min-w-[68rem] text-sm">
            <caption className="sr-only">Usage, raw cost, bill and AI margin per company for {dates}</caption>
            <thead><tr>
              <th scope="col" className={head}>Company</th>
              <th scope="col" className={head}>Plan</th>
              <th scope="col" className={amountHead}>Executions</th>
              <th scope="col" className={amountHead}>Tokens in</th>
              <th scope="col" className={amountHead}>Tokens out</th>
              <th scope="col" className={amountHead}>Raw cost</th>
              <th scope="col" className={amountHead}>AI charge</th>
              <th scope="col" className={amountHead}>Platform fee</th>
              <th scope="col" className={amountHead}>Billed total</th>
              <th scope="col" className={amountHead}>AI margin</th>
              <th scope="col" className={amountHead}>Cost per execution</th>
              <th scope="col" className={amountHead}>Unpriced calls</th>
            </tr></thead>
            {overview.companies.map((company) => {
              const warnings = warningSentences(company);
              const edge = warnings.length > 0 ? "border-b-0" : "";
              return (
                <tbody key={company.client_id} data-testid="billing-company-row">
                  <tr>
                    <th scope="row" className={`${cell} ${edge} text-left font-medium`}>
                      <span className="block">{company.name}</span>
                      {/* In the first column so it never scrolls out of view. */}
                      <button type="button" className={`${secondary} mt-2 whitespace-nowrap font-normal`}
                        aria-expanded={editing === company.client_id}
                        aria-controls={editing === company.client_id ? "billing-plan-editor" : undefined}
                        aria-label={`Billing plan for ${company.name}`} onClick={() => onEdit(company.client_id)}>
                        Billing plan
                      </button>
                    </th>
                    <td className={`${cell} ${edge}`}>{planStatus(company)}</td>
                    <td className={`${amount} ${edge}`}>{countText(company.executions)}</td>
                    <td className={`${amount} ${edge}`}>{tokenText(company.prompt_tokens)}</td>
                    <td className={`${amount} ${edge}`}>{tokenText(company.completion_tokens)}</td>
                    <td className={`${amount} ${edge}`}>{rawCostText(company)}</td>
                    <td className={`${amount} ${edge}`}>{formatRm(company.ai_charge, 2)}</td>
                    <td className={`${amount} ${edge}`}>{formatRm(company.platform_fee, 2)}</td>
                    <td className={`${amount} ${edge}`}>{formatRm(company.billed_total, 2)}</td>
                    <td className={`${amount} ${edge}`}>{marginText(company)}</td>
                    <td className={`${amount} ${edge}`}>
                      {costPerExecutionText(company)}
                      <span className="block text-xs text-ink-600">{workbookNote(company)}</span>
                    </td>
                    <td className={`${amount} ${edge}`}>{countText(company.unpriced_calls)}</td>
                  </tr>
                  {warnings.length > 0 && (
                    <tr>
                      <td colSpan={COLUMNS} className={`${cell} pt-0`} data-testid="billing-warnings">
                        <span className="font-medium">Needs attention for {company.name}:</span>
                        <ul className="mt-1 list-disc space-y-1 pl-5">
                          {warnings.map((sentence) => <li key={sentence}>{sentence}</li>)}
                        </ul>
                      </td>
                    </tr>
                  )}
                </tbody>
              );
            })}
          </table>
        </div>
      )}
    </section>
  );
}
