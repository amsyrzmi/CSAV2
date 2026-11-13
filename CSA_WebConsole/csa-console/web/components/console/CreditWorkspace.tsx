"use client";

import { Bot } from "lucide-react";
import { useEffect, useState } from "react";
import { avatarForAgent } from "@/lib/agent-avatars";
import { fetchCycles, fetchUsage, type CyclePickOut, type UsageOut } from "@/lib/credit-api";
import {
  agentSeries, banner, countText, cycleLabel, cycleOption, dailySeries, formatRm, newestFirst, proratedNote,
  stateMessage,
} from "@/lib/credit-view";
import AgentAvatar from "./fs/AgentAvatar";
import { AgentChart, DailyChart } from "./credit/CreditCharts";
import { field, Heading, Notice, secondary } from "./ui";

const CARD = "rounded-[14px] border border-[var(--fs-border)] bg-[var(--fs-surface)] shadow-[var(--fs-shadow-card)]";
const cell = "border-b border-ink-100 px-3 py-2 align-middle";
const head = "border-b border-ink-200 px-3 py-2 text-left font-medium text-ink-600";
const amount = `${cell} text-right tabular-nums whitespace-nowrap`;
const amountHead = `${head} text-right`;
// Below 640 px each table row stacks into labelled lines (as the Subscriptions table does), so the
// price is never scrolled out of view.
const phoneCell = "max-sm:border-b-0 max-sm:px-0 max-sm:py-0 max-sm:text-left max-sm:before:block max-sm:before:text-xs max-sm:before:text-ink-600 max-sm:before:content-[attr(data-label)]";
const phoneRow = "max-sm:grid max-sm:grid-cols-2 max-sm:gap-x-3 max-sm:gap-y-2 max-sm:border-b max-sm:border-ink-100 max-sm:py-3";

function message(cause: unknown): string {
  return cause instanceof Error && cause.message ? cause.message : "We could not load your usage. Try again.";
}

/**
 * The Credit tab: one billing cycle's AI Usage Charges and bill. The cycle picker scopes
 * everything below it; changing cycle keeps the last figures on screen, dimmed, until the new
 * ones arrive. Money is shown exactly as the server sends it, never added up here. The client
 * sees the charges per AI agent and per day and the execution count, never a list of single
 * executions (owner decision 2026-11-13).
 */
export function CreditWorkspace() {
  const [cycles, setCycles] = useState<CyclePickOut[] | null>(null);
  const [cyclesFailed, setCyclesFailed] = useState(false);
  // "" is the open cycle: the server picks it, so the first read needs no date.
  const [picked, setPicked] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [usage, setUsage] = useState<UsageOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    fetchCycles(controller.signal)
      .then((rows) => setCycles(newestFirst(rows)))
      .catch(() => { if (!controller.signal.aborted) setCyclesFailed(true); });
    return () => controller.abort();
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    fetchUsage(picked || null, controller.signal)
      .then((found) => {
        // A superseded read (the cycle changed) must not overwrite the newer cycle's figures.
        if (controller.signal.aborted) return;
        setUsage(found);
        setLoading(false);
      })
      .catch((cause) => {
        if (controller.signal.aborted) return;
        // A failed read for a new cycle must not leave the old cycle's figures looking current.
        setUsage(null);
        setError(message(cause));
        setLoading(false);
      });
    return () => controller.abort();
  }, [picked, attempt]);

  function pick(cycle: string) {
    setError("");
    setLoading(true);
    setPicked(cycle);
  }

  function retry() {
    setError("");
    setLoading(true);
    setAttempt((value) => value + 1);
  }

  const selected = picked || usage?.cycle_start || "";
  return (
    <>
      <Heading title="Credit" description="Your company's AI Usage Charges and bill for each billing cycle." />
      <div className="mb-5 max-w-md">
        {cycles ? (
          <>
            <label htmlFor="credit-cycle" className="mb-2 block text-sm">Billing cycle</label>
            <select id="credit-cycle" data-testid="credit-cycle" className={field} value={selected}
              disabled={loading && !usage} onChange={(event) => pick(event.target.value)}>
              {!selected && <option value="">Current cycle</option>}
              {cycles.map((cycle) => (
                <option key={cycle.start} value={cycle.start}>{cycleOption(cycle)}</option>
              ))}
            </select>
          </>
        ) : cyclesFailed ? (
          <Notice warning testId="credit-cycles-failed">
            We could not load the list of billing cycles. This shows the current cycle. Reload the page to choose another.
          </Notice>
        ) : null}
      </div>
      {error ? (
        <div className="space-y-3" data-testid="credit-error">
          <Notice error>{error}</Notice>
          <button type="button" className={secondary} onClick={retry}>Try again</button>
        </div>
      ) : !usage ? (
        <div role="status" aria-busy="true" data-testid="credit-loading"><Notice>Loading your usage...</Notice></div>
      ) : (
        <div aria-busy={loading} className={loading ? "opacity-60 transition-opacity" : "transition-opacity"}>
          <CycleView key={usage.cycle_start} usage={usage} />
        </div>
      )}
    </>
  );
}

function CycleView({ usage }: { usage: UsageOut }) {
  const dates = cycleLabel(usage.cycle_start, usage.cycle_end);
  if (usage.status === "not_set_up") {
    // No plan for this cycle: no figures at all, so nothing reads as a bill of RM 0.00.
    return (
      <section aria-label={`Billing cycle ${dates}`} data-testid="credit-not-set-up">
        <Notice>{stateMessage(usage.status)}. Your AI Usage Charges and bill will show here once your plan is set up.</Notice>
      </section>
    );
  }
  const open = banner(usage);
  const prorated = proratedNote(usage);
  const pending = stateMessage(usage.status);
  const agents = agentSeries(usage);
  return (
    <section aria-label={`Billing cycle ${dates}`} className="space-y-6" data-testid="credit-cycle-view">
      {(open || prorated || pending) && (
        <div className="space-y-2">
          {open && <Notice testId="credit-banner">{open}</Notice>}
          {prorated && <p className="text-sm text-ink-600" data-testid="credit-prorated">{prorated}</p>}
          {pending && (
            <Notice warning testId="credit-pending">
              {pending}. It is added to the AI Usage Charges once it is priced.
            </Notice>
          )}
        </div>
      )}
      <Cards usage={usage} />
      <AgentTable usage={usage} />
      {agents.length > 0 && (
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4 xl:grid-cols-[repeat(2,minmax(0,1fr))]">
          <DailyChart rows={dailySeries(usage)} />
          <AgentChart rows={agents} />
        </div>
      )}
    </section>
  );
}

function Cards({ usage }: { usage: UsageOut }) {
  const cards = [
    { id: "executions", label: "Executions", value: countText(usage.executions) },
    { id: "ai-usage-charges", label: "AI Usage Charges", value: formatRm(usage.ai_usage_price, 2) },
    { id: "platform-fee", label: "Platform fee", value: formatRm(usage.platform_fee, 2) },
    { id: "bill", label: usage.final ? "Bill" : "Bill so far", value: formatRm(usage.total, 2) },
  ];
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4" data-testid="credit-cards">
      {cards.map((card) => (
        <div key={card.id} role="group" aria-labelledby={`credit-card-label-${card.id}`}
          data-testid={`credit-card-${card.id}`} className={`flex min-w-0 flex-col gap-2 p-4 ${CARD}`}>
          <p id={`credit-card-label-${card.id}`} className="text-sm text-ink-700">{card.label}</p>
          <p className="font-(family-name:--fs-font-heading) text-2xl font-semibold [overflow-wrap:anywhere]">{card.value}</p>
        </div>
      ))}
    </div>
  );
}

function AgentMark({ agentId }: { agentId: string }) {
  const avatar = avatarForAgent(agentId);
  return (
    <span className="inline-flex size-8 flex-none items-center justify-center text-[color:var(--fs-text-secondary)]">
      {avatar ? <AgentAvatar agentId={avatar} size={32} label="" /> : <Bot aria-hidden="true" className="size-5" />}
    </span>
  );
}

function AgentTable({ usage }: { usage: UsageOut }) {
  return (
    <section className={`${CARD} p-4 sm:p-5`} data-testid="credit-agents">
      <h2 className="mb-3 font-(family-name:--fs-font-heading) text-base font-semibold">By AI agent</h2>
      {usage.by_agent.length === 0 ? (
        <p className="text-sm text-ink-600" data-testid="credit-agents-empty">
          No AI Usage Charges in this cycle yet. Each AI agent your company uses will appear here with its charges.
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">AI Usage Charges by AI agent</caption>
            <thead className="max-sm:sr-only"><tr>
              <th scope="col" className={head}>AI agent</th>
              <th scope="col" className={amountHead}>Executions</th>
              <th scope="col" className={amountHead}>AI Usage Charges</th>
            </tr></thead>
            <tbody>
              {usage.by_agent.map((agent) => (
                <tr key={agent.agent_id} data-testid="credit-agent-row" className={phoneRow}>
                  <th scope="row" className={`${cell} text-left font-normal max-sm:col-span-2 max-sm:border-b-0 max-sm:p-0`}>
                    <span className="flex items-center gap-2"><AgentMark agentId={agent.agent_id} />{agent.title}</span>
                  </th>
                  <td data-label="Executions" className={`${amount} ${phoneCell}`}>{countText(agent.executions)}</td>
                  <td data-label="AI Usage Charges" className={`${amount} ${phoneCell}`}>{formatRm(agent.price, 4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
