import { requireIdentity } from "@/lib/server-identity";
import { linkedConversation } from "@/lib/inbox-link";
import { InboxWorkspace } from "@/components/console/InboxWorkspace";

/**
 * `?client_id=` picks the company and `?conversation=` opens one of its conversations. A
 * conversation id means nothing without its company, so it is passed only with a valid one.
 */
export default async function InboxPage({ searchParams }: PageProps<"/inbox">) {
  const query = await searchParams;
  // A company id is the same canonical UUID shape a conversation id is.
  const clientId = linkedConversation(query.client_id);
  return <InboxWorkspace me={await requireIdentity()} initialClientId={clientId}
    initialConversation={clientId ? linkedConversation(query.conversation) : ""} />;
}
