import type { PeoplePage, Person } from "./hr-people-api";

/** Pure option builders for the HR pickers. The hooks in components/console/hr/Pickers.tsx call these. */
export type Option = [id: string, label: string];

/** The longest walk through the People list a picker makes: 20 pages of 50. */
export const PEOPLE_PICKER_PAGES = 20;

/** The people read answers 404 to anyone it does not serve, so a picker falls back to a typed reference. */
export function isDenied(cause: unknown): boolean {
  return typeof cause === "object" && cause !== null && (cause as { status?: unknown }).status === 404;
}

export function personLabel(person: Pick<Person, "display_name" | "job_title">): string {
  return person.job_title ? `${person.display_name} · ${person.job_title}` : person.display_name;
}

export async function loadPeopleOptions(
  page: (cursor: string | undefined) => Promise<Pick<PeoplePage, "items" | "next_cursor">>,
): Promise<{ options: Option[]; denied: boolean }> {
  const options: Option[] = [];
  let cursor: string | undefined;
  try {
    for (let count = 0; count < PEOPLE_PICKER_PAGES; count += 1) {
      const result = await page(cursor);
      for (const person of result.items) options.push([person.employee_id, personLabel(person)]);
      if (result.next_cursor === null) break;
      cursor = result.next_cursor;
    }
  } catch (cause) {
    if (isDenied(cause)) return { options: [], denied: true };
    throw cause;
  }
  return { options, denied: false };
}

export function memberOptions(items: { user_id: string; display_name: string | null }[]): Option[] {
  return items.map((member) => [member.user_id, member.display_name ?? member.user_id]);
}

/** "27 Oct 2026, 09:15" in Malaysia time, as the writing guide asks. */
export function shortWhen(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
    hour12: false, timeZone: "Asia/Kuala_Lumpur",
  }).format(new Date(iso));
}

/** Scheduled interviews or shifts, as "Who · when". Cancelled ones cannot be cancelled again. */
export function scheduledOptions<T extends { id: string; starts_at: string; status: string }>(
  items: T[], name: (item: T) => string, when: (iso: string) => string = shortWhen,
): Option[] {
  return items.filter((item) => item.status === "scheduled").map((item) => [item.id, `${name(item)} · ${when(item.starts_at)}`]);
}

/** Payroll requests from the workflow list, as "period · status". */
export function payrollRequestOptions(
  items: { id: string; domain: string; status: string; content: Record<string, string | string[] | null> }[],
): Option[] {
  return items.filter((item) => item.domain === "payroll").map((item) => {
    const { starts_on: start, ends_on: end } = item.content;
    const period = typeof start === "string" && typeof end === "string" ? `${start} to ${end}` : item.id;
    return [item.id, `${period} · ${item.status}`];
  });
}
