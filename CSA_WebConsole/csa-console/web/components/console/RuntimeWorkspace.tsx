"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api, patch } from "@/lib/api";
import type {
  Agent, AgentRun, Client, Me, RunPage, RuntimeEvent, RuntimeOverview,
} from "@/lib/identity-contracts";
import { field, Heading, Notice, Panel, secondary } from "./ui";

export function RuntimeWorkspace({
  me, mode, initialAgent = "", initialClientId = "",
}: {
  me: Me;
  mode: "agents" | "activity";
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
      <Heading title={mode === "agents" ? "Agents" : "Runtime activity"}
        description="CSAV2 capability controls and recorded execution metadata. No legacy workflow metrics." />
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
        mode === "agents" ?
          <AgentManagement key={clientId} query={query} platform={platform} /> :
          <RuntimeActivity key={clientId} query={query} initialAgent={initialAgent} />}
    </>
  );
}

function AgentManagement({ query, platform }: { query: string; platform: boolean }) {
  const [agents, setAgents] = useState<Agent[] | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const [selected, setSelected] = useState("");
  const path = `/console/v1/agents?${query}`;
  useEffect(() => {
    const controller = new AbortController();
    api<Agent[]>(path, { signal: controller.signal })
      .then((rows) => { setAgents(rows); setError(""); })
      .catch((cause: Error) => {
        if (!controller.signal.aborted) setError(cause.message);
      });
    return () => controller.abort();
  }, [path, revision]);
  async function toggle(agent: Agent, switchName: "is_enabled" | "client_enabled") {
    const capability = agent.capability;
    if (!capability) return;
    setBusy(true);
    setError("");
    try {
      await patch(`/console/v1/capabilities/${encodeURIComponent(agent.name)}?${query}`,
        { [switchName]: !capability[switchName] });
      setAgents(await api<Agent[]>(path));
    } catch (cause) {
      setError((cause as Error).message);
      // Controls must not use stale switch values after a failed refresh.
      setAgents(null);
    } finally {
      setBusy(false);
    }
  }
  const detail = agents?.find((agent) => agent.name === selected && agent.can_view_specialized);
  return (
    <div className="space-y-5">
      {error && <Notice error>{error}</Notice>}
      <button className={secondary} disabled={busy} onClick={() => setRevision((value) => value + 1)}>Refresh agents</button>
      <Notice>Subscription, tenant switch, implementation, setup and circuit are independent.
        Both switches on does not mean a provider is connected or healthy. Changes affect the next execution attempt; already-started work is not cancelled.</Notice>
      {agents && (
        <nav aria-label="Specialized agents" className="flex flex-wrap gap-2">
          {agents.filter((agent) => agent.can_view_specialized).map((agent) =>
            <button key={agent.name} className={secondary} onClick={() => setSelected(agent.name)}>
              {agent.title}: {agent.implementation === "prepared_only" ? "prepared only" : label(agent.readiness)}
            </button>)}
        </nav>
      )}
      {detail && <Panel title={detail.title}>
        <p className="mb-4 text-sm">{detail.detail}</p>
        <Link className={secondary} href={`${platform ? "" : "/portal"}/activity?${query}&agent=${encodeURIComponent(detail.name)}`}>
          View recorded activity
        </Link>
        {detail.name === "kb_agent" ? <Link className={`${secondary} mt-3`} href={`${platform ? "/knowledge-base" : "/portal/faq"}?${query}`}>Open knowledge base</Link> :
          <p className="mt-3 text-sm text-ink-500">Feature-specific actions are adopted in later migration phases.</p>}
      </Panel>}
      {!agents ? <Notice>{error ? "Agent state unavailable. Refresh to retry." : "Loading agents..."}</Notice> :
        <div className="grid gap-5 xl:grid-cols-2">
          {agents.map((agent) => <Panel key={agent.name} title={agent.title}>
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <div><dt className="text-ink-500">Subscription</dt><dd>{agent.capability?.is_enabled ? "Subscribed" : "Not subscribed"}</dd></div>
              <div><dt className="text-ink-500">Tenant switch</dt><dd>{agent.capability?.client_enabled ? "On" : "Off"}</dd></div>
              <div><dt className="text-ink-500">Implementation</dt><dd>{label(agent.implementation)}</dd></div>
              <div><dt className="text-ink-500">Setup</dt><dd>{label(agent.readiness)}</dd></div>
              <div><dt className="text-ink-500">Circuit</dt><dd>{agent.capability?.circuit_state ?? "Not registered"}</dd></div>
              <div><dt className="text-ink-500">Failure count</dt><dd>{agent.capability?.fail_count ?? "Not registered"}</dd></div>
            </dl>
            <p className="my-4 text-sm text-ink-600">{agent.detail}</p>
            {agent.embedding_model && <p className="mb-4 text-sm">
              Embeddings: {agent.embedding_model}. Compatible chunks: {agent.compatible_chunks ?? "Unknown"};
              incompatible chunks: {agent.incompatible_chunks ?? "Unknown"}.
            </p>}
            {agent.required_system && <p className="mb-3 text-sm">Required safety capability; disabling is prohibited.</p>}
              <div className="flex flex-wrap gap-2">
                {agent.can_subscribe && <button className={secondary} disabled={busy}
                  onClick={() => toggle(agent, "is_enabled")}>
                  {agent.capability?.is_enabled ? "Remove subscription" : "Subscribe"}
                </button>}
                {agent.can_toggle && <button className={secondary} disabled={busy}
                  onClick={() => toggle(agent, "client_enabled")}>
                  Turn tenant switch {agent.capability?.client_enabled ? "off" : "on"}
                </button>}
              </div>
          </Panel>)}
        </div>}
    </div>
  );
}

function label(value: string) { return value.replaceAll("_", " "); }

function RuntimeActivity({ query, initialAgent }: { query: string; initialAgent: string }) {
  const [agent, setAgent] = useState(initialAgent);
  const [filter, setFilter] = useState(initialAgent);
  const [before, setBefore] = useState<number | null>(null);
  const [page, setPage] = useState<RunPage | null>(null);
  const [overview, setOverview] = useState<RuntimeOverview | null>(null);
  const [selected, setSelected] = useState<AgentRun | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      try {
        const params = new URLSearchParams(query);
        params.set("limit", "25");
        if (before !== null) params.set("before_id", String(before));
        if (filter) params.set("agent", filter);
        const [next, summary] = await Promise.all([
          api<RunPage>(`/console/v1/runs?${params}`, { signal: controller.signal }),
          api<RuntimeOverview>(`/console/v1/overview?${query}`, { signal: controller.signal }),
        ]);
        if (!controller.signal.aborted) {
          setPage(next); setOverview(summary); setError("");
        }
      } catch (cause) {
        if (!controller.signal.aborted) {
          setError((cause as Error).message); setPage(null); setOverview(null);
        }
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(refresh, 5000);
      }
    }
    void refresh();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [query, before, filter, revision]);
  return (
    <div className="space-y-5">
      {error && <Notice error>{error}</Notice>}
      {overview && <Panel title="Last 24 hours (all agents)">
        <p className="text-sm">{overview.runs} recorded runs; {overview.succeeded} succeeded; {overview.failed} failed.</p>
        <p className="mt-2 text-sm">Last run: {overview.last_run_at ? new Date(overview.last_run_at).toLocaleString() : "No runs in this window"}.</p>
      </Panel>}
      <form className="flex flex-wrap gap-3" onSubmit={(event) => {
        event.preventDefault(); setFilter(agent.trim()); setBefore(null);
        setPage(null); setSelected(null); setRevision((value) => value + 1);
      }}>
        <label className="flex-1 text-sm">Agent name (optional)
          <input className={field} value={agent} maxLength={100} onChange={(event) => setAgent(event.target.value)} />
        </label>
        <button className={secondary}>Filter / refresh</button>
      </form>
      <Notice>Polling every 5 seconds. Execution metadata only; no message text, raw inputs or outputs.
        An empty list means no recorded executions, not proof that a provider is healthy.</Notice>
      <Panel title="Recorded runs">
        {!page ? <p>{error ? "Runtime data unavailable." : "Loading runs..."}</p> :
          page.items.length === 0 ? <p>No recorded runs for this selection.</p> :
            <ul className="divide-y divide-ink-200">{page.items.map((run) => <li key={run.id}>
              <button className="w-full py-4 text-left text-sm hover:text-brand-600" onClick={() => setSelected(run)}>
                #{run.id} {run.agent} / {run.status} / {run.error_code ?? (run.ok ? "Succeeded" : "Failed")}
                <span className="mt-1 block text-ink-500">{new Date(run.created_at).toLocaleString()} / {run.latency_ms ?? "Unknown"} ms</span>
              </button>
            </li>)}</ul>}
        <div className="mt-4 flex gap-3">
          <button className={secondary} disabled={before === null} onClick={() => { setBefore(null); setPage(null); }}>Newest</button>
          <button className={secondary} disabled={!page?.next_before_id} onClick={() => {
            setBefore(page?.next_before_id ?? null); setPage(null);
          }}>Older runs</button>
        </div>
      </Panel>
      {selected && <RunDetail key={selected.id} run={selected} query={query} />}
    </div>
  );
}

function RunDetail({ run, query }: { run: AgentRun; query: string }) {
  const [detail, setDetail] = useState<AgentRun | null>(null);
  const [events, setEvents] = useState<RuntimeEvent[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    let cursor = 0;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const params = new URLSearchParams(query);
        params.set("trace_id", run.trace_id);
        params.set("after_id", String(cursor));
        const [current, next] = await Promise.all([
          api<AgentRun>(`/console/v1/runs/${run.id}?${query}`, { signal: controller.signal }),
          api<RuntimeEvent[]>(`/console/v1/events?${params}`, { signal: controller.signal }),
        ]);
        if (!controller.signal.aborted) {
          setDetail(current);
          setEvents((previous) => [...previous, ...next]);
          if (next.length) cursor = next[next.length - 1].id;
          setError("");
        }
      } catch (cause) {
        if (!controller.signal.aborted) setError((cause as Error).message);
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(poll, 5000);
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [run.id, run.trace_id, query]);
  return <Panel title={`Run #${run.id}`}>
    {error && <Notice error>{error} (previously loaded detail may be stale)</Notice>}
    {detail && <p className="mb-4 break-all text-sm">{detail.agent}: {detail.status}. Trace: {detail.trace_id}</p>}
    <h3 className="mb-3 text-sm font-semibold">Trace events</h3>
    {events.length === 0 ? <p className="text-sm">{detail ? "No recorded events yet." : "Loading detail..."}</p> :
      <ol className="space-y-2 text-sm">{events.map((event) =>
        <li key={event.id}>#{event.id} {event.node} / {event.agent ?? "Coordinator"} / {event.status} / {event.duration_ms} ms</li>)}</ol>}
  </Panel>;
}
