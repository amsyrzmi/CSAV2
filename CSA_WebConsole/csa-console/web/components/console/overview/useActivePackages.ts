"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { PackageState } from "@/lib/services-api";

const REFRESH_MS = 60_000;

/**
 * The company's packages, read once for everything on Overview that depends on them: the page body
 * and the status strip. `undefined` while the first read is on its way, `null` when it failed. The
 * read repeats every minute while the tab is visible and when the window regains focus; a repeat
 * that fails keeps what the page already knows, so a flaky minute never swaps the body.
 *
 * `scope` is the platform admin's chosen company, "" for a member, or null when there is no
 * company to ask about. A change of scope forgets the earlier company's answer at once.
 */
export function useActivePackages(scope: string | null): PackageState[] | null | undefined {
  const [read, setRead] = useState<{ scope: string; packages: PackageState[] | null } | null>(null);

  useEffect(() => {
    if (scope === null) return;
    const mine: string = scope;
    const query = scope ? `?client_id=${encodeURIComponent(scope)}` : "";
    let current: AbortController | null = null;

    async function load() {
      current?.abort();
      const controller = new AbortController();
      current = controller;
      let packages: PackageState[] | null = null;
      try {
        const answer = await api<{ packages: PackageState[] }>(`/console/v1/services/active${query}`, {
          signal: controller.signal,
        });
        packages = Array.isArray(answer?.packages) ? answer.packages : null;
      } catch {
        // Fall through with null; an aborted read is dropped just below.
      }
      if (controller.signal.aborted) return;
      setRead((before) => (packages === null && before?.scope === mine ? before : { scope: mine, packages }));
    }

    void load();
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "hidden") void load();
    }, REFRESH_MS);
    const onFocus = () => void load();
    window.addEventListener("focus", onFocus);
    return () => {
      current?.abort();
      window.clearInterval(timer);
      window.removeEventListener("focus", onFocus);
    };
  }, [scope]);

  if (scope === null || read === null || read.scope !== scope) return undefined;
  return read.packages;
}
