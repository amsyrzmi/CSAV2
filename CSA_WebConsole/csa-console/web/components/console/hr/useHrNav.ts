"use client";
import { useEffect, useMemo, useState } from "react";
import { hrClient } from "@/lib/hr-api";
import { hrNavItems, isHrPagePath, visibleTabs, type HrNavItem, type HrTab } from "@/lib/hr-paths";

/**
 * The People & HR tabs the viewer may use, as rail sub-pages. Reads the HR context once per
 * tenant, and again each time the path moves from outside HR into it (so a changed access is
 * picked up on the way in). `null` while unknown, for another tenant than the answer's, and
 * after a failure: the rail then shows plain People & HR, never a tab the viewer cannot use.
 */
export function useHrNav({ enabled, platform, tenant, path }: {
  enabled: boolean; platform: boolean; tenant: string | null; path: string;
}): HrNavItem[] | null {
  const [answer, setAnswer] = useState<{ tenant: string; tabs: HrTab[] } | null>(null);
  const inHr = isHrPagePath(path);
  const [wasInHr, setWasInHr] = useState(inHr);
  const [entries, setEntries] = useState(0);
  if (wasInHr !== inHr) {
    setWasInHr(inHr);
    if (inHr) setEntries(entries + 1);
  }
  useEffect(() => {
    if (!enabled || !tenant) return;
    let active = true;
    hrClient(tenant).context()
      .then(ctx => { if (active) setAnswer({ tenant, tabs: visibleTabs(ctx) }); })
      .catch(() => { if (active) setAnswer(null); });
    return () => { active = false; };
  }, [enabled, tenant, entries]);
  return useMemo(
    () => enabled && tenant && answer?.tenant === tenant ? hrNavItems(answer.tabs, platform, tenant) : null,
    [enabled, tenant, answer, platform],
  );
}
