"use client";

import { useCallback, useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from "react";
import { api, ApiError } from "@/lib/api";

/**
 * One list of the page. `data` is null until the first read succeeds; a later failure keeps
 * the rows already on screen and only sets `error`, so a failed read is never shown as an
 * empty list and never blanks the page.
 */
export type Slot<T> = { data: T | null; error: string; code: string; loading: boolean };

async function read<T>(
  url: string,
  running: MutableRefObject<AbortController | null>,
  update: Dispatch<SetStateAction<Slot<T>>>,
): Promise<void> {
  running.current?.abort();
  const controller = new AbortController();
  running.current = controller;
  try {
    const data = await api<T>(url, { signal: controller.signal });
    if (!controller.signal.aborted) update({ data, error: "", code: "", loading: false });
  } catch (cause) {
    if (controller.signal.aborted) return;
    const code = cause instanceof ApiError ? cause.code : "";
    update((previous) => ({ data: previous.data, error: (cause as Error).message, code, loading: false }));
  }
}

/** Loads one `/console/v1/<resource>` list on its own: its failure never touches another list. */
export function useKbSlot<T>(resource: string, query: string): { slot: Slot<T>; load: () => Promise<void> } {
  const [slot, setSlot] = useState<Slot<T>>({ data: null, error: "", code: "", loading: true });
  const running = useRef<AbortController | null>(null);
  const url = `/console/v1/${resource}?${query}`;
  const load = useCallback(() => read<T>(url, running, setSlot), [url]);
  useEffect(() => {
    void load();
    const current = running;
    return () => current.current?.abort();
  }, [load]);
  return { slot, load };
}
