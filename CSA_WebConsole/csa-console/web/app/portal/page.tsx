import { requireIdentity, requireOwnProfile } from "@/lib/server-identity";
import { Heading, Notice, Panel } from "@/components/console/ui";
import { roleLabels } from "@/lib/identity-contracts";

export default async function PortalOverview() {
  const me = await requireIdentity();
  const profile = me.active_client_id ? await requireOwnProfile() : null;
  return (
    <>
      <Heading
        title="Your workspace"
        description="Your account and active tenant membership."
      />
      <Panel title="Account access">
        <dl className="grid gap-4 text-sm sm:grid-cols-2">
          <div>
            <dt className="text-ink-500">Email</dt>
            <dd className="mt-1 break-words font-medium">{me.user.email}</dd>
          </div>
          <div>
            <dt className="text-ink-500">Role</dt>
            <dd className="mt-1 font-medium">
              {me.role ? roleLabels[me.role] : "No active membership"}
            </dd>
          </div>
          <div className="sm:col-span-2">
            <dt className="text-ink-500">Tenant</dt>
            <dd className="mt-1 break-words font-medium">
              {profile?.name ?? "No active tenant"}
            </dd>
          </div>
        </dl>
      </Panel>
      {!me.active_client_id && (
        <div className="mt-5">
          <Notice>Ask a tenant administrator to review your membership.</Notice>
        </div>
      )}
    </>
  );
}
