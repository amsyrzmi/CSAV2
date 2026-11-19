"use client";

import { useId, useRef, useState, type ReactNode, type RefObject } from "react";
import {
  addEmailTarget,
  type NotificationTarget,
  type TargetsResponse,
} from "@/lib/notifications-settings-api";
import { EVENT_KEYS, validateEmailAddress } from "@/lib/notifications-settings-view";
import { input, mutedText, useAction, type Loaded } from "../bookings/parts";
import { action, Notice, Panel, secondary } from "../ui";
import type { NotificationsScope } from "./NotificationsWorkspace";
import { EMAIL_OFF } from "./MyNotificationsPanel";
import TargetCard from "./TargetCard";
import { browserZone } from "./zones";

function AddEmail({ scope, emailAvailable, onAdded, headingRef }: {
  scope: NotificationsScope;
  emailAvailable: boolean;
  onAdded: (target: NotificationTarget) => void;
  headingRef: RefObject<HTMLHeadingElement | null>;
}) {
  const [address, setAddress] = useState("");
  const [problem, setProblem] = useState("");
  const [added, setAdded] = useState("");
  const { busy, error, run } = useAction();
  const addressId = useId();

  async function add() {
    setAdded("");
    const typed = address.trim().toLowerCase();
    const found = validateEmailAddress(typed);
    setProblem(found ?? "");
    if (found) return;
    const done = await run(() => addEmailTarget(scope.tenantId, { address: typed }));
    if (!done) return;
    setAddress("");
    setAdded(`Added ${done.address_masked}. We sent it a test email.`);
    onAdded(done);
  }

  return (
    <form className="space-y-3 border-t border-[color:var(--fs-border)] pt-5" data-testid="email-add"
      onSubmit={(event) => { event.preventDefault(); void add(); }}>
      <h3 ref={headingRef} tabIndex={-1} className="text-sm font-semibold outline-none" data-testid="email-add-heading">
        Add an email address
      </h3>
      <div className="text-sm">
        <label htmlFor={addressId}>Email address</label>
        <div className="mt-1 flex flex-wrap gap-2">
          <input id={addressId} type="text" inputMode="email" autoComplete="off" spellCheck={false}
            className={`${input} min-w-48 flex-1`} data-testid="email-add-address" disabled={!emailAvailable}
            value={address} onChange={(event) => { setAddress(event.target.value); setProblem(""); setAdded(""); }} />
          <button type="submit" className={action} disabled={busy || !emailAvailable} data-testid="email-add-submit">
            Add email address
          </button>
        </div>
        <p className={`mt-1 ${mutedText}`}>
          {emailAvailable
            ? "We send a test email first. The address is saved only if it arrives."
            : `${EMAIL_OFF}.`}
        </p>
      </div>
      {(problem || error) && <Notice error testId="email-add-error">{problem || error}</Notice>}
      {added && <Notice testId="email-add-notice">{added}</Notice>}
    </form>
  );
}

/** Where the company's alerts go: its Telegram staff chat and any email addresses. */
export default function TargetsPanel({ scope, targets }: { scope: NotificationsScope; targets: Loaded<TargetsResponse> }) {
  const { data } = targets;
  // A removed card takes the keyboard focus with it: it goes to the next stable spot, the add form.
  const addHeading = useRef<HTMLHeadingElement>(null);
  let body: ReactNode;
  if (data) {
    const rows = data.targets;
    const offered = data.events_offered.length ? data.events_offered : [...EVENT_KEYS];
    const zone = browserZone();
    const put = (next: NotificationTarget[]) => targets.replace({ ...data, targets: next });
    body = (
      <div className="space-y-5">
        {rows.length === 0 ? (
          <p className={mutedText} data-testid="targets-empty">
            No alerts are set up yet.{scope.canEdit ? " Add an email address to start." : ""}
          </p>
        ) : (
          <ul className="space-y-4" data-testid="targets-list">
            {rows.map((target) => (
              <TargetCard key={target.id} scope={scope} target={target} offered={offered}
                emailAvailable={data.email_available} zone={zone} reload={targets.reload}
                onChange={(saved) => put(rows.map((row) => (row.id === saved.id ? saved : row)))}
                onRemoved={(id) => { addHeading.current?.focus(); put(rows.filter((row) => row.id !== id)); }} />
            ))}
          </ul>
        )}
        {scope.canEdit && (
          <AddEmail scope={scope} emailAvailable={data.email_available} headingRef={addHeading}
            onAdded={(created) => put([...rows, created])} />
        )}
      </div>
    );
  } else if (targets.error) {
    body = (
      <div className="space-y-3">
        <Notice error testId="targets-load-error">{targets.error}</Notice>
        <button type="button" className={secondary} onClick={targets.reload}>Try again</button>
      </div>
    );
  } else {
    body = <p role="status" className={mutedText}>Loading where alerts go…</p>;
  }
  return (
    <div data-testid="notifications-targets">
      <Panel title="Where alerts go">
        <p className={`mb-4 max-w-prose ${mutedText}`}>
          Alerts about customers who need a person go to the places below.
          {!scope.canEdit && " Only an Admin can change them."}
        </p>
        {body}
      </Panel>
    </div>
  );
}
