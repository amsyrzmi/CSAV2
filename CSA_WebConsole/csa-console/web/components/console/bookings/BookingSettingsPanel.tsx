"use client";

import { useId, useState, type ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { saveSettings, type BookingSettings } from "@/lib/bookings-api";
import {
  durationText,
  formToSettingsIn,
  settingsProblem,
  settingsToForm,
  type ReminderUnit,
  type SettingsForm,
} from "@/lib/bookings-view";
import { action, Notice, Panel } from "../ui";
import { Field, input, mutedText, useAction, type BookingsScope, type Loaded } from "./parts";

const REMINDER = "Remind people before a booking";
const PENDING = "How long a request waits for staff";
const LIMIT = "Most upcoming bookings one customer can hold";

const hoursText = (hours: number) => `${hours} hour${hours === 1 ? "" : "s"}`;

function Values({ settings }: { settings: BookingSettings }) {
  const rows: [string, string][] = [
    [REMINDER, durationText(settings.reminder_minutes)],
    [PENDING, hoursText(settings.pending_hours)],
    [LIMIT, String(settings.customer_limit)],
  ];
  return (
    <dl data-testid="settings-values" className="space-y-2">
      {rows.map(([label, value]) => (
        <div key={label} className="grid gap-0.5 sm:grid-cols-[minmax(0,1fr)_8rem] sm:gap-3">
          <dt className="text-sm text-[color:var(--fs-text-secondary)]">{label}</dt>
          <dd className="min-w-0 text-sm [overflow-wrap:anywhere]">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** An Admin's form. It follows the saved settings again when they were read anew after a clash. */
function Editor({ scope, settings, data }: { scope: BookingsScope; settings: Loaded<BookingSettings>; data: BookingSettings }) {
  const [form, setForm] = useState<SettingsForm>(() => settingsToForm(data));
  const [seen, setSeen] = useState(data.version);
  const [problem, setProblem] = useState("");
  const [saved, setSaved] = useState(false);
  const { busy, error, run } = useAction();
  const reminderId = useId();
  if (seen !== data.version) {
    setSeen(data.version);
    setForm(settingsToForm(data));
  }
  // A stored time that hours cannot say exactly is shown in minutes rather than rounded.
  const minutesToo = form.reminderUnit === "minutes" || settingsToForm(data).reminderUnit === "minutes";
  const change = (patch: Partial<SettingsForm>) => {
    setForm({ ...form, ...patch });
    setProblem("");
    setSaved(false);
  };

  async function save() {
    const found = settingsProblem(form);
    setSaved(false);
    setProblem(found ?? "");
    if (found) return;
    const done = await run(async () => {
      try {
        return await saveSettings(scope.tenantId, formToSettingsIn(form, data.version));
      } catch (cause) {
        // Someone saved first: read what is saved now, so the next try is made on it.
        if (cause instanceof ApiError && cause.status === 409) settings.reload();
        throw cause;
      }
    });
    if (!done) return;
    setSeen(done.version);
    settings.replace(done);
    setSaved(true);
  }

  return (
    <form
      className="space-y-4"
      data-testid="settings-form"
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <div className="text-sm">
        <label htmlFor={reminderId}>{REMINDER}</label>
        <div className="mt-1 flex flex-wrap gap-2">
          <input id={reminderId} className={`${input} w-28`} inputMode="decimal" data-testid="settings-reminder"
            value={form.reminder} onChange={(event) => change({ reminder: event.target.value })} />
          <select className={`${input} w-32`} aria-label="Reminder time unit" data-testid="settings-reminder-unit"
            value={form.reminderUnit} onChange={(event) => change({ reminderUnit: event.target.value as ReminderUnit })}>
            {minutesToo && <option value="minutes">minutes</option>}
            <option value="hours">hours</option>
            <option value="days">days</option>
          </select>
        </div>
        <p className={`mt-1 ${mutedText}`}>How long before a booking starts the reminder goes out.</p>
      </div>
      <Field label={PENDING}
        hint="After this long without an answer, the request lapses and the customer is told.">
        <span className="flex flex-wrap items-center gap-2">
          <input className={`${input} w-28`} inputMode="numeric" data-testid="settings-pending-hours"
            value={form.pendingHours} onChange={(event) => change({ pendingHours: event.target.value })} />
          <span className={mutedText} aria-hidden="true">hours</span>
        </span>
      </Field>
      <Field label={LIMIT} hint="Once a customer has this many, the chat makes no more until one is over or cancelled.">
        <input className={`${input} w-28`} inputMode="numeric" data-testid="settings-limit"
          value={form.limit} onChange={(event) => change({ limit: event.target.value })} />
      </Field>
      {(problem || error) && <Notice error testId="settings-error">{problem || error}</Notice>}
      {saved && <Notice testId="settings-notice">Saved.</Notice>}
      <button type="submit" className={action} disabled={busy} data-testid="settings-save">Save settings</button>
    </form>
  );
}

/** The reminder time, how long a request waits, and the customer limit: read by all, changed by an Admin. */
export default function BookingSettingsPanel({ scope, settings }: { scope: BookingsScope; settings: Loaded<BookingSettings> }) {
  let body: ReactNode;
  if (settings.error && !settings.data) body = <Notice error>{settings.error}</Notice>;
  else if (!settings.data) body = <p role="status" className={mutedText}>Loading settings…</p>;
  else if (scope.canEdit) body = <Editor scope={scope} settings={settings} data={settings.data} />;
  else body = <Values settings={settings.data} />;
  return (
    <div data-testid="booking-settings">
      <Panel title="Settings">{body}</Panel>
    </div>
  );
}
