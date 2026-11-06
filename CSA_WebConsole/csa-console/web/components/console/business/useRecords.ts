"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { withCursor } from "@/lib/business";
import { mergeRows, parseOrderCounts, type OrderCounts } from "@/lib/business-view";

export type Records<T> = {
  source?: string;
  /** "connected" or "not_connected" on the order, product and store reads; the listings read has none. */
  marketplace_sync?: string;
  items: T[];
  next_cursor: string | null;
};

/**
 * One keyset-paged read. "Older" adds the next page to the rows already shown (a row never
 * appears twice); "Refresh newest" starts again from the first page and swaps the rows when
 * the answer arrives, so the screen never flashes empty. A failed page keeps what is shown.
 * A changed path starts a clean read; a slower answer for the old path is aborted and ignored.
 */
export function useRecords<T extends { id: string }>(path: string) {
  const [cursor, setCursor] = useState<string | null>(null);
  const [items, setItems] = useState<T[] | null>(null);
  const [next, setNext] = useState<string | null>(null);
  const [sync, setSync] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  // A new path is a new read: start clean in the same render (nothing of the old path shows).
  const [seenPath, setSeenPath] = useState(path);
  if (seenPath !== path) {
    setSeenPath(path);
    setCursor(null);
    setItems(null);
    setNext(null);
    setError("");
    setLoading(true);
  }
  useEffect(() => {
    const controller = new AbortController();
    api<Records<T>>(withCursor(path, cursor), {
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]),
    }).then((result) => {
      if (controller.signal.aborted) return;
      setItems((current) => (cursor && current ? mergeRows(current, result.items) : result.items));
      setNext(result.next_cursor);
      setSync(result.marketplace_sync ?? null);
    }).catch((cause: Error) => {
      if (!controller.signal.aborted) setError(cause.message);
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [path, cursor, revision]);
  return {
    items, next, error, loading,
    /** What the last read said about TikTok Shop being connected; null before it or when the read has no such field. */
    marketplaceSync: sync,
    /** True while an "Older" page is loading (not the first page or a refresh). */
    loadingMore: loading && cursor !== null,
    older: () => {
      if (!next) return;
      setLoading(true);
      setError("");
      setCursor(next);
      // After a failed page the cursor already equals `next`: the revision forces the read again.
      setRevision((value) => value + 1);
    },
    newest: () => {
      setLoading(true);
      setError("");
      setCursor(null);
      setRevision((value) => value + 1);
    },
  };
}

type CountsState = { path: string; counts: OrderCounts | null };

/**
 * The per-status counts for the order filters. It runs beside the list and is aborted when
 * the path changes. `failed` is true only after a read that failed or came back malformed:
 * until then (and for a path nobody has read yet) there are simply no figures, never zeros.
 * `revision` re-reads the same path; the old figures stay until the new answer replaces them.
 */
export function useOrderCounts(path: string, revision: number) {
  const [state, setState] = useState<CountsState | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const done = (counts: OrderCounts | null) => {
      if (!controller.signal.aborted) setState({ path, counts });
    };
    // An unwindowed read can be slow on a busy shop, so it gets longer than a list page.
    api<unknown>(path, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(30_000)]) })
      .then((raw) => done(parseOrderCounts(raw)))
      .catch(() => done(null));
    return () => controller.abort();
  }, [path, revision]);
  const current = state && state.path === path ? state : null;
  return { counts: current?.counts ?? null, failed: current !== null && current.counts === null };
}
