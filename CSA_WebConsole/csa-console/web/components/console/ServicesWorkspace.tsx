"use client";

import { useEffect, useState } from "react";
import { api, ApiError, patch } from "@/lib/api";
import type { Agent, Client, Me } from "@/lib/identity-contracts";
import { servicesClient, type PackageStatus, type Service } from "@/lib/services-api";
import { AgentCard } from "./RuntimeWorkspace";
import { action, field, Heading, Notice, secondary } from "./ui";

const GROUPS: { kind: Service["kind"]; title: string }[] = [
  { kind: "base", title: "Base" },
  { kind: "addon", title: "Add-ons" },
  { kind: "standalone", title: "Standalone" },
];
const BADGE: Record<PackageStatus, { text: string; cls: string }> = {
  available: { text: "Available", cls: "border-ink-300 bg-ink-50 text-ink-700" },
  requested: { text: "Requested", cls: "border-amber-200 bg-amber-50 text-amber-800" },
  active: { text: "Active", cls: "border-green-200 bg-green-50 text-green-800" },
  suspended: { text: "Suspended", cls: "border-red-200 bg-red-50 text-red-800" },
};

function message(cause: unknown) {
  return cause instanceof ApiError || cause instanceof Error ? cause.message : "Request failed.";
}

export function ServicesWorkspace({ me, initialClientId = "" }: { me: Me; initialClientId?: string }) {
  const platform = me.role === "platform_admin";
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
      <Heading title="Services"
        description="Service packages for this tenant: request, activation and the agents each package includes." />
      {error && <Notice error>{error}</Notice>}
      {platform && (
        <div className="mb-5">
          <label htmlFor="services-tenant" className="mb-2 block text-sm">Tenant</label>
          <select id="services-tenant" data-testid="services-tenant" className={field} value={selected}
            onChange={(event) => setSelected(event.target.value)}>
            <option value="">{clients ? "Select a tenant" : "Loading tenants..."}</option>
            {clients?.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
          </select>
        </div>
      )}
      {!clientId ? <Notice>Select a tenant with an active membership to continue.</Notice> :
        <ServiceList key={clientId} clientId={platform ? clientId : null} platform={platform} />}
    </>
  );
}

function ServiceList({ clientId, platform }: { clientId: string | null; platform: boolean }) {
  const [services, setServices] = useState<Service[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    servicesClient(clientId).list()
      .then((rows) => { if (live) setServices(rows); })
      .catch((cause) => { if (live) setError(message(cause)); });
    return () => { live = false; };
  }, [clientId]);
  if (!services) return error ? <Notice error>{error}</Notice> : <Notice>Loading services...</Notice>;
  // Any mutation can change other cards' permissions (add-ons follow the base), so reload all.
  const reload = async () => setServices(await servicesClient(clientId).list());
  return (
    <div className="space-y-8">
      {GROUPS.map(({ kind, title }) => {
        const rows = services.filter((service) => service.kind === kind);
        if (!rows.length) return null;
        return (
          <section key={kind} data-testid={`services-group-${kind}`}>
            <h2 className="mb-3 text-lg font-semibold">{title}</h2>
            <div className="grid gap-5 xl:grid-cols-2">
              {rows.map((service) => <ServiceCard key={service.key} service={service}
                clientId={clientId} platform={platform} onChanged={reload} />)}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function ServiceCard({ service, clientId, platform, onChanged }: {
  service: Service; clientId: string | null; platform: boolean; onChanged: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirm, setConfirm] = useState<"" | "activate" | "suspend">("");
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [open, setOpen] = useState(false);
  const client = servicesClient(clientId);
  const query = clientId ? `client_id=${encodeURIComponent(clientId)}` : "";
  const badge = !service.sellable && service.status === "available"
    ? { text: "Coming soon", cls: "border-ink-300 bg-ink-50 text-ink-600" }
    : BADGE[service.status];

  async function run(call: () => Promise<Service>) {
    setBusy(true);
    setError("");
    try {
      await call();
      await onChanged();
      setConfirm("");
      setReference("");
      setNote("");
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }
  async function toggle(agent: Agent, switchName: "is_enabled" | "client_enabled") {
    const capability = agent.capability;
    if (!capability) return;
    setBusy(true);
    setError("");
    try {
      await patch(`/console/v1/capabilities/${encodeURIComponent(agent.name)}?${query}`,
        { [switchName]: !capability[switchName] });
      await onChanged();
    } catch (cause) {
      setError(message(cause));
    } finally {
      setBusy(false);
    }
  }
  return (
    <article className="card p-5 sm:p-6" data-testid={`services-card-${service.key}`}>
      <div className="mb-3 flex items-start justify-between gap-3">
        <h3 className="text-base font-semibold">{service.title}</h3>
        <span data-testid={`services-status-${service.key}`}
          className={`shrink-0 rounded-full border px-2.5 py-0.5 text-xs font-medium ${badge.cls}`}>{badge.text}</span>
      </div>
      <p className="mb-3 text-sm text-ink-600">{service.summary}</p>
      {service.status === "requested" && <p className="mb-3 text-sm">
        Requested{service.requested_at ? ` ${new Date(service.requested_at).toLocaleDateString()}` : ""}; awaiting activation.</p>}
      {service.status === "active" && <p className="mb-3 text-sm" data-testid={`services-activation-${service.key}`}>
        Activated{service.activated_at ? ` ${new Date(service.activated_at).toLocaleDateString()}` : ""} by a platform administrator
        {service.reference ? `. Reference: ${service.reference}` : ""}.</p>}
      {service.key === "staff_email" && service.inner_switch !== null && <p className="mb-3 text-sm text-ink-600">
        Enabled by your tenant admin in the Email tab: currently {service.inner_switch ? "on" : "off"}.</p>}
      {service.key === "hr" && <p className="mb-3 text-sm text-ink-600">
        HR modules are enabled by your HR owner inside the HR tab.</p>}
      {platform && !service.consistent && <Notice error>
        Inconsistent state: {service.inconsistencies.join(", ")}.</Notice>}
      {error && <div className="my-3"><Notice error>{error}</Notice></div>}
      <div className="mt-3 flex flex-wrap gap-2">
        {service.can_request && <button className={action} disabled={busy}
          data-testid={`services-request-${service.key}`}
          onClick={() => run(() => client.request(service.key))}>Request</button>}
        {service.can_activate && confirm !== "activate" && <button className={action} disabled={busy}
          data-testid={`services-activate-${service.key}`}
          onClick={() => setConfirm("activate")}>Activate</button>}
        {service.can_suspend && confirm !== "suspend" && <button className={secondary} disabled={busy}
          data-testid={`services-suspend-${service.key}`}
          onClick={() => setConfirm("suspend")}>Suspend</button>}
        {service.agents.length > 0 && <button className={secondary} aria-expanded={open}
          data-testid={`services-agents-toggle-${service.key}`}
          onClick={() => setOpen((value) => !value)}>
          {open ? "Hide agents" : `Show agents (${service.agents.length})`}</button>}
      </div>
      {confirm === "activate" && service.can_activate && (
        <form className="mt-4 space-y-3" onSubmit={(event) => {
          event.preventDefault();
          void run(() => client.activate(service.key, reference.trim(), note.trim() || null));
        }}>
          <label className="block text-sm">Reference (required)
            <input className={field} data-testid="services-activate-reference" required maxLength={200}
              value={reference} onChange={(event) => setReference(event.target.value)} />
          </label>
          <label className="block text-sm">Note (optional)
            <input className={field} data-testid="services-activate-note" maxLength={500}
              value={note} onChange={(event) => setNote(event.target.value)} />
          </label>
          <div className="flex gap-2">
            <button className={action} disabled={busy || !reference.trim()}
              data-testid="services-activate-confirm">Confirm activation</button>
            <button type="button" className={secondary} disabled={busy}
              onClick={() => setConfirm("")}>Cancel</button>
          </div>
        </form>
      )}
      {confirm === "suspend" && service.can_suspend && (
        <form className="mt-4 space-y-3" onSubmit={(event) => {
          event.preventDefault();
          void run(() => client.suspend(service.key, note.trim() || null));
        }}>
          <label className="block text-sm">Note (optional)
            <input className={field} data-testid="services-suspend-note" maxLength={500}
              value={note} onChange={(event) => setNote(event.target.value)} />
          </label>
          <div className="flex gap-2">
            <button className={action} disabled={busy}
              data-testid="services-suspend-confirm">Confirm suspension</button>
            <button type="button" className={secondary} disabled={busy}
              onClick={() => setConfirm("")}>Cancel</button>
          </div>
        </form>
      )}
      {open && <div className="mt-4 space-y-4" data-testid={`services-agents-${service.key}`}>
        {service.agents.map((agent) => <AgentCard key={agent.name} agent={agent} busy={busy} onToggle={toggle} />)}
      </div>}
    </article>
  );
}
