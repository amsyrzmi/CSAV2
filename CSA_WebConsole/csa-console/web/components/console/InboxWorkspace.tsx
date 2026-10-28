"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Client, Me } from "@/lib/identity-contracts";
import { Inbox } from "./inbox/Inbox";
import { field, Heading, Notice } from "./ui";

export function InboxWorkspace({ me }: { me: Me }) {
  const [selected, setSelected] = useState("");
  const [clients, setClients] = useState<Client[]>([]);
  const [error, setError] = useState("");
  const platform = me.role === "platform_admin";
  const permitted = platform || me.role === "tenant_admin" || me.role === "tenant_staff";
  useEffect(() => {
    if (!platform) return;
    const controller = new AbortController();
    api<Client[]>("/console/v1/admin/clients", { signal: controller.signal }).then(setClients)
      .catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [platform]);
  if (!permitted) return <Notice error>Staff inbox access requires an administrator or staff role.</Notice>;
  const tenant = platform ? selected : me.active_client_id;
  return <>
    <Heading title="Customer inbox" description="Conversations your customers start, with replies waiting for your review." />
    <Notice>Take over a conversation before you reply. The assistant stays paused until you release it.
      A reply you queue is sent when delivery succeeds. We hide common personal details in messages.</Notice>
    {error && <Notice error>{error}</Notice>}
    {platform && <label className="my-5 block">Tenant
      <select className={field} value={selected} onChange={(event) => setSelected(event.target.value)}>
        <option value="">Select a tenant</option>
        {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
      </select>
    </label>}
    {tenant ? <Inbox key={tenant} me={me} tenantId={platform ? tenant : null}
      query={platform ? `client_id=${encodeURIComponent(tenant)}` : ""} />
      : <Notice>Select a tenant to open its inbox.</Notice>}
  </>;
}
