import { requireIdentity } from "@/lib/server-identity";
import { BookingsWorkspace } from "@/components/console/BookingsWorkspace";

export default async function BookingsTypesPage() {
  return <BookingsWorkspace me={await requireIdentity()} section="types" />;
}
