"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { AlertTriangle, BadgeCheck, CircleDashed } from "lucide-react";
import { ApiError } from "@/lib/api";
import {
  deleteTarget,
  saveTarget,
  testTarget,
  type NotificationLanguage,
  type NotificationPriority,
  type NotificationTarget,
} from "@/lib/notifications-settings-api";
import {
  eventLabel,
  failureLine,
  priorityLabel,
  quietHoursText,
  validateQuietHours,
} from "@/lib/notifications-settings-view";
import { Check, input, mutedText, useAction } from "../bookings/parts";
import { action, Notice, secondary } from "../ui";
import type { NotificationsScope } from "./NotificationsWorkspace";
import { EMAIL_OFF, PRIORITIES } from "./MyNotificationsPanel";
import { browserZone, zoneChoices } from "./zones";

const LANGUAGES: { value: NotificationLanguage; label: string }[] = [
  { value: "en", label: "English" },
  { value: "ms", label: "Malay" },
];
const languageLabel = (value: string) => LANGUAGES.find((item) => item.value === value)?.label ?? value;
const channelLabel = (channel: string) => (channel === "email" ? "Email" : "Telegram");

interface Draft {
  events: string[];
  min: NotificationPriority;
  quiet: boolean;
  start: string;
  end: string;
  tz: string;
  language: NotificationLanguage;
  enabled: boolean;
}

/** The form as it starts: the saved values, and a sensible window to start from when there is none. */
function draftOf(target: NotificationTarget): Draft {
  const quiet = target.quiet_hours;
  return {
    events: target.events,
    min: target.min_priority,
    quiet: quiet !== null,
    start: quiet?.start ?? "22:00",
    end: quiet?.end ?? "07:00",
    tz: quiet?.tz ?? browserZone(),
    language: target.language,
    enabled: target.is_enabled,
  };
}

function VerifiedChip({ target }: { target: NotificationTarget }) {
  const tone = target.verified
    ? "border-[var(--fs-success)]/30 bg-[var(--fs-success-bg)] text-[var(--fs-success)]"
    : "border-[color:var(--fs-border)] bg-[var(--fs-neutral-bg)] text-[color:var(--fs-neutral-text)]";
  const Icon = target.verified ? BadgeCheck : CircleDashed;
  return (
    <span data-testid={`target-verified-${target.id}`}
      className={`inline-flex items-center gap-1 rounded-[var(--fs-radius-round)] border px-2 py-0.5 text-xs ${tone}`}>
      <Icon className="size-3.5" aria-hidden="true" />
      {target.verified ? "Verified" : "Not verified"}
    </span>
  );
}

function Failure({ target, zone }: { target: NotificationTarget; zone: string }) {
  const line = failureLine(target, zone);
  if (!line) return null;
  return (
    <p data-testid={`target-failure-${target.id}`}
      className="flex items-start gap-2 text-sm text-[color:var(--fs-warning)] [overflow-wrap:anywhere]">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <span>{line}</span>
    </p>
  );
}

function Values({ target }: { target: NotificationTarget }) {
  const rows: [string, string][] = [
    ["Alerts about", target.events.length ? target.events.map(eventLabel).join(", ") : "Nothing"],
    ["Lowest priority", priorityLabel(target.min_priority)],
    ["Quiet hours", quietHoursText(target.quiet_hours)],
    ["Language", languageLabel(target.language)],
    ["Sending", target.is_enabled ? "On" : "Off"],
  ];
  return (
    <dl className="space-y-2" data-testid={`target-values-${target.id}`}>
      {rows.map(([label, value]) => (
        <div key={label} className="grid gap-0.5 sm:grid-cols-[10rem_minmax(0,1fr)] sm:gap-3">
          <dt className="text-sm text-[color:var(--fs-text-secondary)]">{label}</dt>
          <dd className="min-w-0 text-sm [overflow-wrap:anywhere]">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** One alert destination. An Admin edits it; everyone else reads it. */
export default function TargetCard({ scope, target, offered, emailAvailable, zone, onChange, onRemoved, reload }: {
  scope: NotificationsScope;
  target: NotificationTarget;
  offered: string[];
  emailAvailable: boolean;
  zone: string;
  onChange: (target: NotificationTarget) => void;
  onRemoved: (id: string) => void;
  reload: () => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(target));
  const [seen, setSeen] = useState(target.version);
  const [problem, setProblem] = useState("");
  const [note, setNote] = useState("");
  const [confirming, setConfirming] = useState(false);
  const saving = useAction();
  const testing = useAction();
  const removing = useAction();
  const quietId = useId();
  const email = target.channel === "email";
  // A save (or a reload after a clash) moves the version: the form follows what is saved now.
  if (seen !== target.version) {
    setSeen(target.version);
    setDraft(draftOf(target));
  }
  const change = (patch: Partial<Draft>) => {
    setDraft({ ...draft, ...patch });
    setProblem("");
    setNote("");
  };

  async function save() {
    setNote("");
    const found = draft.quiet ? validateQuietHours(draft.start, draft.end, draft.tz) : null;
    setProblem(found ?? "");
    if (found) return;
    const done = await saving.run(async () => {
      try {
        return await saveTarget(scope.tenantId, target.id, {
          events: offered.filter((event) => draft.events.includes(event)),
          min_priority: draft.min,
          quiet_hours: draft.quiet ? { start: draft.start, end: draft.end, tz: draft.tz } : null,
          language: draft.language,
          is_enabled: draft.enabled,
          version: target.version,
        });
      } catch (cause) {
        // Someone saved first: read what is saved now, so the next try is made on it.
        if (cause instanceof ApiError && cause.status === 409) reload();
        throw cause;
      }
    });
    if (!done) return;
    onChange(done);
    setNote("Saved.");
  }

  async function sendTest() {
    setNote("");
    const done = await testing.run(() => testTarget(scope.tenantId, target.id));
    if (!done) return;
    onChange(done);
    setNote("Test email sent. This address is verified.");
  }

  async function remove() {
    const done = await removing.run(async () => {
      await deleteTarget(scope.tenantId, target.id);
      return true;
    });
    if (done) onRemoved(target.id);
    else setConfirming(false);
  }

  const events = offered.length ? offered : target.events;
  const stuck = scope.canEdit && email && !emailAvailable;
  const errors = [problem, saving.error, testing.error, removing.error].filter(Boolean);

  return (
    <li data-testid={`target-card-${target.id}`}
      className="min-w-0 space-y-4 rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <h3 className="text-sm font-semibold">{channelLabel(target.channel)}</h3>
        <span className="min-w-0 text-sm [overflow-wrap:anywhere]" data-testid={`target-address-${target.id}`}>
          {target.address_masked}
        </span>
        <VerifiedChip target={target} />
      </div>
      <Failure target={target} zone={zone} />
      {!email && (
        <p className={mutedText} data-testid={`target-channels-${target.id}`}>
          Connect or change the chat on{" "}
          <Link className="underline" href={scope.tenantId ? `/services?${scope.query}` : "/portal/services"}>Channels</Link>.
        </p>
      )}
      {!scope.canEdit ? (
        <Values target={target} />
      ) : (
        <form className="space-y-4" onSubmit={(event) => { event.preventDefault(); void save(); }}>
          <fieldset className="min-w-0">
            <legend className="text-sm">Alerts about</legend>
            <div className="mt-1 grid gap-x-4 sm:grid-cols-2">
              {events.map((event) => (
                <Check key={event} testId={`target-event-${target.id}-${event}`} checked={draft.events.includes(event)}
                  onChange={(on) => change({ events: on ? [...draft.events, event] : draft.events.filter((item) => item !== event) })}>
                  {eventLabel(event)}
                </Check>
              ))}
            </div>
          </fieldset>
          <div className="grid gap-4 sm:grid-cols-2">
            <label className="block text-sm">Lowest priority to send
              <select className={`${input} mt-1`} data-testid={`target-min-${target.id}`} value={draft.min}
                onChange={(event) => change({ min: event.target.value as NotificationPriority })}>
                {PRIORITIES.map((priority) => <option key={priority} value={priority}>{priorityLabel(priority)}</option>)}
              </select>
            </label>
            <label className="block text-sm">Language
              <select className={`${input} mt-1`} data-testid={`target-language-${target.id}`} value={draft.language}
                onChange={(event) => change({ language: event.target.value as NotificationLanguage })}>
                {LANGUAGES.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
              </select>
            </label>
          </div>
          <div>
            <Check testId={`target-quiet-${target.id}`} checked={draft.quiet} onChange={(on) => change({ quiet: on })}>
              Quiet hours: only urgent alerts are sent
            </Check>
            {draft.quiet && (
              <div className="mt-1 grid gap-3 sm:grid-cols-3" role="group" aria-labelledby={quietId}>
                <span id={quietId} className="sr-only">Quiet hours window</span>
                <label className="block text-sm">From
                  <input type="time" className={`${input} mt-1`} data-testid={`target-start-${target.id}`}
                    value={draft.start} onChange={(event) => change({ start: event.target.value })} />
                </label>
                <label className="block text-sm">Until
                  <input type="time" className={`${input} mt-1`} data-testid={`target-end-${target.id}`}
                    value={draft.end} onChange={(event) => change({ end: event.target.value })} />
                </label>
                <label className="block text-sm">Time zone
                  <select className={`${input} mt-1`} data-testid={`target-tz-${target.id}`} value={draft.tz}
                    onChange={(event) => change({ tz: event.target.value })}>
                    {zoneChoices(draft.tz).map((name) => <option key={name} value={name}>{name}</option>)}
                  </select>
                </label>
              </div>
            )}
          </div>
          <Check testId={`target-enabled-${target.id}`} checked={draft.enabled} onChange={(on) => change({ enabled: on })}>
            Send alerts to this {email ? "address" : "chat"}
          </Check>
          {stuck && <p className={mutedText} data-testid={`target-email-off-${target.id}`}>{EMAIL_OFF}.</p>}
          {errors.map((text) => <Notice key={text} error testId={`target-error-${target.id}`}>{text}</Notice>)}
          {note && <Notice testId={`target-notice-${target.id}`}>{note}</Notice>}
          <div className="flex flex-wrap gap-2">
            <button type="submit" className={action} disabled={saving.busy} aria-label={`Save changes to ${target.address_masked}`}
              data-testid={`target-save-${target.id}`}>Save</button>
            {email && (
              <button type="button" className={secondary} disabled={testing.busy || !emailAvailable}
                aria-label={`Send test email to ${target.address_masked}`} data-testid={`target-test-${target.id}`}
                onClick={() => void sendTest()}>Send test</button>
            )}
            {email && !confirming && (
              <button type="button" className={secondary} aria-label={`Remove ${target.address_masked}`}
                data-testid={`target-remove-${target.id}`} onClick={() => setConfirming(true)}>Remove</button>
            )}
          </div>
          {email && confirming && (
            <div role="group" aria-label={`Remove ${target.address_masked}?`} data-testid={`target-confirm-${target.id}`}
              className="space-y-3 rounded-[var(--fs-radius-control)] border border-[var(--fs-danger)]/30 bg-[var(--fs-danger-bg)] p-3">
              <p className="text-sm">Remove {target.address_masked}? Alerts will stop going to it.</p>
              <div className="flex flex-wrap gap-2">
                <button type="button" className={action} disabled={removing.busy}
                  data-testid={`target-remove-confirm-${target.id}`} onClick={() => void remove()}>Yes, remove it</button>
                <button type="button" className={secondary} onClick={() => setConfirming(false)}
                  data-testid={`target-remove-cancel-${target.id}`}>Keep it</button>
              </div>
            </div>
          )}
        </form>
      )}
    </li>
  );
}
