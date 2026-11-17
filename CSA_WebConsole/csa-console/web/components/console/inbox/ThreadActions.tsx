"use client";

import {
  useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type RefObject,
} from "react";
import { ChevronDown } from "lucide-react";
import { useDismiss } from "@/components/console/shell/useDismiss";
import { action, secondary } from "@/components/console/ui";
import type { ConsoleRole } from "@/lib/identity-contracts";
import type { Conversation, QueueActionName, QueueFields } from "@/lib/inbox-api";
import { snoozePresets, threadActions } from "@/lib/inbox-queue";
import { dateTime } from "@/lib/inbox-thread";
import { menuIndex } from "@/lib/shell-state";
import { AssignDialog, PriorityDialog, SnoozeTimeDialog, TopicDialog } from "./QueueDialogs";
import type { InboxActions } from "./useInboxActions";
import { useQueueActions } from "./useQueueActions";

/** The existing hand-back question, word for word. */
const RELEASE_QUESTION =
  "Let the assistant reply again? It won't answer messages that came in while you were handling this chat.";

type Entry = { label: string; onSelect: () => void };

const MENU_WIDTH = 240;
const EDGE = 8;
const ITEM =
  "flex min-h-10 w-full items-center rounded-[var(--fs-radius-control)] px-3 text-left text-sm hover:bg-[var(--fs-tile)] focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[color:var(--fs-focus)] pointer-coarse:min-h-11";

/**
 * A button and its `role="menu"` popover. Opening (click, or an arrow key on the button) puts focus
 * on the first item; the arrows, Home and End move it and wrap; Escape closes and returns focus to
 * the button, and so does choosing an item (a dialog it opens returns focus there too). A press or
 * focus outside closes it. The popover is `fixed` and placed from the button, kept inside the
 * screen, so the thread's overflow never clips it. Keys it handles are marked handled, so the Inbox's
 * J, K and Escape (Back on a phone) leave them alone.
 */
function Menu({ label, menuLabel, testId, disabled, entries, triggerRef }: {
  label: string; menuLabel: string; testId: string; disabled: boolean; entries: Entry[];
  triggerRef: RefObject<HTMLButtonElement | null>;
}) {
  const [open, setOpen] = useState(false);
  // A request that starts while it is open (a poll, another control) closes it.
  if (open && disabled) setOpen(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [place, setPlace] = useState<{ top: number; left: number; width: number } | null>(null);
  const close = useCallback(() => setOpen(false), []);
  const dismiss = useDismiss({ open, rootRef, triggerRef, onClose: close });

  useLayoutEffect(() => {
    if (!open) return;
    function measure() {
      const trigger = triggerRef.current;
      if (!trigger) return;
      const box = trigger.getBoundingClientRect();
      const viewport = document.documentElement.clientWidth;
      const width = Math.min(MENU_WIDTH, viewport - 2 * EDGE);
      // Right edges line up; moved left or right as far as needed to stay on screen.
      const left = Math.max(EDGE, Math.min(box.right - width, viewport - EDGE - width));
      const height = menuRef.current?.offsetHeight ?? 0;
      const below = box.bottom + 4;
      const above = box.top - 4 - height;
      const top = below + height > window.innerHeight - EDGE && above >= EDGE ? above : below;
      setPlace({ top, left, width });
    }
    measure();
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
    };
  }, [open, triggerRef]);

  // Focus moves in once the popover is placed: a hidden element cannot take focus.
  const placed = place !== null;
  useEffect(() => {
    if (open && placed) itemRefs.current[0]?.focus({ preventScroll: true });
  }, [open, placed]);

  // React listens on the document here, so stopping propagation cannot keep a key from the Inbox's
  // own document listener: a key the menu handles is marked handled (defaultPrevented) instead.
  function onMenuKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Enter" || event.key === " ") return; // the item's own click
    if (event.key === "Tab") { setOpen(false); return; }
    event.preventDefault();
    if (event.key === "Escape") return; // the root's dismiss handler closes and refocuses
    event.stopPropagation();
    const at = itemRefs.current.findIndex((item) => item === document.activeElement);
    const next = menuIndex(event.key, Math.max(at, 0), entries.length);
    if (next !== null) itemRefs.current[next]?.focus();
  }

  return <div ref={rootRef} className="relative" onKeyDown={(event) => {
    if (event.key === "Escape" && open) event.preventDefault();
    dismiss.onKeyDown(event);
  }}>
    <button ref={triggerRef} type="button" className={secondary} data-testid={testId} disabled={disabled}
      aria-haspopup="menu" aria-expanded={open}
      onClick={() => setOpen((value) => !value)}
      onKeyDown={(event) => {
        if (!open && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
          event.preventDefault();
          setOpen(true);
        }
      }}>
      {label}<ChevronDown aria-hidden size={16} />
    </button>
    {open && <div ref={menuRef} role="menu" aria-label={menuLabel} onKeyDown={onMenuKey}
      style={place ? { top: place.top, left: place.left, width: place.width }
        : { top: 0, left: 0, width: MENU_WIDTH, visibility: "hidden" }}
      className="fixed z-40 rounded-xl border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-1 text-[color:var(--fs-text)] shadow-[var(--fs-shadow-popover)]">
      {entries.map((entry, index) => <button key={entry.label} type="button" role="menuitem" tabIndex={-1}
        ref={(element) => { itemRefs.current[index] = element; }} className={ITEM}
        onClick={() => {
          setOpen(false);
          triggerRef.current?.focus();
          entry.onSelect();
        }}>{entry.label}</button>)}
    </div>}
  </div>;
}

type Dialogs = "assign" | "priority" | "topic" | "snooze" | null;

/**
 * The thread header's actions (design 14.4). The assistant's chats, and rows from a server without
 * the queue fields, keep exactly the old block: Take over, or Hand back after a confirmation, and
 * Take over indefinitely while an escalation has the assistant paused. A queued item gets one
 * primary action, a Snooze menu and a More menu; every one waits while a request is in flight or an
 * uncertain one is kept for Retry. Each outcome refreshes the item and says what happened.
 */
export function ThreadActions({ item, me, query, actions, disabled, onTakeover, onRelease }: {
  item: Conversation; me: { userId: string | null; role: ConsoleRole | null }; query: string;
  actions: InboxActions; disabled: boolean; onTakeover: () => void; onRelease: () => void;
}) {
  const queue = useQueueActions(query, actions);
  const snoozeRef = useRef<HTMLButtonElement>(null);
  const moreRef = useRef<HTMLButtonElement>(null);
  const [dialog, setDialog] = useState<Dialogs>(null);
  const offered = threadActions(item, me);
  const off = disabled || actions.blocked;

  const handBack = () => { if (window.confirm(RELEASE_QUESTION)) onRelease(); };
  const paused = item.human_muted && item.ai_muted_until ? <>
    <p className="w-full text-xs text-ink-700">The assistant is paused until {dateTime(item.ai_muted_until)}.
      Take over indefinitely to keep it paused until you release it.</p>
    <button type="button" className={secondary} disabled={off} onClick={onTakeover}>Take over indefinitely</button>
  </> : null;

  if (!offered) {
    return <div className="flex flex-wrap items-center gap-2">
      {paused}
      <button type="button" data-testid="inbox-primary-action" className={secondary} disabled={off}
        onClick={() => { if (!item.human_muted) onTakeover(); else handBack(); }}>
        {item.human_muted ? "Hand back to assistant" : "Take over"}</button>
    </div>;
  }

  const run = (name: QueueActionName, fields?: QueueFields) => { void queue.run(name, item, fields); };
  const first = offered.primary;
  const primaryLabel = first.kind === "take_it" ? "Take it"
    : first.kind === "take_from" ? `Take it from ${first.name}`
      : first.kind === "resolve" ? "Resolve" : "";

  const snoozeEntries: Entry[] = [
    ...["Later today", "Tomorrow 09:00", "Next Monday 09:00"].map((label, index) => ({
      label, onSelect: () => run("snooze", { until: snoozePresets(new Date())[index].until }),
    })),
    { label: "Pick a time…", onSelect: () => setDialog("snooze") },
  ];
  const moreEntries: Entry[] = [
    ...offered.assign ? [{ label: "Assign to…", onSelect: () => setDialog("assign") }] : [],
    ...offered.unclaim ? [{ label: "Give up this item", onSelect: () => run("unclaim") }] : [],
    ...offered.priority ? [{ label: "Set priority…", onSelect: () => setDialog("priority") }] : [],
    ...offered.topic ? [{ label: "Set topic…", onSelect: () => setDialog("topic") }] : [],
    ...offered.takeOver ? [{ label: "Take over the chat", onSelect: onTakeover }] : [],
    ...offered.handBack ? [{ label: "Hand back to the assistant", onSelect: handBack }] : [],
  ];
  const closeDialog = () => setDialog(null);

  return <div className="flex flex-wrap items-center gap-2">
    {primaryLabel && <button type="button" data-testid="inbox-primary-action" className={action} disabled={off}
      onClick={() => run(first.kind === "resolve" ? "resolve" : "claim")}>{primaryLabel}</button>}
    {offered.snooze && <Menu label="Snooze" menuLabel="Snooze" testId="inbox-snooze-menu" disabled={off}
      entries={snoozeEntries} triggerRef={snoozeRef} />}
    {moreEntries.length > 0 && <Menu label="More" menuLabel="More actions" testId="inbox-more-menu" disabled={off}
      entries={moreEntries} triggerRef={moreRef} />}
    {paused}
    <AssignDialog open={dialog === "assign"} onClose={closeDialog} returnFocusTo={moreRef} query={query}
      meId={me.userId} blocked={off} onAssign={(person) => {
        closeDialog();
        run("assign", { assignee_id: person.id, assigneeName: person.display_name });
      }} />
    <PriorityDialog open={dialog === "priority"} onClose={closeDialog} returnFocusTo={moreRef}
      current={item.priority ?? null} blocked={off} onSave={(priority) => { closeDialog(); run("priority", { priority }); }} />
    <TopicDialog open={dialog === "topic"} onClose={closeDialog} returnFocusTo={moreRef}
      current={item.topic ?? null} blocked={off} onSave={(topic) => { closeDialog(); run("topic", { topic }); }} />
    <SnoozeTimeDialog open={dialog === "snooze"} onClose={closeDialog} returnFocusTo={snoozeRef}
      blocked={off} onSnooze={(until) => { closeDialog(); run("snooze", { until }); }} />
  </div>;
}
