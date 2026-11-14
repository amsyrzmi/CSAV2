"use client";

import { useEffect, useState, type ReactNode } from "react";
import { ApiError } from "@/lib/api";
import { listItems, type TrainingKind, type TrainingList } from "@/lib/training-api";
import { refusalCopy } from "@/lib/training-view";

/** How often a manager's list is read again while a video is still being made. */
const POLL_MS = 5000;

export const muted = "text-sm text-[color:var(--fs-text-secondary)] [overflow-wrap:anywhere]";
export const dangerText = "text-sm text-[color:var(--fs-danger)]";

/** The server's code for a failure; anything that is not an `ApiError` (the network) has none. */
export function codeOf(cause: unknown): string {
  return cause instanceof ApiError ? cause.code : "";
}

/** The sentence staff see for a failure. The code stays on the error for support. */
export function refusalText(cause: unknown, kind: TrainingKind = "pdf"): string {
  return refusalCopy(codeOf(cause), kind);
}

export interface ListRead {
  data: TrainingList | null;
  /** The failure's code while the last read failed (the data, if any, is from before). */
  error: string | null;
  reload: () => void;
}

/**
 * The company's library, read once and again on `reload`. A failed read keeps the list it had,
 * so a refresh after a change never empties the page.
 */
export function useTrainingList(tenantId: string | null): ListRead {
  const [data, setData] = useState<TrainingList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [round, setRound] = useState(0);
  useEffect(() => {
    let live = true;
    listItems(tenantId).then(
      (list) => {
        if (!live) return;
        setData(list);
        setError(null);
      },
      (cause: unknown) => {
        if (live) setError(codeOf(cause));
      },
    );
    return () => {
      live = false;
    };
  }, [tenantId, round]);
  // Managers watch a video go through its states: read again every 5 s until each is ready or failed.
  const unfinished =
    !!data?.can_manage && data.items.some((row) => row.kind === "video" && row.status !== "ready" && row.status !== "failed");
  useEffect(() => {
    if (!unfinished) return;
    const timer = window.setInterval(() => setRound((value) => value + 1), POLL_MS);
    return () => window.clearInterval(timer);
  }, [unfinished]);
  return { data, error, reload: () => setRound((value) => value + 1) };
}

/** A label above one control, with its hint and its problem tied to it for screen readers. */
export function FormRow({
  id,
  label,
  hint,
  problem,
  children,
}: {
  id: string;
  label: string;
  hint?: ReactNode;
  problem?: string;
  children: ReactNode;
}) {
  return (
    <div className="min-w-0 text-sm">
      <label htmlFor={id} className="block">{label}</label>
      <div className="mt-1">{children}</div>
      {hint ? <p id={`${id}-hint`} className={`mt-1 ${muted}`}>{hint}</p> : null}
      {problem ? <p id={`${id}-problem`} className={`mt-1 ${dangerText}`}>{problem}</p> : null}
    </div>
  );
}

/** The `aria-describedby` and `aria-invalid` for a control in a `FormRow`. */
export function described(id: string, hint: boolean, problem?: string) {
  const ids = [hint ? `${id}-hint` : "", problem ? `${id}-problem` : ""].filter(Boolean).join(" ");
  return { id, "aria-describedby": ids || undefined, "aria-invalid": problem ? true : undefined };
}
