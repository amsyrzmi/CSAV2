"use client";
import { useCallback, useRef, useState } from "react";
import type { hrClient } from "@/lib/hr-api";

const messageOf = (cause: unknown) => cause instanceof Error ? cause.message : "HR request failed";

/** One HR action at a time: a second click while one is in flight is ignored. */
export function useHrRunner() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const flight = useRef(false);
  const run = useCallback(async (task: () => Promise<void>) => {
    if (flight.current) return;
    flight.current = true; setBusy(true); setError(""); setMessage("");
    try { await task(); } catch (cause) { setError(messageOf(cause)); }
    finally { flight.current = false; setBusy(false); }
  }, []);
  return { busy, error, message, setMessage, setError, run };
}
export type HrRunner = ReturnType<typeof useHrRunner>;

/**
 * The moved tools' save: one HR operation, its receipt as the message, then the context and the
 * tab's records reloaded. A failed reload says the write itself was saved.
 */
export function useHrSave(runner: HrRunner, client: ReturnType<typeof hrClient>, reload: () => Promise<void>,
  refreshContext: () => Promise<void>) {
  const { run, setMessage, setError } = runner;
  return useCallback((body: unknown, after?: () => void) => run(async () => {
    const receipt = await client.operation(body);
    after?.(); setMessage(`${receipt.status.replaceAll("_", " ")} · reference ${receipt.resource_id}`);
    try { await refreshContext(); await reload(); } catch (cause) { setError(`Saved; refreshing failed: ${messageOf(cause)}`); }
  }), [run, setMessage, setError, client, reload, refreshContext]);
}
