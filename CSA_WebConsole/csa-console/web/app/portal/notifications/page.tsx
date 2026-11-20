import { NotificationsWorkspace } from "@/components/console/notifications/NotificationsWorkspace";
import { requireIdentity } from "@/lib/server-identity";

export default async function NotificationsPage() {
  return <NotificationsWorkspace me={await requireIdentity()} />;
}
