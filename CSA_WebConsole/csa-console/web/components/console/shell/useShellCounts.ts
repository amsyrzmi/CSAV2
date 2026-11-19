"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Me } from "@/lib/identity-contracts";
import {
  nextCount,
  parseNeedsUs,
  parseUnread,
  wantsShellCounts,
  type ShellCounts,
} from "@/lib/shell-counts";

const REFRESH_MS = 60_000;

/**
 * The shell's one poll, called once in AppShell: the unread notification count (every company
 * member) and, when the navigation holds an Inbox link, the Needs us count. Both are read on
 * mount, every minute while the tab is visible, when the window regains focus and whenever the
 * route changes (`path`). A read that fails or answers something unexpected keeps the previous
 * number and says nothing; a platform admin makes no call at all. `null` means unknown.
 */
export function useShellCounts({
  role,
  hasInboxLink,
  path,
}: {
  role: Me["role"] | null;
  hasInboxLink: boolean;
  path: string;
}): ShellCounts {
  const [counts, setCounts] = useState<ShellCounts>({ unread: null, needsUs: null });
  const wants = wantsShellCounts(role, hasInboxLink);
  const { notifications, queue } = wants;

  useEffect(() => {
    if (!notifications && !queue) return;
    let controller: AbortController | null = null;

    async function read(url: string, signal: AbortSignal): Promise<unknown> {
      try {
        return await api<unknown>(url, { signal });
      } catch {
        return null; // silent: the previous answer stays
      }
    }

    async function load() {
      controller?.abort();
      const mine = new AbortController();
      controller = mine;
      const [unread, needsUs] = await Promise.all([
        notifications ? read("/console/v1/notifications/count", mine.signal) : null,
        queue ? read("/console/v1/inbox/queue/counts", mine.signal) : null,
      ]);
      if (mine.signal.aborted) return;
      setCounts((before) => ({
        unread: notifications ? nextCount(before.unread, parseUnread(unread)) : before.unread,
        needsUs: queue ? nextCount(before.needsUs, parseNeedsUs(needsUs)) : before.needsUs,
      }));
    }

    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "hidden") void load();
    }, REFRESH_MS);
    const onFocus = () => void load();
    window.addEventListener("focus", onFocus);
    return () => {
      controller?.abort();
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [notifications, queue, path]);

  // Counts that this person no longer reads (a role or navigation change) show nothing.
  return {
    unread: notifications ? counts.unread : null,
    needsUs: queue ? counts.needsUs : null,
  };
}
