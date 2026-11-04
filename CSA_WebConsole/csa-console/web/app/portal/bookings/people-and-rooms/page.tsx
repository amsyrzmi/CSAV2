import { requireIdentity } from "@/lib/server-identity";
import { BookingsWorkspace } from "@/components/console/BookingsWorkspace";

export default async function BookingsPeoplePage() {
  return <BookingsWorkspace me={await requireIdentity()} section="people" />;
}
