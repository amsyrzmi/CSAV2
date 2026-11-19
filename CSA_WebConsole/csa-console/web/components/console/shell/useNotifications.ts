"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, post } from "@/lib/api";
import {
  applyRead,
  isUnread,
  unreadAfter,
  type NotificationItem,
} from "@/lib/notifications-view";

export type NotificationsState = "idle" | "loading" | "ready" | "failed";

export const MARK_FAILED = "Could not mark as read. Try again.";
export const ALL_MARKED = "All marked as read.";
/** The most ids the server takes in one mark-read call. */
const IDS_MAX = 200;

/** The rows of an answer that look like notifications; a malformed row is dropped, not shown. */
function readPage(body: unknown): { items: NotificationItem[]; unread: number } | null {
  if (typeof body !== "object" || body === null) return null;
  const { items, unread } = body as Record<string, unknown>;
  if (!Array.isArray(items) || typeof unread !== "number" || !Number.isFinite(unread)) return null;
  const usable = items.filter(
    (item): item is NotificationItem =>
      typeof item === "object" &&
      item !== null &&
      typeof (item as Record<string, unknown>).id === "string" &&
      typeof (item as Record<string, unknown>).created_at === "string",
  );
  return { items: usable, unread: Math.max(0, Math.floor(unread)) };
}

/**
 * The bell panel's list. `load()` reads it (the bell calls it as the panel opens), and it is read
 * again, quietly, when the shell's polled `count` rises while `enabled` (the panel is open); it
 * is never polled on its own. Marking is
 * optimistic: the rows and the unread number change at once and are put back if the call fails.
 * After a successful mark `onMarked` runs (the shell re-reads its count, so the badge and the
 * panel agree).
 */
export function useNotifications({
  enabled,
  count,
  onMarked,
}: {
  enabled: boolean;
  count: number | null;
  onMarked: () => void;
}) {
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [unread, setUnread] = useState(0);
  const [state, setState] = useState<NotificationsState>("idle");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loadedAt, setLoadedAt] = useState(0);
  const latest = useRef({ items, unread });
  const controller = useRef<AbortController | null>(null);
  const previousCount = useRef<number | null>(null);

  useEffect(() => {
    latest.current = { items, unread };
  }, [items, unread]);

  // `quiet` is a refresh of rows already on screen: they stay, and a failure says nothing.
  const read = useCallback(async (quiet: boolean) => {
    controller.current?.abort();
    const mine = new AbortController();
    controller.current = mine;
    if (!quiet) {
      setState("loading");
      setError("");
      setNotice("");
    }
    try {
      const page = readPage(
        await api<unknown>("/console/v1/notifications?limit=50", { signal: mine.signal }),
      );
      if (mine.signal.aborted) return;
      if (page === null) throw new Error("unexpected answer");
      setItems(page.items);
      setUnread(page.unread);
      setLoadedAt(Date.now());
      setState("ready");
    } catch {
      if (mine.signal.aborted) return;
      if (!quiet) setState("failed");
    }
  }, []);

  const load = useCallback(() => read(false), [read]);

  // Closing drops a read still in flight; opening starts one through `load` (the bell's click).
  useEffect(() => {
    if (!enabled) controller.current?.abort();
  }, [enabled]);

  // A rise while the panel is open (never a first answer, never a fall) reads the list again.
  useEffect(() => {
    const before = previousCount.current;
    previousCount.current = count;
    if (enabled && typeof before === "number" && typeof count === "number" && count > before) {
      void read(true);
    }
  }, [count, enabled, read]);

  useEffect(() => () => controller.current?.abort(), []);

  const mark = useCallback(
    async (target: string[] | "all"): Promise<boolean> => {
      const { items: before, unread: beforeUnread } = latest.current;
      const wanted = target === "all" ? null : new Set(target);
      const changed = new Map(
        before
          .filter((item) => isUnread(item) && (wanted === null || wanted.has(item.id)))
          .map((item) => [item.id, item]),
      );
      // The server takes at most IDS_MAX ids a call; a longer list goes in several calls, and
      // only the rows of a call that failed are put back.
      const chunks: Array<string[] | "all"> = [];
      if (target === "all") chunks.push("all");
      else for (let at = 0; at < target.length; at += IDS_MAX) chunks.push(target.slice(at, at + IDS_MAX));
      setError("");
      setNotice("");
      setItems(applyRead(before, target, new Date().toISOString()));
      setUnread(unreadAfter(beforeUnread, before, target));
      const done = await Promise.all(
        chunks.map((chunk) =>
          post("/console/v1/notifications/read", chunk === "all" ? { all: true } : { ids: chunk }).then(
            () => true,
            () => false,
          ),
        ),
      );
      const failed = chunks.filter((_, index) => !done[index]);
      if (failed.length > 0) {
        const restore = new Map<string, NotificationItem>();
        let restored = 0;
        for (const chunk of failed) {
          // What this call took off the unread number, added back (other marks may have run since).
          restored += beforeUnread - unreadAfter(beforeUnread, before, chunk);
          for (const id of chunk === "all" ? changed.keys() : chunk) {
            const row = changed.get(id);
            if (row) restore.set(id, row);
          }
        }
        setItems((now) => now.map((item) => restore.get(item.id) ?? item));
        setUnread((now) => now + restored);
        setError(MARK_FAILED);
      }
      if (failed.length < chunks.length) onMarked();
      return failed.length === 0;
    },
    [onMarked],
  );

  const markRead = useCallback((ids: string[]) => void mark(ids), [mark]);
  const markAll = useCallback(async () => {
    if (await mark("all")) setNotice(ALL_MARKED);
  }, [mark]);

  return { items, unread, state, load, markRead, markAll, error, notice, loadedAt };
}
