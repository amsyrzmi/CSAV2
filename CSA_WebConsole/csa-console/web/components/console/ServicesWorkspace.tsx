"use client";

import { Bot, CirclePause, Hourglass, Layers, Receipt, type LucideIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { avatarForPackage } from "@/lib/agent-avatars";
import { api, ApiError, patch } from "@/lib/api";
import type { Agent, Client, Me } from "@/lib/identity-contracts";
import { usePublishContext } from "@/lib/page-context";
import { servicesClient, type Service } from "@/lib/services-api";
import {
  agentCountLabel, lastChangeText, rowAvatarState, SAVED_NOT_REFRESHED, serviceStats, statusLabel, statusLine,
  type ServiceStats, type StatusTone,
} from "@/lib/services-view";
import AgentAvatar from "./fs/AgentAvatar";
import { useAvatarTransition } from "./fs/useAvatarTransition";
import { AgentCard } from "./RuntimeWorkspace";
import { action, field, Heading, Notice, secondary } from "./ui";
import { WhatsAppPanel } from "./WhatsAppPanel";

const GROUPS: { kind: Service["kind"]; title: string }[] = [
  { kind: "base", title: "Base" },
  { kind: "addon", title: "Add-ons" },
  { kind: "standalone", title: "Standalone" },
];
/** Status words take their colour from the theme tokens; the word itself always says the status. */
const TONE_CLASS: Record<StatusTone, string> = {
  ok: "text-[color:var(--fs-success)]",
  wait: "text-[color:var(--fs-warning)]",
  off: "text-[color:var(--fs-danger)]",
  neutral: "text-ink-600",
};
const CHIP = { background: "var(--fs-sprite-tile)", color: "var(--fs-sprite-ink)" };
const CARD = "rounded-[14px] border border-[var(--fs-border)] bg-[var(--fs-surface)] shadow-[var(--fs-shadow-card)]";

/** Plain words about what each count covers; the footer is not a link. */
const STATS: {
  id: string; label: string; footer: string; icon: LucideIcon; figure: (stats: ServiceStats) => number;
}[] = [
  { id: "active", label: "Active services", footer: "Services switched on", icon: Layers,
    figure: (stats) => stats.active },
  { id: "agents", label: "Agents on", footer: "Counted across all services", icon: Bot,
    figure: (stats) => stats.agentsOn },
  { id: "waiting", label: "Waiting for activation", footer: "Requests to activate", icon: Hourglass,
    figure: (stats) => stats.waiting },
  { id: "suspended", label: "Suspended", footer: "Services switched off", icon: CirclePause,
    figure: (stats) => stats.suspended },
];

function message(cause: unknown) {
  return cause instanceof ApiError || cause instanceof Error ? cause.message : "Request failed.";
}

export function ServicesWorkspace({ me, initialClientId = "" }: { me: Me; initialClientId?: string }) {
  usePublishContext({ tab: "services" });
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
        <ServiceList key={clientId} clientId={platform ? clientId : null} platform={platform}
          canManage={platform || me.role === "tenant_admin"} />}
    </>
  );
}

function ServiceList({ clientId, platform, canManage }: {
  clientId: string | null; platform: boolean; canManage: boolean;
}) {
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
  // The rows read the fresh list to learn whether the server really made the change.
  const reload = async () => {
    const rows = await servicesClient(clientId).list();
    setServices(rows);
    return rows;
  };
  const stats = serviceStats(services);
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {STATS.map(({ id, label, footer, icon: Icon, figure }) => (
          <div key={id} role="group" aria-labelledby={`services-stat-label-${id}`}
            data-testid={`services-stat-${id}`} className={`flex min-w-0 flex-col overflow-hidden ${CARD}`}>
            <div className="flex flex-1 flex-col gap-2 p-4">
              <div className="flex items-start justify-between gap-2">
                <p id={`services-stat-label-${id}`} className="min-w-0 text-sm text-ink-700">{label}</p>
                {/* Below 360 px two cards share 300 px: the chip would clip "Suspended", so it steps aside. */}
                <span aria-hidden="true" className="hidden size-8 flex-none items-center justify-center rounded-lg min-[360px]:inline-flex"
                  style={{ background: "var(--fs-surface-blue)", color: "var(--fs-primary)" }}>
                  <Icon className="size-4" />
                </span>
              </div>
              <p className="font-(family-name:--fs-font-heading) text-3xl font-semibold tabular-nums">{figure(stats)}</p>
            </div>
            <p className="border-t border-[var(--fs-border)] bg-[var(--fs-surface-blue)] px-4 py-2 text-xs text-ink-600">
              {footer}</p>
          </div>
        ))}
      </div>
      {GROUPS.map(({ kind, title }) => {
        const rows = services.filter((service) => service.kind === kind);
        if (!rows.length) return null;
        return (
          <section key={kind} data-testid={`services-group-${kind}`} className={CARD}>
            <h2 className="px-4 pt-4 pb-3 font-(family-name:--fs-font-heading) text-base font-semibold sm:px-5">{title}</h2>
            <ul>
              {rows.map((service) => <ServiceRow key={service.key} service={service}
                clientId={clientId} platform={platform} onChanged={reload} />)}
            </ul>
          </section>
        );
      })}
      {services.some((service) => service.key === "customer_support" && service.status === "active") && (
        <WhatsAppPanel tenantId={clientId} canManage={canManage} platform={platform} />
      )}
    </div>
  );
}

type Outcome = "requested" | "active" | "suspended";

/**
 * One service row. The avatar follows the server, never the click: a row action starts a
 * pending look (still, dimmed, "Working..."), and only when the call has resolved AND the
 * reloaded list shows the new status does an activation wake the avatar or a suspension settle
 * it. A failure restores the old look and shows the server's reason. A call that succeeded but
 * whose reload failed is not a failure: the row says it was saved, the avatar stays still (no
 * confirmed status to wake or settle for), and the reader reloads. A status that changes
 * without an action here (a reload after another row's action) just shows its resting look.
 */
function ServiceRow({ service, clientId, platform, onChanged }: {
  service: Service; clientId: string | null; platform: boolean; onChanged: () => Promise<Service[]>;
}) {
  const [busy, setBusy] = useState(false);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState("");
  const [stale, setStale] = useState(false);
  const [form, setForm] = useState<"" | "activate" | "suspend">("");
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const [open, setOpen] = useState(false);
  // The agent switch being saved and the server's reason for each failed one, shown on its card.
  const [switching, setSwitching] = useState<{ name: string; switchName: "is_enabled" | "client_enabled" } | null>(null);
  const [agentErrors, setAgentErrors] = useState<Record<string, string>>({});
  const [agentStale, setAgentStale] = useState("");
  // The ref closes the gap before a re-render disables the buttons: one request per click burst.
  const working = useRef(false);
  const transition = useAvatarTransition(rowAvatarState(service.status));
  const { start, confirm: confirmTo, fail } = transition;
  const client = servicesClient(clientId);
  const query = clientId ? `client_id=${encodeURIComponent(clientId)}` : "";
  const status = statusLabel(service);
  const avatar = avatarForPackage(service.key);
  const lastChange = lastChangeText(service.last_change);
  const line = statusLine(service);
  const resting = transition.phase === "idle" || transition.phase === "still";
  const avatarState = acting ? "pending" : resting ? rowAvatarState(service.status) : transition.state;

  async function run(call: () => Promise<Service>, outcome: Outcome) {
    if (working.current) return;
    working.current = true;
    setBusy(true);
    setActing(true);
    setError("");
    setStale(false);
    setAgentStale("");
    start();
    try {
      try {
        await call();
      } catch (cause) {
        fail();
        setError(message(cause));
        return;
      }
      // Saved: the form is done with whatever the reload does.
      setForm("");
      setReference("");
      setNote("");
      let rows: Service[];
      try {
        rows = await onChanged();
      } catch {
        fail(); // saved, but no reloaded status confirms it: no wake or settle
        setStale(true);
        return;
      }
      const now = rows.find((row) => row.key === service.key)?.status;
      if (outcome === "active" && now === "active") confirmTo("active");
      else if (outcome === "suspended" && now === "suspended") confirmTo("inactive");
      else fail(); // a request only moves the row to "requested": nothing to wake or settle
    } finally {
      working.current = false;
      setBusy(false);
      setActing(false);
    }
  }
  async function toggle(agent: Agent, switchName: "is_enabled" | "client_enabled") {
    const capability = agent.capability;
    if (!capability || working.current) return;
    working.current = true;
    setBusy(true);
    setError("");
    setAgentErrors({});
    setStale(false);
    setAgentStale("");
    setSwitching({ name: agent.name, switchName });
    try {
      try {
        await patch(`/console/v1/capabilities/${encodeURIComponent(agent.name)}?${query}`,
          { [switchName]: !capability[switchName] });
      } catch (cause) {
        setAgentErrors({ [agent.name]: message(cause) });
        return;
      }
      // Saved even if the reload fails; the card then says so instead of reporting a failure.
      await onChanged().catch(() => setAgentStale(agent.name));
    } finally {
      working.current = false;
      setBusy(false);
      setSwitching(null);
    }
  }
  return (
    <li data-testid={`services-card-${service.key}`}
      className="border-t border-dashed border-[var(--fs-border)] px-4 py-4 sm:px-5">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
        <div className="flex min-w-0 flex-1 basis-60 items-start gap-3">
          <span style={CHIP} className="inline-flex size-14 flex-none items-center justify-center rounded-xl">
            {avatar ? <AgentAvatar agentId={avatar} size={48} label="" state={avatarState} /> :
              <Receipt aria-hidden="true" className="size-6" />}
          </span>
          <div className="min-w-0">
            <h3 className="font-(family-name:--fs-font-heading) text-base font-semibold">{service.title}</h3>
            <p className="mt-0.5 text-sm text-ink-600 [overflow-wrap:anywhere]">{service.summary}</p>
            {/* Only what the last-change line below does not already say (see statusLine). */}
            {line && <p className="mt-1 text-sm [overflow-wrap:anywhere]"
              data-testid={`services-${service.status === "active" ? "activation" : "requested"}-${service.key}`}>{line}</p>}
            {lastChange && <p className="mt-1 text-sm text-ink-600 [overflow-wrap:anywhere]"
              data-testid={`services-last-change-${service.key}`}>{lastChange}</p>}
            {service.key === "staff_email" && service.inner_switch !== null && <p className="mt-1 text-sm text-ink-600">
              Enabled by your tenant admin in the Email tab: currently {service.inner_switch ? "on" : "off"}.</p>}
            {service.key === "hr" && <p className="mt-1 text-sm text-ink-600">
              HR modules are enabled by your HR owner inside the HR tab.</p>}
          </div>
        </div>
        <div className="flex items-start gap-x-5 text-sm">
          <span className="whitespace-nowrap text-ink-600 sm:w-20">{agentCountLabel(service.agents.length)}</span>
          <span className="flex flex-col sm:w-24">
            <span data-testid={`services-status-${service.key}`}
              className={`font-medium ${TONE_CLASS[status.tone]}`}>{status.text}</span>
            {/* Always rendered (empty when idle) so assistive technology announces the change reliably. */}
            <span role="status" data-testid={`services-pending-${service.key}`}
              className={acting ? "text-ink-600" : ""}>{acting ? "Working..." : ""}</span>
          </span>
        </div>
        <div className="flex flex-wrap gap-2">
          {service.can_request && <button className={secondary} disabled={busy}
            data-testid={`services-request-${service.key}`}
            onClick={() => run(() => client.request(service.key), "requested")}>Request</button>}
          {service.can_activate && form !== "activate" && <button className={action} disabled={busy}
            data-testid={`services-activate-${service.key}`}
            onClick={() => setForm("activate")}>Activate</button>}
          {service.can_suspend && form !== "suspend" && <button className={secondary} disabled={busy}
            data-testid={`services-suspend-${service.key}`}
            onClick={() => setForm("suspend")}>Suspend</button>}
          {service.agents.length > 0 && <button className={secondary} aria-expanded={open} disabled={busy}
            data-testid={`services-agents-toggle-${service.key}`}
            onClick={() => setOpen((value) => !value)}>
            {open ? "Hide agents" : `Show agents (${service.agents.length})`}</button>}
        </div>
      </div>
      {platform && !service.consistent && <div className="mt-3"><Notice error>
        Inconsistent state: {service.inconsistencies.join(", ")}.</Notice></div>}
      {error && <div className="mt-3"><Notice error>{error}</Notice></div>}
      {stale && <div className="mt-3"><Notice warning>{SAVED_NOT_REFRESHED}</Notice></div>}
      {form === "activate" && service.can_activate && (
        <form className="mt-4 max-w-xl space-y-3" onSubmit={(event) => {
          event.preventDefault();
          void run(() => client.activate(service.key, reference.trim(), note.trim() || null), "active");
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
              onClick={() => setForm("")}>Cancel</button>
          </div>
        </form>
      )}
      {form === "suspend" && service.can_suspend && (
        <form className="mt-4 max-w-xl space-y-3" onSubmit={(event) => {
          event.preventDefault();
          void run(() => client.suspend(service.key, note.trim() || null), "suspended");
        }}>
          <label className="block text-sm">Note (optional)
            <input className={field} data-testid="services-suspend-note" maxLength={500}
              value={note} onChange={(event) => setNote(event.target.value)} />
          </label>
          <div className="flex gap-2">
            <button className={action} disabled={busy}
              data-testid="services-suspend-confirm">Confirm suspension</button>
            <button type="button" className={secondary} disabled={busy}
              onClick={() => setForm("")}>Cancel</button>
          </div>
        </form>
      )}
      {open && <div className="mt-4 grid grid-cols-[minmax(0,1fr)] gap-4 xl:grid-cols-[repeat(2,minmax(0,1fr))]"
        data-testid={`services-agents-${service.key}`}>
        {service.agents.map((agent) => <AgentCard key={agent.name} agent={agent} busy={busy} onToggle={toggle}
          pending={switching?.name === agent.name ? switching.switchName : null}
          failed={agentErrors[agent.name] ?? null} saved={agentStale === agent.name} />)}
      </div>}
    </li>
  );
}
