"use client";
import Link from "next/link";
import { HR_TABS, hrHref, type HrRoute, type HrTab } from "@/lib/hr-paths";
import { action, secondary } from "../ui";

const routeFor = (tab: HrTab): HrRoute => tab === "people" ? { tab, view: "list" } : { tab };

/** The People & HR tabs the viewer may use. Each is a link, so back, bookmarks and new tabs work. */
export function HrTabBar({ tabs, current, platform, tenant }: {
  tabs: HrTab[]; current: HrTab; platform: boolean; tenant: string;
}) {
  return <nav aria-label="HR sections" className="flex flex-wrap gap-2">
    {HR_TABS.filter(item => tabs.includes(item.tab)).map(item =>
      <Link key={item.tab} href={hrHref(routeFor(item.tab), platform, tenant)}
        className={item.tab === current ? action : secondary}
        aria-current={item.tab === current ? "page" : undefined}>{item.label}</Link>)}
  </nav>;
}
