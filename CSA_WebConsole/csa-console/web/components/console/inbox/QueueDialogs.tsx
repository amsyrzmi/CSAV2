"use client";

import { useId, useState, type FormEvent, type ReactNode, type RefObject } from "react";
import Dialog from "@/components/console/fs/Dialog";
import { action, field, secondary } from "@/components/console/ui";
import type { InboxAssignee, Priority, Topic } from "@/lib/inbox-api";
import { customSnoozeUntil, PRIORITY_LABELS, snoozePresets, TOPIC_LABELS } from "@/lib/inbox-queue";
import { useAssignees } from "./useQueueData";

const option =
  "flex min-h-10 min-w-0 cursor-pointer items-center gap-3 rounded-[var(--fs-radius-control)] px-2 text-sm text-[color:var(--fs-text)] hover:bg-[var(--fs-tile)] pointer-coarse:min-h-11";
const radio =
  "size-4 shrink-0 accent-[var(--fs-primary)] focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]";

/** What every dialog here shares: the parent owns `open`; focus goes back to `returnFocusTo`. */
type Shell = { open: boolean; onClose: () => void; returnFocusTo: RefObject<HTMLElement | null> };

/** A form with Cancel and the one action at its foot. Native dialog, Escape and focus come from `Dialog`. */
function DialogForm({ submit, disabled, onCancel, onSubmit, children }: {
  submit: string; disabled: boolean; onCancel: () => void; onSubmit: () => void; children: ReactNode;
}) {
  return <form noValidate className="space-y-4" onSubmit={(event: FormEvent) => {
    event.preventDefault();
    if (!disabled) onSubmit();
  }}>
    {children}
    <div className="flex flex-wrap justify-end gap-2">
      <button type="button" className={secondary} onClick={onCancel}>Cancel</button>
      <button type="submit" className={action} disabled={disabled}>{submit}</button>
    </div>
  </form>;
}

function Choices<T extends string>({ legend, name, values, labels, value, onPick }: {
  legend: string; name: string; values: readonly T[]; labels: Record<T, string>; value: T | null;
  onPick: (value: T) => void;
}) {
  return <fieldset className="min-w-0 space-y-1">
    <legend className="mb-1 text-sm font-medium">{legend}</legend>
    {values.map((item) => <label key={item} className={option}>
      <input type="radio" name={name} className={radio} checked={value === item} onChange={() => onPick(item)} />
      <span className="min-w-0 [overflow-wrap:anywhere]">{labels[item]}</span>
    </label>)}
  </fieldset>;
}

// ---- assign ----

/**
 * Assign to a member of the team. The list is read when the dialog first opens: while it loads, after
 * a failure (with Try again) and while the member directory is down (503) it says so in words, and
 * an empty list is never shown as if nobody could take the item.
 */
export function AssignDialog({ open, onClose, returnFocusTo, query, meId, blocked, onAssign }: Shell & {
  query: string; meId: string | null; blocked: boolean; onAssign: (person: InboxAssignee) => void;
}) {
  const team = useAssignees(query, open);
  return <Dialog open={open} onClose={onClose} title="Assign this conversation" testId="inbox-assign-dialog"
    returnFocusTo={returnFocusTo} widthClass="max-w-md">
    <AssignBody team={team} meId={meId} blocked={blocked} onCancel={onClose} onAssign={onAssign} />
  </Dialog>;
}

function AssignBody({ team, meId, blocked, onCancel, onAssign }: {
  team: ReturnType<typeof useAssignees>; meId: string | null; blocked: boolean;
  onCancel: () => void; onAssign: (person: InboxAssignee) => void;
}) {
  const name = useId();
  const [chosen, setChosen] = useState("");
  const people = team.people ?? [];
  const person = people.find((item) => item.id === chosen);
  let body: ReactNode;
  if (team.failed) {
    body = <div className="space-y-2 text-sm">
      <p>Could not load the team. Try again.</p>
      <button type="button" className={secondary} onClick={team.retry}>Try again</button>
    </div>;
  } else if (team.unavailable) {
    body = <p className="text-sm">The team list is unavailable right now. Try again in a moment.</p>;
  } else if (!team.loaded) {
    body = <p className="text-sm text-ink-600">Loading the team…</p>;
  } else if (people.length === 0) {
    body = <p className="text-sm">Nobody in the team can take conversations yet.</p>;
  } else {
    body = <fieldset className="min-w-0 space-y-1">
      <legend className="mb-1 text-sm font-medium">Assign to</legend>
      {people.map((item) => <label key={item.id} className={option}>
        <input type="radio" name={name} className={radio} checked={chosen === item.id}
          onChange={() => setChosen(item.id)} />
        <span className="min-w-0 [overflow-wrap:anywhere]">
          {item.display_name}{item.id === meId ? " (you)" : ""}</span>
      </label>)}
    </fieldset>;
  }
  return <DialogForm submit="Assign" disabled={blocked || !person} onCancel={onCancel}
    onSubmit={() => { if (person) onAssign(person); }}>{body}</DialogForm>;
}

// ---- priority and topic ----

const PRIORITIES = Object.keys(PRIORITY_LABELS) as Priority[];
const TOPICS = Object.keys(TOPIC_LABELS) as Topic[];

export function PriorityDialog({ open, onClose, returnFocusTo, current, blocked, onSave }: Shell & {
  current: Priority | null; blocked: boolean; onSave: (priority: Priority) => void;
}) {
  return <Dialog open={open} onClose={onClose} title="Set priority" testId="inbox-priority-dialog"
    returnFocusTo={returnFocusTo} widthClass="max-w-sm">
    <PickOne legend="Priority" submit="Save priority" values={PRIORITIES} labels={PRIORITY_LABELS}
      current={current} blocked={blocked} onCancel={onClose} onSave={onSave} />
  </Dialog>;
}

/** The seven topics. The API cannot clear a topic once set, so there is no "No topic" choice. */
export function TopicDialog({ open, onClose, returnFocusTo, current, blocked, onSave }: Shell & {
  current: Topic | null; blocked: boolean; onSave: (topic: Topic) => void;
}) {
  return <Dialog open={open} onClose={onClose} title="Set topic" testId="inbox-topic-dialog"
    returnFocusTo={returnFocusTo} widthClass="max-w-sm">
    <PickOne legend="Topic" submit="Save topic" values={TOPICS} labels={TOPIC_LABELS}
      current={current} blocked={blocked} onCancel={onClose} onSave={onSave} />
  </Dialog>;
}

function PickOne<T extends string>({ legend, submit, values, labels, current, blocked, onCancel, onSave }: {
  legend: string; submit: string; values: readonly T[]; labels: Record<T, string>; current: T | null;
  blocked: boolean; onCancel: () => void; onSave: (value: T) => void;
}) {
  const name = useId();
  const [value, setValue] = useState<T | null>(current);
  return <DialogForm submit={submit} disabled={blocked || value === null} onCancel={onCancel}
    onSubmit={() => { if (value !== null) onSave(value); }}>
    <Choices legend={legend} name={name} values={values} labels={labels} value={value} onPick={setValue} />
  </DialogForm>;
}

// ---- snooze until a picked time ----

/**
 * "Pick a time": a native date and time field read as company time (+08:00), checked in words
 * before anything is sent (in the future, at most 60 days ahead, as the server requires).
 */
export function SnoozeTimeDialog({ open, onClose, returnFocusTo, blocked, onSnooze }: Shell & {
  blocked: boolean; onSnooze: (until: string) => void;
}) {
  return <Dialog open={open} onClose={onClose} title="Snooze until a time you pick" testId="inbox-snooze-custom"
    returnFocusTo={returnFocusTo} widthClass="max-w-sm">
    <SnoozeTimeBody blocked={blocked} onCancel={onClose} onSnooze={onSnooze} />
  </Dialog>;
}

function SnoozeTimeBody({ blocked, onCancel, onSnooze }: {
  blocked: boolean; onCancel: () => void; onSnooze: (until: string) => void;
}) {
  const id = useId();
  // Starts at tomorrow 09:00, the field's own format (no offset; it is read as +08:00).
  const [value, setValue] = useState(() => snoozePresets(new Date())[1].until.slice(0, 16));
  const [error, setError] = useState("");
  return <DialogForm submit="Snooze" disabled={blocked} onCancel={onCancel} onSubmit={() => {
    const picked = customSnoozeUntil(value, Date.now());
    if ("error" in picked) { setError(picked.error); return; }
    onSnooze(picked.until);
  }}>
    <div>
      <label htmlFor={`${id}-field`} className="block text-sm font-medium">Snooze until</label>
      <p id={`${id}-hint`} className="mt-0.5 text-xs text-ink-600">Malaysia time (GMT+8), up to 60 days ahead.</p>
      <input id={`${id}-field`} type="datetime-local" className={`${field} mt-1`} value={value}
        aria-invalid={error ? true : undefined} aria-describedby={error ? `${id}-hint ${id}-error` : `${id}-hint`}
        onChange={(event) => { setValue(event.target.value); setError(""); }} />
      {error && <p id={`${id}-error`} role="alert" className="mt-1 text-sm font-medium text-[color:var(--fs-danger)]">{error}</p>}
    </div>
  </DialogForm>;
}
