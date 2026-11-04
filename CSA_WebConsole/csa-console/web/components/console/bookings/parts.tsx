"use client";

import { useEffect, useRef, useState, type ComponentProps, type ReactNode } from "react";
import Dialog from "../fs/Dialog";
import { field } from "../ui";

/**
 * A dialog that is open for as long as it is mounted. The shared Dialog closes and reopens its
 * native <dialog> when effects run twice in development, and the late `close` event of that
 * first close would shut a dialog that mounted already open. This one mounts closed and opens a
 * frame later, which is how the shared Dialog is used everywhere else (toggled, not mounted open).
 */
export function OpenDialog(props: Omit<ComponentProps<typeof Dialog>, "open">) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const frame = requestAnimationFrame(() => setOpen(true));
    return () => cancelAnimationFrame(frame);
  }, []);
  return <Dialog {...props} open={open} />;
}

/** What every Bookings page needs to know about who is looking and which company it is. */
export interface BookingsScope {
  /** Null for a tenant member (the session names the tenant); the chosen company for a platform admin. */
  tenantId: string | null;
  /** An Admin of the company, or a platform admin: the only people who change the setup. */
  canEdit: boolean;
  /** The company's IANA time zone, or null while it is unknown. */
  zone: string | null;
  /** `client_id=…` for a platform admin's links, else empty. */
  query: string;
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
