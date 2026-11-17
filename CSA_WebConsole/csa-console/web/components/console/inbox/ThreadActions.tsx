"use client";

import { useRef, useState } from "react";
import { action, secondary } from "@/components/console/ui";
import type { ConsoleRole } from "@/lib/identity-contracts";
import type { Conversation, QueueActionName, QueueFields } from "@/lib/inbox-api";
import { snoozePresets, threadActions } from "@/lib/inbox-queue";
import { dateTime } from "@/lib/inbox-thread";
import { Menu, type Entry } from "./Menu";
import { AssignDialog, PriorityDialog, SnoozeTimeDialog, TopicDialog } from "./QueueDialogs";
import type { InboxActions } from "./useInboxActions";
import { useQueueActions } from "./useQueueActions";

/** The existing hand-back question, word for word. */
const RELEASE_QUESTION =
  "Let the assistant reply again? It won't answer messages that came in while you were handling this chat.";

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
