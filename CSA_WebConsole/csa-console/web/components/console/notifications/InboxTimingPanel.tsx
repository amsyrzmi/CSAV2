"use client";

import { useState, type ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { settingsBody, type InboxSettings, type InboxSettingsValues } from "@/lib/inbox-api";
import { saveInboxSettings } from "@/lib/notifications-settings-api";
import { timingFields, validateTiming, type TimingField, type TimingKey } from "@/lib/notifications-settings-view";
import { input, mutedText, useAction, type Loaded } from "../bookings/parts";
import { action, Notice, Panel, secondary } from "../ui";
import type { NotificationsScope } from "./NotificationsWorkspace";

type Draft = Record<TimingKey, string>;

const draftOf = (settings: InboxSettings): Draft =>
  Object.fromEntries(timingFields.map((item) => [item.key, String(settings[item.key])])) as Draft;

const unitWord = (item: TimingField) => (item.unit === "count" ? "" : item.unit);
const ZERO_OFF = "0 turns it off";

function valueText(item: TimingField, value: number): string {
  if (item.zeroOff && value === 0) return "Off";
  return item.unit === "count" ? String(value) : `${value} ${item.unit}`;
}

function Values({ settings }: { settings: InboxSettings }) {
  return (
    <dl data-testid="timing-values" className="space-y-2">
      {timingFields.map((item) => (
        <div key={item.key} className="grid gap-0.5 sm:grid-cols-[minmax(0,1fr)_10rem] sm:gap-3">
          <dt className="text-sm text-[color:var(--fs-text-secondary)]">{item.label}</dt>
          <dd className="min-w-0 text-sm [overflow-wrap:anywhere]" data-testid={`timing-value-${item.key}`}>
            {valueText(item, settings[item.key])}
          </dd>
        </div>
      ))}
    </dl>
  );
}

/** An Admin's form. It follows the saved settings again when they were read anew after a clash. */
function Editor({ scope, timing, data }: { scope: NotificationsScope; timing: Loaded<InboxSettings>; data: InboxSettings }) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(data));
  const [seen, setSeen] = useState(data.version);
  const [errors, setErrors] = useState<Partial<Record<TimingKey, string>>>({});
  const [saved, setSaved] = useState(false);
  const { busy, error, run } = useAction();
  if (seen !== data.version) {
    setSeen(data.version);
    setDraft(draftOf(data));
    setErrors({});
  }

  async function save() {
    setSaved(false);
    const found = validateTiming(draft);
    setErrors(found);
    if (Object.keys(found).length > 0) return;
    const values = Object.fromEntries(timingFields.map((item) => [item.key, Number(draft[item.key].trim())])) as InboxSettingsValues;
    const done = await run(async () => {
      try {
        return await saveInboxSettings(scope.tenantId, settingsBody(values, data.version));
      } catch (cause) {
        // Someone saved first: read what is saved now, so the next try is made on it.
        if (cause instanceof ApiError && cause.status === 409) timing.reload();
        throw cause;
      }
    });
    if (!done) return;
    setSeen(done.version);
    setDraft(draftOf(done));
    timing.replace(done);
    setSaved(true);
  }

  return (
    <form className="space-y-4" data-testid="timing-form" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <div className="grid gap-x-6 gap-y-4 md:grid-cols-2">
        {timingFields.map((item) => {
          const problem = errors[item.key];
          const hintId = `timing-hint-${item.key}`;
          const unitId = `timing-unit-${item.key}`;
          // The unit ("minutes") is read as part of the field's description, then any hint or error.
          const describedBy = [unitWord(item) && unitId, (problem || item.zeroOff) && hintId].filter(Boolean).join(" ");
          return (
            <div key={item.key} className="min-w-0 text-sm">
              <label htmlFor={`timing-${item.key}`}>{item.label}</label>
              <span className="mt-1 flex flex-wrap items-center gap-2">
                <input id={`timing-${item.key}`} className={`${input} w-28`} inputMode="numeric" data-testid={`timing-${item.key}`}
                  value={draft[item.key]} aria-invalid={problem ? true : undefined}
                  aria-describedby={describedBy || undefined}
                  onChange={(event) => { setDraft({ ...draft, [item.key]: event.target.value }); setSaved(false); }} />
                {unitWord(item) && <span id={unitId} className={mutedText} data-testid={unitId}>{unitWord(item)}</span>}
              </span>
              {(problem || item.zeroOff) && (
                <span id={hintId} data-testid={problem ? `timing-error-${item.key}` : undefined}
                  className={`mt-1 block [overflow-wrap:anywhere] ${problem ? "text-[color:var(--fs-danger)]" : mutedText}`}>
                  {problem ?? ZERO_OFF}
                </span>
              )}
            </div>
          );
        })}
      </div>
      {error && <Notice error testId="timing-error">{error}</Notice>}
      {saved && <Notice testId="timing-notice">Saved.</Notice>}
      <button type="submit" className={action} disabled={busy} data-testid="timing-save">Save timing</button>
    </form>
  );
}

/** How fast the Inbox expects a reply and when it reminds: read by everyone, changed by an Admin. */
export default function InboxTimingPanel({ scope, timing }: { scope: NotificationsScope; timing: Loaded<InboxSettings> }) {
  let body: ReactNode;
  if (timing.data) {
    body = scope.canEdit ? <Editor scope={scope} timing={timing} data={timing.data} /> : <Values settings={timing.data} />;
  } else if (timing.error) {
    body = (
      <div className="space-y-3">
        <Notice error testId="timing-load-error">{timing.error}</Notice>
        <button type="button" className={secondary} onClick={timing.reload}>Try again</button>
      </div>
    );
  } else {
    body = <p role="status" className={mutedText}>Loading Inbox timing…</p>;
  }
  return (
    <div data-testid="notifications-timing">
      <Panel title="Inbox timing">{body}</Panel>
    </div>
  );
}
