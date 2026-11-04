"use client";

import Link from "next/link";
import { bookingsPages } from "@/lib/navigation";
import { Panel } from "../ui";
import type { BookingsScope } from "./parts";

/** Stands in for the calendar until it is built; the next task replaces this component. */
export default function CalendarPlaceholder({ scope, admin }: { scope: BookingsScope; admin: boolean }) {
  const [, types, people] = bookingsPages(admin);
  const href = (path: string) => (scope.query ? `${path}?${scope.query}` : path);
  return (
    <Panel title="The calendar is coming soon">
      <div data-testid="calendar-placeholder" className="space-y-3 text-sm">
        <p className="max-w-prose">
          You will book, move and cancel appointments here. Until then, set up what can be booked.
        </p>
        <ul className="list-disc space-y-1 pl-5">
          <li><Link className="underline" href={href(types.href)}>Booking types</Link>: what people can book.</li>
          <li><Link className="underline" href={href(people.href)}>People &amp; rooms</Link>: who and what can be booked, and when.</li>
        </ul>
      </div>
    </Panel>
  );
}
