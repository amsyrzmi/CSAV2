import { requireIdentity } from "@/lib/server-identity";
import { EmailWorkspace } from "@/components/console/EmailWorkspace";

export default async function PortalEmailPage({ searchParams }: PageProps<"/portal/email">) {
  const query = await searchParams;
  return <EmailWorkspace me={await requireIdentity()}
    notice={typeof query.connected === "string" ? "connected" : typeof query.error === "string" ? query.error : ""} />;
}
