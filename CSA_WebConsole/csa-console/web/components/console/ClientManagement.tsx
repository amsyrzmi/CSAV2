"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, post, patch } from "@/lib/api";
import { fetchOverview, type AdminOverviewOut } from "@/lib/billing-admin-api";
import { currentPlanOf, planStatus } from "@/lib/billing-admin-view";
import type { Agent, Capability, Client } from "@/lib/identity-contracts";
import { PlanEditor } from "./billing/PlanEditor";
import { action, field, Heading, Notice, Panel } from "./ui";

export function ClientManagement() {
  const [clients, setClients] = useState<Client[] | null>(null);
  const [selected, setSelected] = useState("");
  const selection = useRef("");
  const [capabilities, setCapabilities] = useState<Agent[] | null>(null);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [timezone, setTimezone] = useState("Asia/Kuala_Lumpur");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // The open cycle's overview: the only place each tenant's current plan is read from. Until it
  // loads (or if it fails, or does not list the tenant) the plan editor treats the plan as unknown.
  const [overview, setOverview] = useState<AdminOverviewOut | null>(null);
  const [overviewRead, setOverviewRead] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    fetchOverview(null, controller.signal)
      .then((found) => {
        if (controller.signal.aborted) return;
        setOverview(found);
      })
      .catch(() => {
        if (controller.signal.aborted) return;
        // Unknown, not guessed: the editor says so and asks for an explicit choice.
        setOverview(null);
      });
    return () => controller.abort();
  }, [overviewRead]);
  const rereadPlans = () => setOverviewRead((value) => value + 1);
  useEffect(() => {
    let live = true;
    api<Client[]>("/console/v1/admin/clients")
      .then((rows) => {
        if (live) setClients(rows);
      })
      .catch((cause) => {
        if (live) setError((cause as Error).message);
      });
    return () => {
      live = false;
    };
  }, []);
  const plan = selected ? currentPlanOf(overview, selected) : null;
  async function select(id: string) {
    selection.current = id;
    setSelected(id);
    setCapabilities(null);
    setError("");
    if (!id) return;
    try {
      const rows = await api<Agent[]>(
        `/console/v1/agents?client_id=${encodeURIComponent(id)}`,
      );
      if (selection.current === id)
        setCapabilities(rows.filter((agent) => agent.capability !== null));
    } catch (cause) {
      if (selection.current === id) setError((cause as Error).message);
    }
  }
  async function create(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const row = await post<Client>("/console/v1/admin/clients", {
        slug,
        name,
        timezone,
      });
      setClients((previous) => [...(previous ?? []), row]);
      setName("");
      setSlug("");
      rereadPlans();
      await select(row.id);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function toggle(capability: Capability) {
    setBusy(true);
    setError("");
    try {
      await patch(
        `/console/v1/capabilities/${encodeURIComponent(capability.name)}?client_id=${encodeURIComponent(selected)}`,
        { is_enabled: !capability.is_enabled },
      );
      const rows = await api<Agent[]>(
        `/console/v1/agents?client_id=${encodeURIComponent(selected)}`,
      );
      setCapabilities(rows.filter((agent) => agent.capability !== null));
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Heading
        title="Tenants"
        description="Create isolated workspaces and provision capability subscriptions."
      />
      {error && (
        <div className="mb-5">
          <Notice error>{error}</Notice>
        </div>
      )}
      <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
        <Panel title="Create tenant">
          <form onSubmit={create} className="space-y-4">
            <div>
              <label htmlFor="tenant-name" className="mb-2 block text-sm">
                Business name
              </label>
              <input
                id="tenant-name"
                className={field}
                maxLength={160}
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="tenant-slug" className="mb-2 block text-sm">
                Workspace slug
              </label>
              <input
                id="tenant-slug"
                className={field}
                pattern="[a-z0-9]+(?:-[a-z0-9]+)*"
                minLength={2}
                maxLength={63}
                required
                value={slug}
                onChange={(e) => setSlug(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="tenant-timezone" className="mb-2 block text-sm">
                Time zone
              </label>
              <input
                id="tenant-timezone"
                className={field}
                required
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
              />
            </div>
            <button className={action} disabled={busy}>
              {busy ? "Saving…" : "Create tenant"}
            </button>
          </form>
        </Panel>
        <div className="space-y-5">
          <Panel title="Tenant workspaces">
            {clients === null ? (
              <p className="text-sm text-ink-500">Loading tenants…</p>
            ) : clients.length === 0 ? (
              <p className="text-sm text-ink-500">No tenants yet.</p>
            ) : (
              <>
                <label htmlFor="client" className="mb-2 block text-sm">
                  Selected tenant
                </label>
                <select
                  id="client"
                  className={field}
                  value={selected}
                  disabled={busy}
                  onChange={(e) => void select(e.target.value)}
                >
                  <option value="">Choose a tenant</option>
                  {clients.map((client) => (
                    <option key={client.id} value={client.id}>
                      {client.name} · {client.slug}
                    </option>
                  ))}
                </select>
              </>
            )}
          </Panel>
          {selected && (
            <Panel title="Capability subscriptions">
              <p className="mb-5 text-sm text-ink-600">
                Subscriptions grant tenant access. Tenant enablement is a
                separate control. Provider setup and later feature rollout still
                apply.
              </p>
              {capabilities === null ? (
                <p className="text-sm text-ink-500">Loading capabilities…</p>
              ) : capabilities.length === 0 ? (
                <p className="text-sm text-ink-500">
                  No capabilities provisioned.
                </p>
              ) : (
                <ul className="divide-y divide-ink-100">
                  {capabilities.map((agent) => {
                    const capability = agent.capability;
                    if (!capability) return null;
                    return (
                      <li
                        key={capability.name}
                        className="flex flex-wrap items-center justify-between gap-3 py-4"
                      >
                        <span className="min-w-0">
                          <strong className="block break-words text-sm font-medium">
                            {capability.name}
                          </strong>
                          <span className="text-xs text-ink-500">
                            Tenant switch:{" "}
                            {capability.client_enabled ? "on" : "off"}
                          </span>
                        </span>
                        <button
                          type="button"
                          className="min-h-11 rounded-md border border-ink-300 px-3 text-sm hover:bg-brand-50 disabled:opacity-50"
                          disabled={busy || !agent.can_subscribe}
                          aria-pressed={capability.is_enabled}
                          onClick={() => void toggle(capability)}
                        >
                          {agent.required_system && capability.is_enabled
                            ? "Required safety capability"
                            : capability.is_enabled
                              ? "Subscribed · remove"
                              : "Not subscribed · grant"}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
            </Panel>
          )}
          {selected && (
            <Panel title="Billing plan">
              <p className="mb-5 text-sm text-ink-600">
                The stage and bundle this tenant is billed on. Its usage and bill
                are on the <Link href="/billing" className="underline underline-offset-2">Billing</Link> page.
                {plan && <span className="mt-1 block" data-testid="tenant-plan-now">Now: {planStatus(plan)}</span>}
              </p>
              <PlanEditor
                // A new key when the plan becomes known (or unknown), so the form starts from it.
                key={`${selected}:${plan ? "known" : "unknown"}`}
                clientId={selected}
                name={clients?.find((client) => client.id === selected)?.name ?? "This tenant"}
                current={plan}
                onSaved={rereadPlans}
              />
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}
