"use client";

import { anyFilterOn, cacheNote, categoryLabel, noFilters, presentValues, type EmailFilters, type FilterableThread } from "@/lib/email-view";
import { DateRangeFilter, StatusChips } from "../kit";
import { field, secondary } from "../ui";

type Chip = "unread" | "hasDraft";
const priorityLabel: Record<string, string> = { high: "High", normal: "Normal", low: "Low" };

/**
 * Filters over the emails already loaded (the server has none): two chips, a category and a
 * priority list built from what is present, a day range, and one line saying how far back the
 * loaded emails reach. Nothing here asks the server for anything.
 */
export default function FilterBar({
  threads,
  filters,
  onChange,
  unreadCount,
  draftCount,
  shown,
}: {
  threads: FilterableThread[];
  filters: EmailFilters;
  onChange: (next: EmailFilters) => void;
  unreadCount: number;
  draftCount: number;
  shown: number;
}) {
  const on = anyFilterOn(filters);
  const pressed: Chip[] = [...(filters.unread ? ["unread" as const] : []), ...(filters.hasDraft ? ["hasDraft" as const] : [])];
  const categories = presentValues(threads, "category");
  const priorities = presentValues(threads, "priority");
  return (
    <div data-testid="email-filters" className="card space-y-3 rounded-[14px] p-4">
      <div className="flex flex-wrap items-end gap-x-4 gap-y-3">
        <StatusChips<Chip>
          mode="multi"
          testId="email-chips"
          ariaLabel="Show only"
          value={pressed}
          options={[
            { value: "unread", label: "Unread", count: unreadCount },
            { value: "hasDraft", label: "Has a draft", count: draftCount },
          ]}
          onChange={(next) => {
            const list = Array.isArray(next) ? next : [next];
            onChange({ ...filters, unread: list.includes("unread"), hasDraft: list.includes("hasDraft") });
          }}
        />
        <label className="block min-w-0 grow basis-40 text-sm sm:max-w-56">
          Category
          <select
            data-testid="email-category"
            className={`${field} mt-1`}
            value={filters.category ?? ""}
            onChange={(e) => onChange({ ...filters, category: e.target.value || null })}
          >
            <option value="">All categories</option>
            {categories.map((value) => <option key={value} value={value}>{categoryLabel[value] ?? value}</option>)}
          </select>
        </label>
        <label className="block min-w-0 grow basis-40 text-sm sm:max-w-56">
          Priority
          <select
            data-testid="email-priority"
            className={`${field} mt-1`}
            value={filters.priority ?? ""}
            onChange={(e) => onChange({ ...filters, priority: e.target.value || null })}
          >
            <option value="">All priorities</option>
            {priorities.map((value) => <option key={value} value={value}>{priorityLabel[value] ?? value}</option>)}
          </select>
        </label>
        <div className="text-sm">
          Last activity
          <div className="mt-1">
            <DateRangeFilter
              testId="email-date"
              label="Last activity"
              value={filters.range}
              onChange={(range) => onChange({ ...filters, range })}
            />
          </div>
        </div>
        {on && (
          <button type="button" data-testid="email-clear-filters" className={secondary} onClick={() => onChange(noFilters())}>
            Clear filters
          </button>
        )}
      </div>
      <p
        data-testid="email-cache-note"
        data-filtered={on ? "true" : "false"}
        className={`text-[length:var(--fs-text-sm)] ${on ? "text-[color:var(--fs-text)]" : "text-[color:var(--fs-text-secondary)]"}`}
      >
        {on && <span data-testid="email-match-count" className="mr-2 font-medium">{shown} of {threads.length} emails match.</span>}
        {cacheNote(threads.length)}
      </p>
    </div>
  );
}
