"use client";
import Link from "next/link";
import { hrNavItems, type HrTab } from "@/lib/hr-paths";
import { action, secondary } from "../ui";

/** The People & HR tabs the viewer may use. Each is a link, so back, bookmarks and new tabs work. */
export function HrTabBar({ tabs, current, platform, tenant }: {
  tabs: HrTab[]; current: HrTab; platform: boolean; tenant: string;
}) {
  return <nav aria-label="HR sections" className="flex flex-wrap gap-2">
    {hrNavItems(tabs, platform, tenant).map(item => {
      const here = item.key === `hr_${current}`;
      return <Link key={item.key} href={item.href} data-testid={item.testId}
        className={here ? action : secondary}
        aria-current={here ? "page" : undefined}>{item.label}</Link>;
    })}
  </nav>;
}
