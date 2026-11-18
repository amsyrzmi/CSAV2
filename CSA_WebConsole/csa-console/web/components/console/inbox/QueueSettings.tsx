"use client";

import { useEffect, useId, useRef, useState, type FormEvent, type RefObject } from "react";
import { Timer } from "lucide-react";
import SlideOver from "@/components/console/fs/SlideOver";
import { action, field, Notice, secondary } from "@/components/console/ui";
import {
  draftValues, minutesText, reminderSettingText, settingProblem, settingsDraft, TARGET_KEYS,
  type InboxSettings, type InboxSettingsKey, type SettingsDraft,
} from "@/lib/inbox-api";
import { SETTINGS_LOADING, type InboxSettingsData } from "./useQueueData";

type TargetKey = (typeof TARGET_KEYS)[number];

const TARGET_LABELS: Record<TargetKey, string> = {
  target_urgent_min: "Urgent",
  target_high_min: "High",
  target_normal_min: "Normal",
  target_low_min: "Low",
};
const REMINDER_ROWS: [InboxSettingsKey, string][] = [
  ["repeat_minutes", "Time between reminders"],
  ["max_repeats", "Most reminders for one conversation"],
  ["follow_up_hours", "Follow up when the customer has not answered after"],
  ["stale_claim_hours", "Remind when a claimed conversation is quiet for"],
  ["handback_prompt_hours", "Ask to hand back a quiet conversation after"],
];
const INTRO = "How long a conversation can wait for your team before it is overdue, by priority.";
const ONLY_NEW = "A change applies only to conversations that enter the queue after it is saved; those already waiting keep their due time.";
const LATER = "Used by reminders, which come later.";
const STAFF = "Only administrators can change these.";
const SAVED = "Response times saved.";

const muted = "text-sm text-[color:var(--fs-text-secondary)]";
const errorText = "text-sm font-medium text-[color:var(--fs-danger)] [overflow-wrap:anywhere]";
const heading = "font-(family-name:--fs-font-heading) text-sm font-semibold";
/** The look of a disabled button, for one that stays focusable (`aria-disabled`). */
const inertLook = "aria-disabled:cursor-not-allowed aria-disabled:bg-[var(--fs-disabled-bg)] aria-disabled:text-[color:var(--fs-disabled-text)] aria-disabled:hover:bg-[var(--fs-disabled-bg)] aria-disabled:active:scale-100";

/** Label and value rows, stacked on a phone. */
function Values({ rows, testId }: { rows: [string, string][]; testId: string }) {
  return <dl data-testid={testId} className="space-y-2">
    {rows.map(([label, value]) => <div key={label} className="grid gap-0.5 sm:grid-cols-[minmax(0,1fr)_minmax(0,11rem)] sm:gap-3">
      <dt className={`${muted} [overflow-wrap:anywhere]`}>{label}</dt>
      <dd className="min-w-0 text-sm [overflow-wrap:anywhere]">{value}</dd>
    </div>)}
  </dl>;
}

/** The five reminder settings: shown, never edited here, and saved unchanged with the targets. */
function Reminders({ data }: { data: InboxSettings }) {
  const titleId = useId();
  return <section aria-labelledby={titleId} className="space-y-2 border-t border-[color:var(--fs-border)] pt-4">
    <h3 id={titleId} className={heading}>Reminders</h3>
    <p className={muted}>{LATER}</p>
    <Values testId="inbox-response-times-reminders"
      rows={REMINDER_ROWS.map(([key, label]) => [label, reminderSettingText(key, data[key])])} />
  </section>;
}

/** Tenant staff: every value read-only, and who can change them. */
function ReadOnly({ data }: { data: InboxSettings }) {
  return <div className="space-y-4">
    <p className="text-sm font-medium">{STAFF}</p>
    <p className={muted}>{INTRO}</p>
    <Values testId="inbox-response-times-values"
      rows={TARGET_KEYS.map((key) => [TARGET_LABELS[key], reminderSettingText(key, data[key])])} />
    <p className={muted}>{ONLY_NEW}</p>
    <Reminders data={data} />
  </div>;
}

/**
 * An administrator's form: the four targets in minutes, each with its hours-and-minutes reading or,
 * when the server would refuse it, why. Save stays inert while nothing changed, a value is invalid
 * or a save is out (then every field is inert too). The form follows the stored settings again
 * when they were read anew after a conflict. It is unmounted with the panel: a save's confirmation
 * is said only while it is open, but a failure is the settings' own (`failure`), so one that
 * arrives after the panel closed is said when it opens again.
 */
function Editor({ settings, data }: { settings: InboxSettingsData; data: InboxSettings }) {
  const id = useId();
  const [draft, setDraft] = useState<SettingsDraft>(() => settingsDraft(data));
  const [seen, setSeen] = useState(data.version);
  const [status, setStatus] = useState("");
  const inputs = useRef<Partial<Record<TargetKey, HTMLInputElement | null>>>({});
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);
  if (seen !== data.version) {
    setSeen(data.version);
    setDraft(settingsDraft(data));
  }

  const busy = settings.saving;
  const failure = settings.failure;
  const inert = busy || undefined;
  const values = draftValues(draft);
  const changed = values !== null && TARGET_KEYS.some((key) => values[key] !== data[key]);

  function change(key: TargetKey, raw: string) {
    if (busy) return;
    setDraft({ ...draft, [key]: raw });
    setStatus("");
    settings.forget();
  }
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    if (values === null) {
      // Nothing is sent: focus goes to the first field that says why.
      const first = TARGET_KEYS.find((key) => settingProblem(key, draft[key]) !== null);
      if (first) inputs.current[first]?.focus();
      return;
    }
    if (!changed) return;
    setStatus("");
    const result = await settings.save(values);
    if (alive.current && result.ok) setStatus(SAVED);
  }

  return <form noValidate className="space-y-4" onSubmit={(event) => void submit(event)}>
    <p className={muted}>{INTRO}</p>
    <fieldset className="space-y-3">
      <legend className={`${heading} mb-2`}>Answer within, in minutes</legend>
      {TARGET_KEYS.map((key) => {
        const problem = settingProblem(key, draft[key]);
        const hintId = `${id}-${key}-hint`;
        return <div key={key} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1">
          <label htmlFor={`${id}-${key}`} className="text-sm font-medium">{TARGET_LABELS[key]}</label>
          <span className="flex items-center gap-2">
            <input ref={(element) => { inputs.current[key] = element; }} id={`${id}-${key}`}
              className={`${field.replace("w-full", "w-24")} num`} inputMode="numeric" autoComplete="off"
              value={draft[key]} readOnly={busy} aria-disabled={inert}
              aria-invalid={problem ? true : undefined} aria-describedby={hintId}
              onChange={(event) => change(key, event.target.value)} />
            <span aria-hidden className={muted}>min</span>
          </span>
          <p id={hintId} className={`col-span-2 sm:col-span-1 sm:col-start-2 ${problem ? errorText : muted}`}>
            {problem ?? minutesText(Number(draft[key].trim()))}</p>
        </div>;
      })}
    </fieldset>
    <p className={muted}>{ONLY_NEW}</p>
    {failure && (failure.conflict ? <Notice warning>{failure.error}</Notice> : <Notice error>{failure.error}</Notice>)}
    <p role="status" data-testid="inbox-response-times-status" className="text-sm">{status}</p>
    <button type="submit" className={`${action} ${inertLook}`}
      aria-disabled={busy || values === null || !changed || undefined}>{busy ? "Saving…" : "Save"}</button>
    <Reminders data={data} />
  </form>;
}

/** Loading, a failed read with Try again, then the form or the read-only values. */
function TimesBody({ settings, canEdit, bodyRef }: {
  settings: InboxSettingsData; canEdit: boolean; bodyRef: RefObject<HTMLDivElement | null>;
}) {
  let body;
  if (settings.settings !== null) {
    body = canEdit
      ? <Editor key={settings.path} settings={settings} data={settings.settings} />
      : <ReadOnly data={settings.settings} />;
  } else if (settings.failed) {
    body = <div className="space-y-3">
      <p role="alert" className="text-sm [overflow-wrap:anywhere]">{settings.error}</p>
      <button type="button" className={secondary} onClick={() => {
        // The button goes away while the read is out: keep focus in the panel.
        bodyRef.current?.focus();
        settings.retry();
      }}>Try again</button>
    </div>;
  } else {
    body = <p role="status" className={muted}>{SETTINGS_LOADING}</p>;
  }
  return <div ref={bodyRef} tabIndex={-1} className="min-w-0 outline-none">{body}</div>;
}

/**
 * The queue bar's "Response times" button and its panel (design 14): the company's response
 * targets by priority and the reminder settings. The settings are read the first time the panel
 * opens (`onOpen`); administrators edit the targets, tenant staff read them. Escape, Close and the
 * backdrop close the panel and give focus back to the button.
 */
export function ResponseTimes({ settings, canEdit, onOpen }: {
  settings: InboxSettingsData; canEdit: boolean; onOpen: () => void;
}) {
  const button = useRef<HTMLButtonElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  return <>
    <button ref={button} type="button" className={secondary} data-testid="inbox-response-times"
      aria-haspopup="dialog" aria-expanded={open} onClick={() => { onOpen(); setOpen(true); }}>
      <Timer aria-hidden size={16} />Response times</button>
    <SlideOver open={open} onClose={() => {
      setOpen(false);
      // A failure on screen has been seen; one still to come (a save out) is kept for the next open.
      if (!settings.saving) settings.forget();
    }} title="Response times" returnFocusTo={button}
      widthClass="max-w-md" testId="inbox-response-times-panel">
      <TimesBody settings={settings} canEdit={canEdit} bodyRef={bodyRef} />
    </SlideOver>
  </>;
}
