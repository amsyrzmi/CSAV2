"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { api } from "@/lib/api";
import type { Client, Me } from "@/lib/identity-contracts";
import { overviewBody } from "@/lib/overview-home";
import { CustomerRepliesBanner } from "../CustomerRepliesBanner";
import { SandboxWorkspace } from "../SandboxWorkspace";
import { AssistantTile } from "../chat/ChatEmptyState";
import { Notice } from "../ui";
import { StatusStrip } from "./StatusStrip";
import { useActivePackages } from "./useActivePackages";
import { WorkspaceHome } from "./WorkspaceHome";

/**
 * Overview is the assistant home: a slim header (the tenant selector for platform
 * administrators and the status strip) above the sandbox chat, which fills the rest of the
 * window. The page is marked `data-fill`, so the shell drops its centred column and the chat's
 * message list scrolls inside instead of the page.
 *
 * A company without Customer support has no customers to chat as, so it gets the small home
 * instead of the chat and the customer-replies banner. The company's packages are read once here
 * and shared with the status strip; while they load nothing but a short wait shows, and a failed
 * read shows the chat, as before.
 */
export function OverviewHome({ me }: { me: Me }) {
  const platform = me.role === "platform_admin";
  const search = useSearchParams();
  const chosen = search.get("client_id")?.trim() || "";
  const tenant = platform ? chosen : me.active_client_id;

  const packages = useActivePackages(tenant ? (platform ? tenant : "") : null);
  const showing = packages === undefined ? "loading" : overviewBody(packages);

  let body: React.ReactNode;
  if (platform && !tenant) {
    body = (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
        <AssistantTile />
        <p className="text-[color:var(--fs-text-secondary)]">Choose a tenant to start chatting</p>
      </div>
    );
  } else if (!tenant) {
    body = (
      <div className="mx-auto w-full max-w-3xl p-5 sm:p-8">
        <Notice>Your account has no active company. Ask your administrator to check your access.</Notice>
      </div>
    );
  } else if (showing === "loading") {
    body = <p role="status" className="p-6 text-center text-sm text-[color:var(--fs-text-secondary)]">Loading</p>;
  } else if (showing === "home") {
    body = <WorkspaceHome me={me} packages={packages ?? []} tenant={tenant} />;
  } else {
    body = <SandboxWorkspace me={me} chromeless tenant={tenant} />;
  }

  return (
    <div data-fill className="flex min-h-0 flex-1 flex-col">
      <h1 className="sr-only">Overview</h1>
      <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-[color:var(--fs-border)] px-4 py-2.5 sm:px-6">
        {platform && <TenantSelect value={chosen} />}
        {tenant && (
          <div className="sm:ml-auto">
            <StatusStrip key={tenant} me={me} clientId={platform ? tenant : null} packages={packages} />
          </div>
        )}
      </div>
      {/* Only the chat has the banner, and it is silent unless customers aren't being answered. */}
      {tenant && showing === "chat" && (
        <div className="shrink-0 px-4 pt-3 sm:px-6 empty:hidden">
          <CustomerRepliesBanner
            tenantId={platform ? tenant : null}
            servicesHref={platform ? `/services?client_id=${encodeURIComponent(tenant)}` : "/portal/services"}
          />
        </div>
      )}
      <div className="min-h-0 flex-1">{body}</div>
    </div>
  );
}

/** The platform administrator's tenant, kept in `?client_id=` so links and the dock follow it. */
function TenantSelect({ value }: { value: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams();
  const [clients, setClients] = useState<Client[] | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    api<Client[]>("/console/v1/admin/clients", { signal: controller.signal })
      .then(setClients)
      .catch((cause: Error) => {
        if (!controller.signal.aborted) setError(cause.message);
      });
    return () => controller.abort();
  }, []);

  function choose(next: string) {
    const params = new URLSearchParams(search.toString());
    if (next) params.set("client_id", next);
    else params.delete("client_id");
    const query = params.toString();
    router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
  }

  // A tenant named in the URL but not (yet) in the list still shows as chosen.
  const listed = clients?.some((client) => client.id === value) ?? false;
  return (
    <>
      <div className="flex min-w-0 items-center gap-2">
        <label htmlFor="overview-tenant" className="shrink-0 text-sm font-medium">
          Tenant
        </label>
        <select
          id="overview-tenant"
          data-testid="overview-tenant"
          className="h-10 min-w-0 max-w-[16rem] rounded-[var(--fs-radius-control)] border border-[color:var(--fs-border-control)] bg-[var(--fs-surface)] px-3 text-sm text-[color:var(--fs-text)] hover:border-[color:var(--fs-primary)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)] pointer-coarse:h-11"
          value={value}
          onChange={(event) => choose(event.target.value)}
        >
          <option value="">Select a tenant</option>
          {value && !listed && <option value={value}>{clients ? value : "Loading tenants"}</option>}
          {clients?.map((client) => (
            <option key={client.id} value={client.id}>
              {client.name}
            </option>
          ))}
        </select>
      </div>
      {error && (
        <div className="basis-full">
          <Notice error>Tenants could not be loaded: {error}</Notice>
        </div>
      )}
    </>
  );
}
