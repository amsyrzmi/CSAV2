"use client";

import { useId, type ReactNode } from "react";
import { secondary } from "@/components/console/ui";
import type { ChannelFilter, InboxAssignee, Priority, Reason, SenderFilter, Topic } from "@/lib/inbox-api";
import { REASON_LABELS, TOPIC_LABELS, tabFilters, type QueueFilters, type QueueTabId } from "@/lib/inbox-queue";

/** What `useAssignees` gives the panel: the team, why it is missing, and a retry. */
export type TeamList = {
  people: InboxAssignee[] | null; unavailable: boolean; failed: boolean; loaded: boolean; retry: () => void;
};

const PRIORITY: { value: "" | Priority; label: string }[] = [
  { value: "", label: "Any" },
  { value: "high", label: "High and above" },
  { value: "urgent", label: "Urgent" },
];
const CHANNEL: { value: ChannelFilter; label: string }[] = [
  { value: "", label: "Any" },
  { value: "telegram", label: "Telegram" },
  { value: "whatsapp", label: "WhatsApp" },
];
const SENDER: { value: SenderFilter; label: string }[] = [
  { value: "", label: "Anyone" },
  { value: "known", label: "Customers" },
  { value: "new", label: "New senders" },
];
/** Presets only: the API takes minutes. */
const WAITING: { value: number | null; label: string }[] = [
  { value: null, label: "Any" },
  { value: 15, label: "15 minutes" },
  { value: 60, label: "1 hour" },
  { value: 240, label: "4 hours" },
  { value: 1440, label: "1 day" },
];
const REASONS = Object.keys(REASON_LABELS) as Reason[];
const TOPICS = Object.keys(TOPIC_LABELS) as Topic[];

const option =
  "flex min-h-10 min-w-0 cursor-pointer items-center gap-2 rounded-[var(--fs-radius-control)] px-2 text-sm text-[color:var(--fs-text)] hover:bg-[var(--fs-tile)] pointer-coarse:min-h-11 has-[:disabled]:cursor-not-allowed has-[:disabled]:text-[color:var(--fs-disabled-text)] has-[:disabled]:hover:bg-transparent";
const input =
  "size-4 shrink-0 accent-[var(--fs-primary)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]";

function Group({ legend, hint, columns = false, children }: {
  legend: string; hint?: string; columns?: boolean; children: ReactNode;
}) {
  return <fieldset className="min-w-0 space-y-1">
    <legend className="mb-1 text-xs font-semibold text-ink-700">{legend}</legend>
    {hint && <p className="px-2 text-xs text-ink-600">{hint}</p>}
    <div className={columns ? "grid grid-cols-1 gap-x-2 min-[24rem]:grid-cols-2" : "grid grid-cols-1"}>{children}</div>
  </fieldset>;
}

function Radio({ name, label, checked, disabled = false, onPick }: {
  name: string; label: string; checked: boolean; disabled?: boolean; onPick: () => void;
}) {
  return <label className={option}>
    <input type="radio" name={name} className={input} checked={checked} disabled={disabled} onChange={onPick} />
    <span className="min-w-0 [overflow-wrap:anywhere]">{label}</span>
  </label>;
}

function Check({ label, checked, disabled = false, onToggle }: {
  label: string; checked: boolean; disabled?: boolean; onToggle: (on: boolean) => void;
}) {
  return <label className={option}>
    <input type="checkbox" className={input} checked={checked} disabled={disabled}
      onChange={(event) => onToggle(event.target.checked)} />
    <span className="min-w-0 [overflow-wrap:anywhere]">{label}</span>
  </label>;
}

function toggled<T>(list: T[], value: T, on: boolean, order: readonly T[]): T[] {
  const next = on ? [...list, value] : list.filter((item) => item !== value);
  // Keep the panel's order, so the same choice always sends the same query.
  return order.filter((item) => next.includes(item));
}

/**
 * The Filters choices. Every change applies at once (the list refreshes behind the panel). A
 * filter the tab itself sets (Mine and Unassigned set who it is assigned to, Overdue sets
 * overdue) is shown fixed, so the tab and the list never disagree. The first three assignee
 * choices work without the team list; the people come from the assignee read.
 */
export function FiltersBody({
  tab, tabLabel, filters, onChange, team, meId, chipCount, onClear, onClose,
}: {
  tab: QueueTabId; tabLabel: string; filters: QueueFilters; onChange: (next: QueueFilters) => void;
  team: TeamList; meId: string; chipCount: number; onClear: () => void;
  /** Shown for the popover only; the sheet has its own Close. */
  onClose?: () => void;
}) {
  const id = useId();
  const own = tabFilters(tab);
  const set = (patch: Partial<QueueFilters>) => onChange({ ...filters, ...patch });
  const fixedAssignee = own.assignee !== "";
  const fixedHint = `Set by the ${tabLabel} tab.`;
  const people = (team.people ?? []).filter((person) => person.id !== meId);
  return <div className="space-y-4">
    <Group legend="Assigned to" hint={fixedAssignee ? fixedHint : undefined}>
      {([["", "Anyone"], ["me", "Me"], ["none", "Unassigned"]] as const).map(([value, label]) =>
        <Radio key={value} name={`${id}-assignee`} label={label} checked={filters.assignee === value}
          disabled={fixedAssignee} onPick={() => set({ assignee: value })} />)}
      {people.map((person) => <Radio key={person.id} name={`${id}-assignee`} label={person.display_name}
        checked={filters.assignee === person.id} disabled={fixedAssignee}
        onPick={() => set({ assignee: person.id })} />)}
      {team.failed ? <div className="flex flex-wrap items-center gap-2 px-2 py-1 text-xs text-ink-700">
        <p>Could not load the team.</p>
        <button type="button" className={secondary} onClick={team.retry}>Try again</button>
      </div>
        : team.unavailable ? <p className="px-2 py-1 text-xs text-ink-700">The team list is unavailable right now.</p>
          : !team.loaded ? <p className="px-2 py-1 text-xs text-ink-600">Loading the team…</p>
            : null}
    </Group>
    <Group legend="Priority">
      {PRIORITY.map((choice) => <Radio key={choice.value} name={`${id}-priority`} label={choice.label}
        checked={filters.priorityMin === choice.value} onPick={() => set({ priorityMin: choice.value })} />)}
    </Group>
    <Group legend="Reason" columns>
      {REASONS.map((reason) => <Check key={reason} label={REASON_LABELS[reason]}
        checked={filters.reason.includes(reason)}
        onToggle={(on) => set({ reason: toggled(filters.reason, reason, on, REASONS) })} />)}
    </Group>
    <Group legend="Topic" columns>
      {TOPICS.map((topic) => <Check key={topic} label={TOPIC_LABELS[topic]} checked={filters.topic.includes(topic)}
        onToggle={(on) => set({ topic: toggled(filters.topic, topic, on, TOPICS) })} />)}
    </Group>
    <Group legend="Channel">
      {CHANNEL.map((choice) => <Radio key={choice.value} name={`${id}-channel`} label={choice.label}
        checked={filters.channel === choice.value} onPick={() => set({ channel: choice.value })} />)}
    </Group>
    <Group legend="Sender">
      {SENDER.map((choice) => <Radio key={choice.value} name={`${id}-sender`} label={choice.label}
        checked={filters.sender === choice.value} onPick={() => set({ sender: choice.value })} />)}
    </Group>
    <div className="min-w-0 space-y-1">
      <Check label="Overdue only" checked={filters.overdue} disabled={own.overdue}
        onToggle={(on) => set({ overdue: on })} />
      {own.overdue && <p className="px-2 text-xs text-ink-600">{fixedHint}</p>}
    </div>
    <Group legend="Waiting longer than" columns>
      {WAITING.map((choice) => <Radio key={String(choice.value)} name={`${id}-waiting`} label={choice.label}
        checked={filters.waitingOver === choice.value} onPick={() => set({ waitingOver: choice.value })} />)}
    </Group>
    {(chipCount > 0 || onClose) && <div className="flex flex-wrap justify-end gap-2 border-t border-[color:var(--fs-border)] pt-3">
      {chipCount > 0 && <button type="button" className={secondary} onClick={onClear}>Clear filters</button>}
      {onClose && <button type="button" className={secondary} onClick={onClose}>Close</button>}
    </div>}
  </div>;
}
