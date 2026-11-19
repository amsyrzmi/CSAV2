"use client";

import { useId, useState, type ReactNode } from "react";
import { getPreferences, savePreferences, type NotificationPriority, type Preferences } from "@/lib/notifications-settings-api";
import { KIND_KEYS, kindLabel, priorityLabel } from "@/lib/notifications-settings-view";
import { Check, input, mutedText, useAction, useLoaded, type Loaded } from "../bookings/parts";
import { action, Notice, Panel, secondary } from "../ui";

export const PRIORITIES: NotificationPriority[] = ["low", "normal", "high", "urgent"];
export const EMAIL_OFF = "Email is not set up on this deployment";

function Editor({ data, replace, emailAvailable }: {
  data: Preferences;
  replace: (value: Preferences) => void;
  emailAvailable: boolean | null;
}) {
  const [enabled, setEnabled] = useState(data.email_enabled);
  const [kinds, setKinds] = useState<string[]>(data.kinds);
  const [floor, setFloor] = useState<NotificationPriority>(data.min_priority);
  const [saved, setSaved] = useState(false);
  const { busy, error, run } = useAction();
  const floorId = useId();
  // Email can be turned off whenever it is on; it can be turned on only while the relay is. The
  // switch is locked by what is saved, not by the box: unchecking and re-checking works.
  const noRelay = emailAvailable === false && !data.email_enabled;
  const touch = () => setSaved(false);

  async function save() {
    setSaved(false);
    const done = await run(() =>
      savePreferences(null, {
        email_enabled: enabled,
        kinds: KIND_KEYS.filter((kind) => kinds.includes(kind)),
        min_priority: floor,
      }),
    );
    if (!done) return;
    replace(done);
    setSaved(true);
  }

  return (
    <form className="space-y-4" data-testid="my-form" onSubmit={(event) => { event.preventDefault(); void save(); }}>
      <div>
        <label className="flex min-h-10 items-start gap-2 py-1 text-sm pointer-coarse:min-h-11">
          <input type="checkbox" role="switch" data-testid="my-email" checked={enabled} disabled={noRelay}
            onChange={(event) => { setEnabled(event.target.checked); touch(); }}
            className="mt-0.5 size-4 shrink-0 accent-[var(--fs-primary)]" />
          <span>Email me about these</span>
        </label>
        {noRelay && <p className={`mt-1 ${mutedText}`} data-testid="my-email-off">{EMAIL_OFF}.</p>}
        <p className={`mt-1 ${mutedText}`}>Emails name the customer. They never contain the customer&apos;s message.</p>
      </div>
      <fieldset className="min-w-0">
        <legend className="text-sm">Tell me when</legend>
        <div className="mt-1 grid gap-x-4 sm:grid-cols-2">
          {KIND_KEYS.map((kind) => (
            <Check key={kind} testId={`my-kind-${kind}`} checked={kinds.includes(kind)}
              onChange={(on) => { setKinds(on ? [...kinds, kind] : kinds.filter((item) => item !== kind)); touch(); }}>
              {kindLabel(kind)}
            </Check>
          ))}
        </div>
      </fieldset>
      <div className="text-sm">
        <label htmlFor={floorId}>Only email me for this priority and above</label>
        <select id={floorId} className={`${input} mt-1 w-40`} data-testid="my-floor" value={floor}
          onChange={(event) => { setFloor(event.target.value as NotificationPriority); touch(); }}>
          {PRIORITIES.map((priority) => <option key={priority} value={priority}>{priorityLabel(priority)}</option>)}
        </select>
      </div>
      {error && <Notice error testId="my-error">{error}</Notice>}
      {saved && <Notice testId="my-notice">Saved.</Notice>}
      <button type="submit" className={action} disabled={busy} data-testid="my-save">Save my notifications</button>
    </form>
  );
}

/** One person's own email choices. Every member may change their own, whatever their role. */
export default function MyNotificationsPanel({ emailAvailable }: { emailAvailable: boolean | null }) {
  const preferences: Loaded<Preferences> = useLoaded(() => getPreferences(null));
  let body: ReactNode;
  if (preferences.data) {
    body = <Editor data={preferences.data} replace={preferences.replace} emailAvailable={emailAvailable} />;
  } else if (preferences.error) {
    body = (
      <div className="space-y-3">
        <Notice error testId="my-load-error">{preferences.error}</Notice>
        <button type="button" className={secondary} onClick={preferences.reload}>Try again</button>
      </div>
    );
  } else {
    body = <p role="status" className={mutedText}>Loading your notifications…</p>;
  }
  return (
    <div data-testid="notifications-my">
      <Panel title="My notifications">{body}</Panel>
    </div>
  );
}
