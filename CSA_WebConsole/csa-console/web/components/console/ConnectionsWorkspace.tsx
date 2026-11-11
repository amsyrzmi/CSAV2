"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import { ARRIVAL_KEYS, arrivals, type Arrival } from "@/lib/connections-view";
import type { Client, Me } from "@/lib/identity-contracts";
import { field, Heading, Notice } from "./ui";

/** The Connections tab: the company's connections (TikTok Shop) and the member's own Google ones. */
export function ConnectionsWorkspace({ me }: { me: Me }) {
  const search = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const platform = me.role === "platform_admin";
  const [clients, setClients] = useState<Client[]>([]);
  const [error, setError] = useState("");
  // The chosen company lives in the URL (?client_id=) and nowhere else, as on Business management.
  const selected = search.get("client_id") ?? "";
  const tenant = platform ? selected : me.active_client_id;

  // What Google or TikTok sent the browser back with: read once on arrival, shown above the
  // groups, then taken out of the address (`client_id` stays) so a reload does not say it again.
  const [returned] = useState<Arrival[]>(() => arrivals(search));
  useEffect(() => {
    if (!ARRIVAL_KEYS.some((key) => search.has(key))) return;
    const rest = new URLSearchParams(search.toString());
    for (const key of ARRIVAL_KEYS) rest.delete(key);
    const text = rest.toString();
    router.replace(text ? `${pathname}?${text}` : pathname, { scroll: false });
  }, [search, pathname, router]);

  useEffect(() => {
    if (!platform) return;
    const controller = new AbortController();
    api<Client[]>("/console/v1/admin/clients", { signal: controller.signal })
      .then(setClients)
      .catch((cause: Error) => { if (!controller.signal.aborted) setError(cause.message); });
    return () => controller.abort();
  }, [platform]);

  const chooseTenant = (value: string) => {
    router.replace(value ? `${pathname}?client_id=${encodeURIComponent(value)}` : pathname, { scroll: false });
  };
  const groupHeading = "font-(family-name:--fs-font-heading) text-(length:--fs-text-xl) font-semibold";
  return <>
    <Heading title="Connections"
      description="Connect fourstacks to your TikTok Shop and your own Google account, and see where each connection stands." />
    {error && <Notice error>{error}</Notice>}
    {platform && <label className="my-5 block">Tenant
      <select data-testid="connections-tenant" className={field} value={selected}
        onChange={(event) => chooseTenant(event.target.value)}>
        <option value="">Select a tenant</option>
        {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
      </select>
    </label>}
    {platform && !tenant && <Notice>Select a tenant to see its connections.</Notice>}
    {returned.length > 0 && <div className="mb-5 space-y-3">
      {returned.map((arrival) => <Notice key={arrival.kind} error={arrival.tone === "bad"}
        testId={`connections-result-${arrival.kind}`}>{arrival.text}</Notice>)}
    </div>}
    <section data-testid="connections-group-business" className="mb-8">
      <h2 className={groupHeading}>Your business</h2>
      <p className="mt-1 mb-4 text-sm text-[color:var(--fs-text-secondary)]">
        For the whole company. Only an Admin can connect or disconnect.</p>
    </section>
    {!platform && <section data-testid="connections-group-you" className="mb-8">
      <h2 className={groupHeading}>You</h2>
      <p className="mt-1 mb-4 text-sm text-[color:var(--fs-text-secondary)]">
        Your own Google account. Nobody else can connect it for you.</p>
    </section>}
  </>;
}
