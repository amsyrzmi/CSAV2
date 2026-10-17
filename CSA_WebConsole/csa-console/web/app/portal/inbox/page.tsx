import { requireIdentity } from "@/lib/server-identity";
import { InboxWorkspace } from "@/components/console/InboxWorkspace";

export default async function InboxPage() {
  return <InboxWorkspace me={await requireIdentity()} />;
}
