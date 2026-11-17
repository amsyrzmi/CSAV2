"use client";
import { useEffect, useRef, useState } from "react";
import type { hrClient } from "@/lib/hr-api";
import { processSaveError } from "@/lib/hr-process-copy";
import {
  markingText, processToday, reminderDue, reminderLayout, type HomeReminder, type OpenTasks,
} from "@/lib/hr-process-view";
import StatusLabel from "../fs/StatusLabel";
import { secondary, Notice } from "../ui";
import { NOTICE_FOCUS } from "./TaskActions";

const row = "flex flex-wrap items-start justify-between gap-3 border-b border-ink-200 py-3 text-sm";
type Outcome = { message: string; error?: string };
/** Mark all read announces its progress at the first row and then every this many rows. */
const ANNOUNCE_EVERY = 5;

/**
 * Home's reminders. A task reminder shows its line and when the step is due, links to My tasks
 * on this page and, grouped by kind with a count once there are more than a few; one whose step
 * My tasks no longer holds is a quiet Done line with nothing to do. Every other kind reads as it
 * always did. Mark read marks one; Mark all read marks the unread ones in screen order, one
 * request at a time, and stops at the first that fails. Read-only members cannot mark (as before).
 */
export function HomeReminders({ client, reminders, open, readonly, busy, reload }: {
  client: ReturnType<typeof hrClient>; reminders: HomeReminder[]; open: OpenTasks | null;
  readonly: boolean; busy: boolean; reload: () => Promise<void>;
}) {
  const [marking, setMarking] = useState<{ done: number; total: number } | null>(null);
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  // What the live region says: the start, every few rows and the result, never each row.
  const [announced, setAnnounced] = useState("");
  const flight = useRef(false);
  const notice = useRef<HTMLDivElement>(null);
  // A new outcome object each time, so the same answer twice still moves focus to it.
  useEffect(() => { if (outcome) notice.current?.focus(); }, [outcome]);
  const layout = reminderLayout(reminders, open);
  const unread = layout.order.filter(item => !item.read_at);
  const today = processToday();
  const locked = busy || marking !== null || readonly;

  async function mark(items: HomeReminder[], single: boolean) {
    if (flight.current || items.length === 0) return;
    flight.current = true; setOutcome(null); setAnnounced("");
    let marked = 0;
    let failure: unknown = null;
    for (const item of items) {
      if (!single) {
        setMarking({ done: marked + 1, total: items.length });
        if (marked % ANNOUNCE_EVERY === 0) setAnnounced(markingText(marked + 1, items.length));
      }
      try { await client.operation({ operation: "notification.read", notification_id: item.id }); marked += 1; }
      catch (cause) { failure = cause; break; }
    }
    let reloadFailed = false;
    try { await reload(); } catch { reloadFailed = true; }
    setMarking(null); flight.current = false;
    const reloadText = reloadFailed ? " The list could not refresh: choose Refresh reminders." : "";
    const result: Outcome = failure !== null
      ? {
        message: single ? "" : `Marked ${marked} of ${items.length} as read.${reloadText}`,
        error: processSaveError(failure, single ? "mark-read" : "mark-all-read"),
      }
      : { message: `${single ? "Marked as read." : `Marked ${marked} reminder${marked === 1 ? "" : "s"} as read.`}${reloadText}` };
    setOutcome(result);
    if (!single) setAnnounced(result.message);
  }

  const markRead = (item: HomeReminder, describedBy: string) => !item.read_at && <button type="button" className={secondary}
    disabled={locked} aria-describedby={describedBy} onClick={() => void mark([item], true)}>Mark read</button>;
  const live = (item: HomeReminder) => {
    const textId = `hr-reminder-text-${item.id}`;
    const due = reminderDue(item, today);
    return <li key={item.id} data-testid={`hr-reminder-${item.id}`} className={row}>
      <div className="min-w-0">
        <a id={textId} className="font-medium underline [overflow-wrap:anywhere]" href="#hr-my-tasks">{item.subject}</a>
        {due && <p className="text-[color:var(--fs-text-secondary)]">{due}</p>}
      </div>
      {markRead(item, textId)}
    </li>;
  };

  return <div className="space-y-4">
    {!readonly && unread.length > 0 && <button type="button" className={secondary} disabled={locked}
      onClick={() => void mark(unread, false)}>Mark all read</button>}
    {/* Mounted empty, so the first text is announced; the visible progress line is not live. */}
    <p role="status" data-testid="hr-reminders-status" className="sr-only">{announced}</p>
    {marking && <p data-testid="hr-reminders-progress" className="text-sm">{markingText(marking.done, marking.total)}</p>}
    {outcome && <div ref={notice} tabIndex={-1} data-testid="hr-reminders-outcome" className={`space-y-2 ${NOTICE_FOCUS}`}>
      {outcome.message && <Notice>{outcome.message}</Notice>}
      {outcome.error && <Notice error>{outcome.error}</Notice>}
    </div>}
    {(layout.order.length > layout.other.length) && <div data-testid="hr-task-reminders" className="space-y-4">
      {layout.grouped ? layout.groups.map(group => <section key={group.kind} aria-labelledby={`hr-reminders-${group.kind}`}>
        <h3 id={`hr-reminders-${group.kind}`} className="text-sm font-semibold">{group.label} ({group.count})</h3>
        <ul>{group.items.map(live)}</ul>
      </section>)
        : layout.groups.length > 0 && <ul>{layout.groups.flatMap(group => group.items).map(live)}</ul>}
      {layout.done.length > 0 && <section aria-labelledby="hr-reminders-done">
        <h3 id="hr-reminders-done" className="text-sm font-semibold">Already done ({layout.done.length})</h3>
        <ul>{layout.done.map(item => <li key={item.id} data-testid={`hr-reminder-${item.id}`} className={row}>
          <span className="min-w-0 text-[color:var(--fs-text-secondary)] [overflow-wrap:anywhere]">{item.subject}</span>
          <StatusLabel tone="neutral">Done</StatusLabel>
        </li>)}</ul>
      </section>}
    </div>}
    {layout.other.length > 0 && <div className="space-y-3">{layout.other.map(item =>
      <div key={item.id} data-testid={`hr-reminder-${item.id}`} className="flex flex-wrap justify-between gap-3 border-b border-ink-200 py-3 text-sm">
        <span id={`hr-reminder-text-${item.id}`}>{item.kind.replaceAll("_", " ")} · {item.resource_id}</span>
        {markRead(item, `hr-reminder-text-${item.id}`)}
      </div>)}</div>}
  </div>;
}
