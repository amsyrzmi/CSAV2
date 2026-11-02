"use client";

import { Receipt } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  agentOptions, lastAssistants, popCursor, pushCursor, runsQuery,
  type LastAssistant, type StatusFilter,
} from "@/lib/activity-view";
import { avatarForAgent } from "@/lib/agent-avatars";
import { api } from "@/lib/api";
import { toInstants, type DayRange } from "@/lib/date-range";
import type {
  Agent, AgentRun, Client, Me, RunPage, RuntimeOverview,
} from "@/lib/identity-contracts";
import { buildText, connectionText, lastChangeText, SAVED_NOT_REFRESHED, setupText } from "@/lib/services-view";
import LastAssistants from "./activity/LastAssistants";
import RunRow from "./activity/RunRow";
import AgentAvatar from "./fs/AgentAvatar";
import { useAvatarTransition } from "./fs/useAvatarTransition";
import { DateRangeFilter, PageIntro, StatusChips } from "./kit";
import { field, Notice, Panel, secondary } from "./ui";

type SwitchName = "is_enabled" | "client_enabled";

export function RuntimeWorkspace({
  me, initialAgent = "", initialClientId = "",
}: {
  me: Me;
  initialAgent?: string;
  initialClientId?: string;
}) {
  const platform = me.role === "platform_admin";
  const [clients, setClients] = useState<Client[] | null>(null);
  const [selected, setSelected] = useState(initialClientId);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!platform) return;
    const controller = new AbortController();
    api<Client[]>("/console/v1/admin/clients", { signal: controller.signal })
      .then(setClients)
      .catch((cause: Error) => {
        if (!controller.signal.aborted) setError(cause.message);
      });
    return () => controller.abort();
  }, [platform]);
  const clientId = platform ? selected : me.active_client_id;
  const query = platform ? `client_id=${encodeURIComponent(selected)}` : "";
  return (
    <>
      <h1 className="page-title mb-2 font-(family-name:--fs-font-heading) text-(length:--fs-text-page) font-semibold">
        Assistant activity
      </h1>
      <PageIntro>
        What your assistants did recently: each run, whether it worked and how long it took. Message text is never shown.
      </PageIntro>
      {error && <Notice error>{error}</Notice>}
      {platform && (
        <div className="mb-5">
          <label htmlFor="runtime-tenant" className="mb-2 block text-sm">Tenant</label>
          <select id="runtime-tenant" className={field} value={selected}
            onChange={(event) => setSelected(event.target.value)}>
            <option value="">{clients ? "Select a tenant" : "Loading tenants..."}</option>
            {clients?.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
          </select>
        </div>
      )}
      {!clientId ? <Notice>Select a tenant with an active membership to continue.</Notice> :
        <RuntimeActivity key={clientId} query={query} initialAgent={initialAgent} />}
    </>
  );
}

/**
 * Agents that are planned but not built yet. Listed by name rather than inferred from "has no
 * avatar", so a future agent that simply lacks art is not mislabelled as planned.
 */
const PLANNED_AGENTS: ReadonlySet<string> = new Set(["lhdn_agent"]);

/**
 * One agent: avatar chip, title, status words, the details, and the two switches.
 *
 * The avatar follows the server, never the click. `pending` (the switch being saved) shows a
 * still, dimmed avatar and a "Saving..." label; when it clears, `failed` restores the old look
 * and shows the reason, a newly active capability plays the short wake, a newly inactive one
 * settles, and an unchanged one just restores. `saved` means the change was saved but the
 * list reload after it failed: the card says so (not a failure) and nothing animates, because
 * no reloaded state confirms it. A change nobody made here (a reload or a poll)
 * is shown at once without a wake, because the resting look is derived from `capability.active`
 * and only the transition phases come from the hook. Parents must keep `key` stable (the agent
 * name) so a reload while pending does not remount the card and lose the pending request.
 */
export function AgentCard({ agent, busy, onToggle, pending = null, failed = null, saved = false }: {
  agent: Agent; busy: boolean;
  onToggle: (agent: Agent, switchName: SwitchName) => void;
  pending?: SwitchName | null;
  failed?: string | null;
  saved?: boolean;
}) {
  const active = Boolean(agent.capability?.active);
  const avatar = avatarForAgent(agent.name);
  const transition = useAvatarTransition(active ? "idle" : "still");
  const { start, confirm, fail } = transition;
  const previous = useRef<SwitchName | null>(null);
  const activeAtStart = useRef(active);
  useEffect(() => {
    const before = previous.current;
    if (pending === before) return;
    previous.current = pending;
    if (pending) {
      activeAtStart.current = active;
      start();
    } else if (failed || active === activeAtStart.current) {
      fail();
    } else {
      confirm(active ? "active" : "inactive");
    }
  }, [pending, failed, active, start, confirm, fail]);
  const resting = transition.phase === "idle" || transition.phase === "still";
  const state = pending ? "pending" : resting ? (active ? "idle" : "still") : transition.state;
  const subscribed = agent.capability?.is_enabled;
  const switchOn = agent.capability?.client_enabled;
  const name = agent.name;
  const planned = PLANNED_AGENTS.has(name);
  const lastChange = lastChangeText(agent.capability?.last_change);
  return (
    <article data-testid={`agent-card-${name}`}
      className="rounded-[14px] border border-[var(--fs-border)] bg-[var(--fs-surface)] p-5 shadow-[var(--fs-shadow-card)] sm:p-6">
      <header className="mb-4 flex items-start gap-3">
        {avatar ? <AgentAvatar agentId={avatar} size={48} label="" state={state} /> :
          <span aria-hidden="true"
            className="inline-flex size-12 flex-none items-center justify-center text-[color:var(--fs-text-secondary)]">
            <Receipt className="size-6" />
          </span>}
        <div className="min-w-0 flex-1">
          {/* h4: the card sits under a service row whose title is the h3. */}
          <h4 className="font-(family-name:--fs-font-heading) text-base font-semibold">{agent.title}</h4>
          <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
            {planned && <span data-testid={`agent-planned-${name}`} className="font-medium">Planned</span>}
            {agent.capability && <span data-testid={`agent-status-${name}`}
              className="font-medium text-ink-700">{active ? "Active" : "Not active"}</span>}
          </p>
          {lastChange && <p data-testid={`agent-last-change-${name}`}
            className="mt-1 text-sm text-ink-600 [overflow-wrap:anywhere]">{lastChange}</p>}
          {/* Always rendered (empty when idle) so assistive technology announces the change reliably. */}
          <span role="status" data-testid={`agent-pending-${name}`}
            className={pending ? "mt-1 block text-sm text-ink-600" : "block"}>{pending ? "Saving..." : ""}</span>
        </div>
      </header>
      <dl className="grid grid-cols-2 gap-3 text-sm">
        <div><dt className="text-ink-500">Subscription</dt><dd>{subscribed ? "Subscribed" : "Not subscribed"}</dd></div>
        <div><dt className="text-ink-500">Company switch</dt><dd>{switchOn ? "On" : "Off"}</dd></div>
        <div><dt className="text-ink-500">Build</dt><dd>{buildText(agent.implementation)}</dd></div>
        <div><dt className="text-ink-500">Setup</dt><dd>{setupText(agent.readiness)}</dd></div>
        <div><dt className="text-ink-500">Failure guard</dt><dd>{agent.capability ? connectionText(agent.capability.circuit_state) : "No record yet"}</dd></div>
        <div><dt className="text-ink-500">Recent failures</dt><dd>{agent.capability?.fail_count ?? "No record yet"}</dd></div>
      </dl>
      <p className="my-4 text-sm text-ink-600">{agent.detail}</p>
      {agent.embedding_model && <p className="mb-4 text-sm">
        Embeddings: {agent.embedding_model}. Compatible chunks: {agent.compatible_chunks ?? "Unknown"};
        incompatible chunks: {agent.incompatible_chunks ?? "Unknown"}.
      </p>}
      {agent.required_system && <p className="mb-3 text-sm">Required safety capability; disabling is prohibited.</p>}
      {failed && <div className="mb-3"><Notice error>{failed}</Notice></div>}
      {saved && !failed && <div className="mb-3"><Notice warning>{SAVED_NOT_REFRESHED}</Notice></div>}
      <div className="flex flex-wrap gap-2">
        {agent.can_subscribe && <button className={secondary} disabled={busy}
          data-testid={`agent-subscribe-${name}`}
          onClick={() => onToggle(agent, "is_enabled")}>
          {subscribed ? "Remove subscription" : "Subscribe"}
        </button>}
        {agent.can_toggle && <button className={secondary} disabled={busy}
          data-testid={`agent-switch-${name}`}
          onClick={() => onToggle(agent, "client_enabled")}>
          Turn company switch {switchOn ? "off" : "on"}
        </button>}
      </div>
    </article>
  );
}

/** The current time, renewed every `ms`, so "3 minutes ago" keeps up while the page stays open. */
function useClock(ms: number): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), ms);
    return () => clearInterval(timer);
  }, [ms]);
  return now;
}

const PAGE_SIZE = 5;
const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "ok", label: "Worked" },
  { value: "error", label: "Needed attention" },
];

function RuntimeActivity({ query, initialAgent }: { query: string; initialAgent: string }) {
  const [agent, setAgent] = useState(initialAgent);
  const [status, setStatus] = useState<StatusFilter>("all");
  const [range, setRange] = useState<DayRange>({ from: null, to: null });
  // The `before_id` cursors of the pages already passed; the last one is the page on screen.
  const [stack, setStack] = useState<number[]>([]);
  const before = stack.length ? stack[stack.length - 1] : null;
  const instants = useMemo(() => toInstants(range), [range]);
  const [page, setPage] = useState<RunPage | null>(null);
  const [error, setError] = useState("");
  const [cards, setCards] = useState<LastAssistant[] | null>(null);
  const [cardsFailed, setCardsFailed] = useState(false);
  const [overview, setOverview] = useState<RuntimeOverview | null>(null);
  // Open rows are kept by run id, so a refresh (or a page change and back) never closes them.
  const [openIds, setOpenIds] = useState<ReadonlySet<number>>(() => new Set());
  const now = useClock(30_000);
  const newest = before === null;
  const filtered = Boolean(agent) || status !== "all" || range.from !== null || range.to !== null;
  const options = agentOptions();
  const known = options.some((option) => option.value === initialAgent);

  // The list: refreshed every 5 seconds on the newest page only. A filter or page change
  // restarts it, which also drops any read still in flight for the old choice.
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const params = runsQuery(query, {
          limit: PAGE_SIZE, beforeId: before, agent, status, from: instants.from, to: instants.to,
        });
        const next = await api<RunPage>(`/console/v1/runs?${params}`, { signal: controller.signal });
        if (!controller.signal.aborted) { setPage(next); setError(""); }
      } catch (cause) {
        if (!controller.signal.aborted) { setError((cause as Error).message); setPage(null); }
      } finally {
        if (!controller.signal.aborted && before === null) timer = setTimeout(refresh, 5000);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [query, before, agent, status, instants.from, instants.to]);

  // The last-assistants strip and the 24-hour line do not depend on the filters. They load once
  // and then refresh with the list, on the newest page only.
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function load() {
      const [runs, summary] = await Promise.allSettled([
        api<RunPage>(`/console/v1/runs?${runsQuery(query, { limit: 100 })}`, { signal: controller.signal }),
        api<RuntimeOverview>(`/console/v1/overview?${query}`, { signal: controller.signal }),
      ]);
      if (controller.signal.aborted) return;
      if (runs.status === "fulfilled") { setCards(lastAssistants(runs.value.items)); setCardsFailed(false); }
      else setCardsFailed(true);
      setOverview(summary.status === "fulfilled" ? summary.value : null);
      if (newest) timer = setTimeout(load, 5000);
    }
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [query, newest]);

  // A filter that did not really change (the same chip, the same day, Clear when already clear)
  // does nothing: no new read, no loading flash, paging kept.
  function change(changed: boolean, apply: () => void) {
    if (!changed) return;
    apply(); setStack([]); setPage(null);
  }
  function setOpen(id: number, open: boolean) {
    setOpenIds((current) => {
      const next = new Set(current);
      if (open) next.add(id); else next.delete(id);
      return next;
    });
  }
  return (
    <div className="space-y-5">
      {error && <Notice error>{error}</Notice>}
      {overview && <p data-testid="activity-overview" className="text-sm text-[color:var(--fs-text-secondary)]">
        Last 24 hours: {overview.runs} {overview.runs === 1 ? "run" : "runs"}, {overview.failed} needed attention
      </p>}
      <LastAssistants cards={cards} failed={cardsFailed} now={now} />
      <div className="flex flex-wrap items-end gap-3">
        <DateRangeFilter testId="activity-date" value={range}
          onChange={(next) => change(next.from !== range.from || next.to !== range.to, () => setRange(next))} />
        <div className="w-full sm:w-72">
          <label htmlFor="activity-agent" className="mb-1 block text-sm">Assistant</label>
          <select id="activity-agent" data-testid="activity-agent" className={field} value={agent}
            onChange={(event) => change(event.target.value !== agent, () => setAgent(event.target.value))}>
            <option value="">All assistants</option>
            {initialAgent && !known && <option value={initialAgent}>{initialAgent}</option>}
            {options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
          </select>
        </div>
        <StatusChips mode="single" testId="activity-status" ariaLabel="Outcome" options={STATUS_OPTIONS}
          value={status} onChange={(next) => change(next !== status, () => setStatus(next as StatusFilter))} />
      </div>
      <Notice>This list refreshes every 5 seconds. It shows when each run happened, not what was said.
        An empty list only means nothing has run yet; it does not mean everything is healthy.</Notice>
      <Panel title="Recorded runs">
        {!page ? <p>{error ? "Runtime data unavailable." : "Loading runs..."}</p> :
          page.items.length === 0 ? <p>{filtered ? "Nothing has been recorded for this selection." : "Nothing has run yet. Runs appear here as your assistants work."}</p> :
            <ul data-testid="activity-list" className="divide-y divide-[color:var(--fs-border)]">
              {page.items.map((run: AgentRun) => <li key={run.id} className="py-3 first:pt-0 last:pb-0">
                <RunRow run={run} query={query} now={now} open={openIds.has(run.id)}
                  onOpenChange={(open) => setOpen(run.id, open)} />
              </li>)}
            </ul>}
        <div className="mt-4 flex gap-3">
          <button className={secondary} data-testid="activity-newer" disabled={stack.length === 0}
            onClick={() => { setStack(popCursor(stack)); setPage(null); }}>Newer</button>
          <button className={secondary} data-testid="activity-older" disabled={!page?.next_before_id}
            onClick={() => {
              if (!page?.next_before_id) return;
              setStack(pushCursor(stack, page.next_before_id)); setPage(null);
            }}>Older</button>
        </div>
      </Panel>
    </div>
  );
}
