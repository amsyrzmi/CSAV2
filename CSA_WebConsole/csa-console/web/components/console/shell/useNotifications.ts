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
      setError("");
      setNotice("");
      setItems(applyRead(before, target, new Date().toISOString()));
      setUnread(unreadAfter(beforeUnread, before, target));
      try {
        await post("/console/v1/notifications/read", target === "all" ? { all: true } : { ids: target });
      } catch {
        setItems((now) => now.map((item) => changed.get(item.id) ?? item));
        setUnread(beforeUnread);
        setError(MARK_FAILED);
        return false;
      }
      onMarked();
      return true;
    },
    [onMarked],
  );

  const markRead = useCallback((ids: string[]) => void mark(ids), [mark]);
  const markAll = useCallback(async () => {
    if (await mark("all")) setNotice(ALL_MARKED);
  }, [mark]);

  return { items, unread, state, load, markRead, markAll, error, notice, loadedAt };
}
