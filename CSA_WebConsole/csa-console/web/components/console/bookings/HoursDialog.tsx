"use client";

import { useState } from "react";
import { getHours, replaceHours, type BookingHoursOut } from "@/lib/bookings-api";
import { daysToPeriods, hoursLines, hoursProblem, periodsToDays, type HoursDays } from "@/lib/bookings-view";
import Dialog from "../fs/Dialog";
import { action, Notice, secondary } from "../ui";
import HoursEditor from "./HoursEditor";
import { mutedText, useAction, useLoaded, type BookingsScope } from "./parts";

export type HoursSubject = { kind: "company" } | { kind: "person" | "resource"; id: string; name: string };

/** Everyone in the company can read the hours; an Admin can change them. */
export default function HoursDialog({
  scope,
  subject,
  companyDays,
  onClose,
  onSaved,
}: {
  scope: BookingsScope;
  subject: HoursSubject;
  /** The company's week, to start a person's or room's own hours from. */
  companyDays: HoursDays | null;
  onClose: () => void;
  onSaved?: (hours: BookingHoursOut) => void;
}) {
  const title = subject.kind === "company" ? "Opening hours" : `Working hours: ${subject.name}`;
  const hours = useLoaded(() =>
    getHours(scope.tenantId, subject.kind === "company" ? undefined : { kind: subject.kind, id: subject.id }),
  );
  return (
    <Dialog open onClose={onClose} title={title} testId="hours-dialog" widthClass="max-w-xl">
      {hours.error ? (
        <Notice error>{hours.error}</Notice>
      ) : !hours.data ? (
        <p role="status" className={mutedText}>Loading hours…</p>
      ) : (
        <HoursBody scope={scope} subject={subject} hours={hours.data} companyDays={companyDays}
          onClose={onClose} onSaved={onSaved} />
      )}
    </Dialog>
  );
}

function HoursBody({
  scope,
  subject,
  hours,
  companyDays,
  onClose,
  onSaved,
}: {
  scope: BookingsScope;
  subject: HoursSubject;
  hours: BookingHoursOut;
  companyDays: HoursDays | null;
  onClose: () => void;
  onSaved?: (hours: BookingHoursOut) => void;
}) {
  const own = subject.kind !== "company";
  const [follows, setFollows] = useState(own && hours.periods.length === 0);
  const [days, setDays] = useState<HoursDays>(() =>
    hours.periods.length || !own
      ? periodsToDays(hours.periods)
      : (companyDays ?? periodsToDays([])).map((rows) => rows.map((row) => ({ ...row }))),
  );
  const [problem, setProblem] = useState("");
  const { busy, error, run } = useAction();

  async function save(periods: HoursDays | null) {
    const found = periods ? hoursProblem(periods) : null;
    setProblem(found ?? "");
    if (found) return;
    const body = {
      subject_kind: subject.kind,
      subject_id: subject.kind === "company" ? null : subject.id,
      periods: periods ? daysToPeriods(periods) : [],
    };
    const saved = await run(() => replaceHours(scope.tenantId, body));
    if (saved) {
      onSaved?.(saved);
      onClose();
    }
  }

  return (
    <div className="space-y-4">
      <div data-testid="hours-current">
        <p className="text-sm font-medium">Now</p>
        {hoursLines(hours).map((line) => (
          <p key={line} className="text-sm [overflow-wrap:anywhere]">{line}</p>
        ))}
      </div>
      {scope.canEdit && (
        <>
          {(problem || error) && <Notice error testId="hours-error">{problem || error}</Notice>}
          {own && (
            <fieldset className="space-y-1" disabled={busy}>
              <legend className="text-sm font-medium">Hours</legend>
              <label className="flex min-h-10 items-center gap-2 text-sm">
                <input type="radio" name="hours-mode" data-testid="hours-same" checked={follows}
                  onChange={() => setFollows(true)} className="size-4 accent-[var(--fs-primary)]" />
                Same as the company
              </label>
              <label className="flex min-h-10 items-center gap-2 text-sm">
                <input type="radio" name="hours-mode" data-testid="hours-own" checked={!follows}
                  onChange={() => setFollows(false)} className="size-4 accent-[var(--fs-primary)]" />
                Their own hours
              </label>
            </fieldset>
          )}
          {!(own && follows) && (
            <fieldset disabled={busy}>
              <HoursEditor days={days} onChange={setDays} zone={scope.zone} />
            </fieldset>
          )}
          <div className="flex flex-wrap gap-2">
            <button type="button" className={action} disabled={busy} data-testid="hours-save"
              onClick={() => void save(own && follows ? null : days)}>
              Save hours
            </button>
            {!own && !hours.is_default && (
              <button type="button" className={secondary} disabled={busy} data-testid="hours-default"
                onClick={() => void save(null)}>
                Go back to the default week
              </button>
            )}
            <button type="button" className={secondary} disabled={busy} onClick={onClose}>Cancel</button>
          </div>
        </>
      )}
    </div>
  );
}
