import { requireIdentity } from "@/lib/server-identity";
import { BookingsWorkspace } from "@/components/console/BookingsWorkspace";

export default async function BookingsCalendarPage() {
  return <BookingsWorkspace me={await requireIdentity()} section="calendar" />;
}
