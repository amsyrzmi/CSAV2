import { requireIdentity } from "@/lib/server-identity";
import { EmailWorkspace } from "@/components/console/EmailWorkspace";

export default async function PortalEmailPage() {
  return <EmailWorkspace me={await requireIdentity()} />;
}
