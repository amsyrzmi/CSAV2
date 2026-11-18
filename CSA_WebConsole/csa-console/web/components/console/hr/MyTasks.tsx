"use client";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { processClient, type MyTask, type TaskStatus } from "@/lib/hr-process-api";
import { processLoadError } from "@/lib/hr-process-copy";
import { autoCheckText, dueText, processKindLabel, processToday, statusLabel, type OpenTasks } from "@/lib/hr-process-view";
import { isDenied } from "@/lib/hr-pickers";
import StatusLabel, { type StatusTone } from "../fs/StatusLabel";
import { secondary, Notice, Panel } from "../ui";
import { NOTICE_FOCUS, TaskActions, TaskDialog, useTaskWrites, type TaskOutcome } from "./TaskActions";
import { useHrRunner } from "./useHrRunner";
import { useReturnFocus } from "./useReturnFocus";

const card = "rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3";
const TONES: Record<TaskStatus, StatusTone> = {
  todo: "neutral", awaiting_approval: "info", done: "success", rejected: "attention", skipped: "neutral",
};
const tone = (status: TaskStatus) => (Object.hasOwn(TONES, status) ? TONES[status] : "neutral");

/** `null` while loading; `hidden` when the read is not for this viewer (404). */
type List = { items: MyTask[]; next: string | null } | { hidden: true } | { failed: string } | null;

/**
 * My tasks: the caller's open process steps, the steps waiting for their approval and, for the HR
 * team, the HR pool and the steps of people who cannot get their reminders. A field the API
 * withholds from this viewer is `null` and shows nothing. Read-only members see the list without
 * any button. `onOpenTasks` hears which open steps the list holds (null while it has none to give),
 * so Home can tell a reminder whose step is done. The panel is the `#hr-my-tasks` anchor.
 */
export function MyTasks({ tenant, readonly, onOpenTasks }: {
  tenant: string; readonly: boolean; onOpenTasks?: (open: OpenTasks | null) => void;
}) {
  // Memoised: the client holds the Idempotency-Key of an uncertain write until it succeeds.
  const client = useMemo(() => processClient(tenant), [tenant]);
  const runner = useHrRunner();
  const [list, setList] = useState<List>(null);
  const [reads, setReads] = useState(0);
  const [reloading, setReloading] = useState(false);
  const [outcome, setOutcome] = useState<TaskOutcome | null>(null);
  const [more, setMore] = useState({ busy: false, error: "" });
  // How many pages the list holds: a reload reads as many again from the first, so a write on a later page
  // does not fold the list back to page 1.
  const pages = useRef(1);
  // Bumped by every reload: a Load more that started before it is dropped (its rows carry old versions).
  const generation = useRef(0);
  const panel = useRef<HTMLDivElement>(null);
  const notices = useRef<HTMLDivElement>(null);
  // After a write (and its reload), focus goes back to the row button, or the message, or the heading.
  const returnFocus = useReturnFocus(!reloading && !runner.busy, notices, panel);
  useEffect(() => {
    let active = true;
    const wanted = pages.current;
    (async () => {
      const items: MyTask[] = [];
      let next: string | null = null;
      let read = 0;
      do {
        const page = await client.myTasks(undefined, next ?? undefined);
        items.push(...page.items);
        next = page.next_cursor;
        read += 1;
      } while (next && read < wanted);
      return { items, next, read };
    })()
      .then(({ items, next, read }) => { if (active) { pages.current = read; setList({ items, next }); } })
      .catch(cause => { if (active) setList(isDenied(cause) ? { hidden: true } : { failed: processLoadError(cause) }); })
      .finally(() => { if (active) setReloading(false); });
    return () => { active = false; };
  }, [client, reads]);
  useEffect(() => {
    onOpenTasks?.(list && "items" in list
      ? { ids: new Set(list.items.map(task => task.task_id)), complete: list.next === null } : null);
  }, [list, onOpenTasks]);
  const reload = useCallback(() => {
    generation.current += 1;
    setReloading(true);
    setReads(count => count + 1);
  }, []);
  const onOutcome = useCallback((next: TaskOutcome | null) => {
    setOutcome(next);
    if (!next) return;
    if (next.reload || next.opener) returnFocus(next.opener ?? null);
    if (next.reload) reload();
  }, [reload, returnFocus]);
  const writes = useTaskWrites(client, runner, onOutcome);
  const loadMore = async (items: MyTask[], after: string) => {
    const started = generation.current;
    setMore({ busy: true, error: "" });
    try {
      const page = await client.myTasks(undefined, after);
      if (started === generation.current) {
        pages.current += 1;
        setList({ items: [...items, ...page.items], next: page.next_cursor });
      }
      setMore({ busy: false, error: "" });
    } catch (cause) { setMore({ busy: false, error: started === generation.current ? processLoadError(cause) : "" }); }
  };

  if (list && "hidden" in list) return null;
  const today = processToday();
  const actions = (task: MyTask, describedBy: string) => readonly ? null
    : <TaskActions task={task} writes={writes} disabled={reloading} describedBy={describedBy} />;
  return <div ref={panel} id="hr-my-tasks" data-testid="hr-my-tasks" aria-busy={runner.busy || reloading}><Panel title="My tasks">
    <div className="space-y-3">
      {(outcome?.message || outcome?.error) && <div ref={notices} tabIndex={-1} data-testid="hr-my-tasks-outcome"
        className={`space-y-3 ${NOTICE_FOCUS}`}>
        {outcome.message && <Notice testId="hr-my-tasks-notice">{outcome.message}</Notice>}
        {outcome.error && <Notice error testId="hr-my-tasks-error">{outcome.error}</Notice>}
      </div>}
      {!list ? <Notice>Loading your tasks…</Notice>
        : "failed" in list ? <>
          <Notice error>{list.failed}</Notice>
          <button type="button" className={secondary} onClick={reload}>Try again</button>
        </>
          : list.items.length === 0 ? <p className="text-sm text-ink-500">Nothing waiting for you.</p>
            : <>
              <div className="overflow-x-auto"><table data-testid="hr-my-tasks-table" className="hidden w-full text-left text-sm md:table">
                <thead><tr>{["Step", "Process", "Due", "Status", ...(readonly ? [] : ["Actions"])].map(label =>
                  <th className="p-2" key={label} scope="col">{label}</th>)}</tr></thead>
                <tbody>{list.items.map(task => <tr key={task.task_id} data-testid={`hr-task-row-${task.task_id}`}
                  className="border-t border-ink-200 align-top">
                  <td className="p-2"><TaskText task={task} titleId={`hr-task-title-${task.task_id}`} /></td>
                  <td className="p-2 [overflow-wrap:anywhere]"><ProcessText task={task} /></td>
                  <td className="p-2"><Due task={task} today={today} /></td>
                  <td className="p-2"><StatusLabel tone={tone(task.status)}>{statusLabel(task.status)}</StatusLabel></td>
                  {!readonly && <td className="p-2">{actions(task, `hr-task-title-${task.task_id}`)}</td>}
                </tr>)}</tbody>
              </table>
              <ul data-testid="hr-my-tasks-cards" className="space-y-3 md:hidden">{list.items.map(task =>
                <li key={task.task_id} data-testid={`hr-task-card-${task.task_id}`} className={card}>
                  <TaskText task={task} titleId={`hr-task-card-title-${task.task_id}`} />
                  <dl className="mt-2 space-y-1 text-sm">
                    <div className="flex justify-between gap-3"><dt className="text-[color:var(--fs-text-secondary)]">Process</dt>
                      <dd className="min-w-0 text-right [overflow-wrap:anywhere]"><ProcessText task={task} /></dd></div>
                    <div className="flex justify-between gap-3"><dt className="text-[color:var(--fs-text-secondary)]">Due</dt>
                      <dd className="min-w-0 text-right"><Due task={task} today={today} /></dd></div>
                    <div className="flex items-center justify-between gap-3"><dt className="text-[color:var(--fs-text-secondary)]">Status</dt>
                      <dd><StatusLabel tone={tone(task.status)}>{statusLabel(task.status)}</StatusLabel></dd></div>
                  </dl>
                  {!readonly && <div className="mt-3">{actions(task, `hr-task-card-title-${task.task_id}`)}</div>}
                </li>)}</ul></div>
              {more.error && <Notice error>{more.error}</Notice>}
              {list.next && <button type="button" className={secondary} disabled={more.busy || reloading}
                onClick={() => void loadMore(list.items, list.next as string)}>{more.busy ? "Loading…" : "Load more"}</button>}
            </>}
    </div>
    {!readonly && <TaskDialog writes={writes} />}
  </Panel></div>;
}

/** The step: English title, the Malay line, and whichever texts this viewer may see. */
function TaskText({ task, titleId }: { task: MyTask; titleId: string }) {
  const line = "mt-1 whitespace-pre-line text-sm [overflow-wrap:anywhere]";
  const who = task.mine || task.needs_my_approval ? null : task.pool ? "HR team"
    : task.assignee_name ? `For ${task.assignee_name}` : null;
  return <div className="min-w-0">
    <p id={titleId} className="font-medium [overflow-wrap:anywhere]">{task.title_en}</p>
    {task.title_ms && <p lang="ms" className="text-sm text-[color:var(--fs-text-secondary)] [overflow-wrap:anywhere]">{task.title_ms}</p>}
    {who && <p className="mt-1 text-sm font-medium">{who}</p>}
    {task.description_en && <p className={line}>{task.description_en}</p>}
    {task.description_ms && <p lang="ms" className={`${line} text-[color:var(--fs-text-secondary)]`}>{task.description_ms}</p>}
    {task.auto_check && <p data-testid="hr-task-check" className={line}>{autoCheckText(task.auto_check, task.mine)}</p>}
    {task.reference_note && <p data-testid="hr-task-reference" className={line}>Reference: {task.reference_note}</p>}
    {task.note && <p className={line}>Note: {task.note}</p>}
    {task.sent_back_reason && <p className={`${line} font-medium`}>Sent back: {task.sent_back_reason}</p>}
    {task.done_by_hr && <p className={line}>Done by HR</p>}
  </div>;
}

function ProcessText({ task }: { task: MyTask }) {
  return <>{task.process_name ?? processKindLabel(task.process_kind)}
    <span className="block text-[color:var(--fs-text-secondary)]">{task.employee_name}</span></>;
}

function Due({ task, today }: { task: MyTask; today: string }) {
  return <span className={task.overdue ? "font-medium text-[color:var(--fs-danger)]" : undefined}>{dueText(task, today)}</span>;
}
