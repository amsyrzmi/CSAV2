import { requireIdentity } from "@/lib/server-identity";
import { linkedConversation } from "@/lib/inbox-link";
import { InboxWorkspace } from "@/components/console/InboxWorkspace";

/** `?conversation=` opens that conversation (the notification bell links here). */
export default async function InboxPage({ searchParams }: PageProps<"/portal/inbox">) {
  const query = await searchParams;
  return <InboxWorkspace me={await requireIdentity()}
    initialConversation={linkedConversation(query.conversation)} />;
}
