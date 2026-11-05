"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { field, Notice } from "../ui";

/** Reading the company's time zone: still going, done, or failed (so nothing may be shown in a guess). */
export type ZoneState = "loading" | "loaded" | "failed";

/** What every Bookings page needs to know about who is looking and which company it is. */
export interface BookingsScope {
  /** Null for a tenant member (the session names the tenant); the chosen company for a platform admin. */
  tenantId: string | null;
  /** An Admin of the company, or a platform admin: the only people who change the setup. */
  canEdit: boolean;
  /** An Admin, a platform admin or staff: who may make bookings. Read-only members may not. */
  canBook: boolean;
  /** The company's IANA time zone; set only once it has been read and is valid. */
  zone: string | null;
  zoneState: ZoneState;
  /** `client_id=…` for a platform admin's links, else empty. */
  query: string;
}

/**
 * Shows its content once the company's time zone is known. While it is being read there is only a
 * quiet loading line; if it could not be read there is the message and nothing else, because times
 * must never be shown or accepted in the browser's own zone.
 */
export function ZoneGate({ scope, children }: { scope: BookingsScope; children: (zone: string) => ReactNode }) {
  if (scope.zone) return <>{children(scope.zone)}</>;
  if (scope.zoneState === "loading") return <p role="status" className={mutedText}>Loading…</p>;
  return (
    <Notice warning testId="zone-failed">
      We could not read your company&apos;s time zone, so times can&apos;t be shown or entered yet.
      Reload the page to try again.
    </Notice>
  );
}

export const cardClass =
  "rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border)] bg-[var(--fs-surface)] p-3";
export const mutedText = "text-sm text-[color:var(--fs-text-secondary)] [overflow-wrap:anywhere]";

export function errorText(cause: unknown): string {
  return cause instanceof Error ? cause.message : "The request failed. Try again.";
}

/**
 * Loads one thing and keeps it. `reload` fetches again; `replace` swaps in a value the page
 * already holds (the answer to a change). A failed read shows its sentence, never an empty list.
 */
export interface Loaded<T> {
  data: T | null;
  error: string;
  reload: () => void;
  replace: (value: T | null) => void;
}

export function useLoaded<T>(load: () => Promise<T>): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [round, setRound] = useState(0);
  const loader = useRef(load);
  useEffect(() => {
    loader.current = load;
  });
  useEffect(() => {
    let live = true;
    loader.current().then(
      (value) => {
        if (!live) return;
        setData(value);
        setError("");
      },
      (cause: unknown) => {
        if (live) setError(errorText(cause));
      },
    );
    return () => {
      live = false;
    };
  }, [round]);
  return { data, error, reload: () => setRound((value) => value + 1), replace: setData };
}

/** A label above one control. */
export function Field({
  label,
  hint,
  children,
  className = "",
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <label className={`block text-sm ${className}`}>
      {label}
      <span className="mt-1 block">{children}</span>
      {hint ? <span className={`mt-1 block ${mutedText}`}>{hint}</span> : null}
    </label>
  );
}

export const input = `${field} min-w-0`;

/** A checkbox with text beside it, at a comfortable touch size. */
export function Check({
  checked,
  onChange,
  children,
  testId,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <label className="flex min-h-10 items-start gap-2 py-1 text-sm pointer-coarse:min-h-11">
      <input
        type="checkbox"
        data-testid={testId}
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 size-4 shrink-0 accent-[var(--fs-primary)]"
      />
      <span className="min-w-0 [overflow-wrap:anywhere]">{children}</span>
    </label>
  );
}

/** Runs `task` once at a time and keeps its sentence when it fails. */
export function useAction() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const running = useRef(false);
  async function run<T>(task: () => Promise<T>): Promise<T | undefined> {
    if (running.current) return undefined;
    running.current = true;
    setBusy(true);
    setError("");
    try {
      return await task();
    } catch (cause) {
      setError(errorText(cause));
      return undefined;
    } finally {
      running.current = false;
      setBusy(false);
    }
  }
  return { busy, error, setError, run };
}
