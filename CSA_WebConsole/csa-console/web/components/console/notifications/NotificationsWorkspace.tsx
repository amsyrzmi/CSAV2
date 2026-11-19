"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import type { Client, Me } from "@/lib/identity-contracts";
import { getInboxSettings, getTargets } from "@/lib/notifications-settings-api";
import { useLoaded } from "../bookings/parts";
import { PageIntro } from "../kit";
import { field, Heading, Notice } from "../ui";
import InboxTimingPanel from "./InboxTimingPanel";
import MyNotificationsPanel from "./MyNotificationsPanel";
import TargetsPanel from "./TargetsPanel";

/** Who is looking and which company it is, as the three sections need to know. */
export interface NotificationsScope {
  /** Null for a tenant member (the session names the company); the chosen company for a platform admin. */
  tenantId: string | null;
  /** An Admin of the company, or a platform admin: the only people who change targets and timing. */
  canEdit: boolean;
  /** `client_id=...` for a platform admin's links, else empty. */
  query: string;
}

/**
 * Where alerts go, how long the Inbox waits before it nudges, and each person's own email. Every
 * member reads the company sections and may change their own email choices; only an Admin changes
 * the company's. A platform admin has no personal section, and names the company in the address
 * (`?client_id=`), as the Bookings tab does.
 */
export function NotificationsWorkspace({ me }: { me: Me }) {
  const search = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const platform = me.role === "platform_admin";
  const [clients, setClients] = useState<Client[] | null>(null);
  const [error, setError] = useState("");
  const selected = search.get("client_id") ?? "";
  const tenant = platform ? selected : me.active_client_id;

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
  const scope: NotificationsScope = {
    tenantId: platform ? tenant : null,
    canEdit: platform || me.role === "tenant_admin",
    query: platform && tenant ? `client_id=${encodeURIComponent(tenant)}` : "",
  };

  return (
    <>
      <Heading title="Notifications" description="Where alerts about your customers go, and when the Inbox reminds you." />
      {error && <div className="mb-5"><Notice error>{error}</Notice></div>}
      {platform && (
        <label className="mb-5 block text-sm">Tenant
          <select className={`${field} mt-1`} data-testid="notifications-tenant" value={selected}
            onChange={(event) => chooseTenant(event.target.value)}>
            <option value="">Select a tenant</option>
            {(clients ?? []).map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
          </select>
        </label>
      )}
      {!tenant ? (
        <Notice>Select a tenant to see its notification settings.</Notice>
      ) : (
        <Sections key={tenant} scope={scope} personal={!platform} />
      )}
    </>
  );
}

/** The sections, each reading its own data so one that fails never blanks the others. */
function Sections({ scope, personal }: { scope: NotificationsScope; personal: boolean }) {
  const targets = useLoaded(() => getTargets(scope.tenantId));
  const timing = useLoaded(() => getInboxSettings(scope.tenantId));
  // Unknown while the targets are unread: the server answers 503 if email really is off.
  const emailAvailable = targets.data ? targets.data.email_available : null;
  return (
    <div className="space-y-6">
      <PageIntro>Each section below saves on its own.</PageIntro>
      {personal && <MyNotificationsPanel emailAvailable={emailAvailable} />}
      <TargetsPanel scope={scope} targets={targets} />
      <InboxTimingPanel scope={scope} timing={timing} />
    </div>
  );
}
