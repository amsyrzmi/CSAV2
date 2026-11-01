"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { hrClient, type HrContext } from "@/lib/hr-api";
import { hrHref, visibleTabs, type HrRoute } from "@/lib/hr-paths";
import type { Me } from "@/lib/identity-contracts";
import { usePublishContext } from "@/lib/page-context";
import { field, secondary, Heading, Notice } from "../ui";
import { HomeTab } from "./HomeTab";
import { HrTabBar } from "./HrTabBar";
import { LeaveTab } from "./LeaveTab";
import { PeopleTab } from "./PeopleTab";
import { PayrollTab } from "./PayrollTab";
import { NewPerson } from "./PersonForm";
import { PersonProfile } from "./PersonProfile";
import { RecruitmentTab } from "./RecruitmentTab";
import { ALL_DOMAINS, RequestsPanel } from "./RequestsPanel";
import { SettingsTab } from "./SettingsTab";
import { TrainingTab } from "./TrainingTab";

/** What every tab receives from the shell. `refreshContext` reloads the shell's HR context after a write. */
export interface HrTabProps {
  tenant: string;
  context: HrContext;
  readonly: boolean;
  platform: boolean;
  userId: string;
  refreshContext: () => Promise<void>;
}

/**
 * People & HR: the tenant (staff use their active tenant; platform admins choose one, kept in
 * `client_id`), the tab bar for the tabs the viewer may use, then the route's tab.
 */
export function HrShell({ me, route, initialTenant }: { me: Me; route: HrRoute; initialTenant: string }) {
  const router = useRouter();
  const platform = me.role === "platform_admin";
  const tenant = platform ? initialTenant : me.active_client_id ?? "";
  const [selection, setSelection] = useState(initialTenant || me.active_client_id || "");
  // On a profile the assistant gets the employee record, so "her" means this person. Published here,
  // not in the profile, so it changes with the route and is never overwritten by this shell.
  usePublishContext({
    tab: "hr", section: route.tab,
    record: route.tab === "people" && route.view === "profile" ? { kind: "employee", id: route.employeeId } : null,
  });
  return <div className="space-y-6">
    <Heading title="People & HR" description="Training, recruitment, attendance, leave and payroll preparation." />
    {platform && <form className="flex flex-wrap items-end gap-3" onSubmit={event => {
      event.preventDefault(); router.push(hrHref(route, true, selection));
    }}><label className="text-sm">Tenant membership<select className={`${field} mt-1`} value={selection}
      onChange={e => setSelection(e.target.value)} required><option value="">Choose a tenant</option>
      {me.memberships.map(m => <option key={m.client_id} value={m.client_id}>{m.client_id}</option>)}
    </select></label><button className={secondary}>Open HR</button></form>}
    {tenant ? <TenantHr key={tenant} me={me} tenant={tenant} route={route} platform={platform} />
      : <Notice>Select a tenant to continue.</Notice>}
  </div>;
}

/** Keyed by tenant: a new tenant gets fresh state and transport, so late answers cannot cross tenants. */
function TenantHr({ me, tenant, route, platform }: { me: Me; tenant: string; route: HrRoute; platform: boolean }) {
  const client = useMemo(() => hrClient(tenant), [tenant]);
  const [context, setContext] = useState<HrContext | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    client.context()
      .then(ctx => { if (active) setContext(ctx); })
      .catch(cause => { if (active) setError(cause instanceof Error ? cause.message : "HR request failed"); });
    return () => { active = false; };
  }, [client]);
  const refreshContext = useCallback(async () => { setContext(await client.context()); }, [client]);
  if (error) return <Notice error>{error}. Reload the page to try again.</Notice>;
  if (!context) return <Notice>Loading your HR access…</Notice>;
  const tabs = visibleTabs(context);
  const props: HrTabProps = { tenant, context, readonly: me.role === "read_only", platform, userId: me.user.id, refreshContext };
  return <div className="space-y-6">
    <HrTabBar tabs={tabs} current={route.tab} platform={platform} tenant={tenant} />
    {tabs.includes(route.tab) ? <TabContent route={route} props={props} />
      : <Notice>You don&apos;t have access to this part of People & HR. <Link className="underline" href={hrHref({ tab: "home" }, platform, tenant)}>Go to HR Home</Link></Notice>}
  </div>;
}

function TabContent({ route, props }: { route: HrRoute; props: HrTabProps }) {
  switch (route.tab) {
    case "home": return <HomeTab {...props} />;
    case "people": {
      const { tenant, context, readonly, platform } = props;
      if (route.view === "profile")
        return <PersonProfile key={route.employeeId} tenant={tenant} context={context} readonly={readonly} platform={platform}
          employeeId={route.employeeId} />;
      if (route.view === "new") return <NewPerson tenant={tenant} context={context} readonly={readonly} platform={platform} />;
      return <PeopleTab tenant={tenant} context={context} readonly={readonly} platform={platform} view={route.view} />;
    }
    case "recruitment": return <RecruitmentTab {...props} />;
    case "leave": return <LeaveTab {...props} />;
    case "payroll": return <PayrollTab {...props} />;
    case "training": return <TrainingTab {...props} />;
    case "requests": return <RequestsPanel {...props} domains={ALL_DOMAINS} />;
    case "settings": return <SettingsTab {...props} />;
  }
}
