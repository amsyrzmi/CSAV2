"use client";

import { useId, useRef, useState, type RefObject } from "react";
import { ListChecks } from "lucide-react";
import { secondary } from "@/components/console/ui";
import type { QueueActionName, QueueFields } from "@/lib/inbox-api";
import { snoozePresets } from "@/lib/inbox-queue";
import { AssignDialog, SnoozeTimeDialog } from "./QueueDialogs";
import { Menu, type Entry } from "./ThreadActions";

const ring = "focus-visible:outline-3 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--fs-focus)]";
/** A quiet text button, as "Clear filters" is in the queue bar. */
const quiet = `inline-flex min-h-10 items-center rounded-[var(--fs-radius-control)] px-2 text-sm font-medium text-[color:var(--fs-primary)] underline-offset-2 hover:underline pointer-coarse:min-h-11 disabled:cursor-not-allowed disabled:text-[color:var(--fs-disabled-text)] disabled:no-underline ${ring}`;
const SNOOZE_LABELS = ["Later today", "Tomorrow 09:00", "Next Monday 09:00"];

/**
 * "Select all loaded": it takes the rows on screen, never older pages, and at most 50; the hint
 * says so, and the bar says what it did.
 */
function SelectAll({ disabled, onSelectAll }: { disabled: boolean; onSelectAll: () => void }) {
  const hint = useId();
  return <>
    <button type="button" className={quiet} disabled={disabled} aria-describedby={hint}
      onClick={onSelectAll}>Select all loaded</button>
    <span id={hint} className="sr-only">This selects the conversations loaded so far, up to 50.</span>
  </>;
}

/**
 * Select mode for touch screens (and anyone who wants the boxes in the tab order): every row
 * shows its box. While it is on and nothing is selected yet, Select all loaded sits beside it.
 */
export function SelectToggle({ on, onToggle, canSelectAll, onSelectAll, disabled, toggleRef }: {
  on: boolean; onToggle: () => void; canSelectAll: boolean; onSelectAll: () => void; disabled: boolean;
  toggleRef: RefObject<HTMLButtonElement | null>;
}) {
  return <>
    <button ref={toggleRef} type="button" data-testid="inbox-select-mode" aria-pressed={on} onClick={onToggle}
      className={`${secondary} aria-pressed:border-brand-600 aria-pressed:bg-brand-50`}>
      <ListChecks aria-hidden size={16} />Select</button>
    {on && canSelectAll && <SelectAll disabled={disabled} onSelectAll={onSelectAll} />}
  </>;
}

/**
 * The bulk bar (design 14.6), in place of the queue bar's second line while anything is selected:
 * the count, Claim, Assign (administrators only), Snooze, Resolve, Select all loaded and Clear.
 * Every action waits while a request is in flight or an uncertain one is kept for Retry. `message`
 * says what Select all did or why the 51st box would not check.
 */
export function BulkBar({
  count, message, canSelectAll, onSelectAll, onClear, blocked, canAssign, query, meId, onRun, barRef,
}: {
  count: number; message: string; canSelectAll: boolean; onSelectAll: () => void; onClear: () => void;
  blocked: boolean; canAssign: boolean; query: string; meId: string | null;
  onRun: (action: QueueActionName, fields?: QueueFields) => void;
  barRef: RefObject<HTMLDivElement | null>;
}) {
  const assignRef = useRef<HTMLButtonElement>(null);
  const snoozeRef = useRef<HTMLButtonElement>(null);
  const [dialog, setDialog] = useState<"assign" | "snooze" | null>(null);
  const closeDialog = () => setDialog(null);
  // The preset times are worked out when one is chosen, as in the thread's Snooze menu.
  const snoozeEntries: Entry[] = [
    ...SNOOZE_LABELS.map((label, index) => ({
      label, onSelect: () => onRun("snooze", { until: snoozePresets(new Date())[index].until }),
    })),
    { label: "Pick a time…", onSelect: () => setDialog("snooze") },
  ];
  return <div ref={barRef} role="group" aria-label="Selected conversations" data-testid="inbox-bulk-bar"
    className="flex min-w-0 flex-wrap items-center gap-2">
    <p role="status" className="min-w-0 pr-1 text-sm font-semibold text-[color:var(--fs-text)]">
      <span className="num">{count}</span> selected</p>
    <button type="button" className={secondary} disabled={blocked} onClick={() => onRun("claim")}>Claim</button>
    {canAssign && <button ref={assignRef} type="button" className={secondary} disabled={blocked}
      onClick={() => setDialog("assign")}>Assign…</button>}
    <Menu label="Snooze" menuLabel="Snooze the selected conversations" testId="inbox-bulk-snooze" disabled={blocked}
      entries={snoozeEntries} triggerRef={snoozeRef} />
    <button type="button" className={secondary} disabled={blocked} onClick={() => onRun("resolve")}>Resolve</button>
    {canSelectAll && <SelectAll disabled={blocked} onSelectAll={onSelectAll} />}
    <button type="button" className={quiet} onClick={onClear}>Clear</button>
    {/* Always mounted, so a new sentence is announced. */}
    <p role="status" className={message ? "w-full text-xs text-ink-700" : "sr-only"}>{message}</p>
    {canAssign && <AssignDialog open={dialog === "assign"} onClose={closeDialog} returnFocusTo={assignRef}
      query={query} meId={meId} blocked={blocked} title={`Assign ${count} ${count === 1 ? "conversation" : "conversations"}`}
      onAssign={(person) => {
        closeDialog();
        onRun("assign", { assignee_id: person.id, assigneeName: person.display_name });
      }} />}
    <SnoozeTimeDialog open={dialog === "snooze"} onClose={closeDialog} returnFocusTo={snoozeRef}
      blocked={blocked} onSnooze={(until) => { closeDialog(); onRun("snooze", { until }); }} />
  </div>;
}
