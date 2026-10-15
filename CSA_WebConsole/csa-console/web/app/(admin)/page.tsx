import Link from "next/link";
import { Heading, Panel, secondary } from "@/components/console/ui";

export default function Overview() {
  return (
    <>
      <Heading
        title="Workspace administration"
        description="Onboard tenants and manage access to the CSA Console."
      />
      <div className="grid gap-5 md:grid-cols-2">
        <Panel title="Tenants">
          <p className="mb-5 text-sm text-ink-600">
            Create a tenant and review its capability subscriptions.
          </p>
          <Link className={secondary} href="/settings/clients">
            Manage tenants
          </Link>
        </Panel>
        <Panel title="Team access">
          <p className="mb-5 text-sm text-ink-600">
            Invite tenant members with explicit roles and review active access.
          </p>
          <Link className={secondary} href="/settings/team">
            Manage members
          </Link>
        </Panel>
      </div>
    </>
  );
}
