"use client";
import { hrNavItems, type HrTab } from "@/lib/hr-paths";
import SectionNav from "../kit/SectionNav";

/**
 * The People & HR tabs the viewer may use. Each is a link, so back, bookmarks and new tabs work.
 * It is the same underlined section bar as Business management, so every page group reads alike.
 */
export function HrTabBar({ tabs, current, platform, tenant }: {
  tabs: HrTab[]; current: HrTab; platform: boolean; tenant: string;
}) {
  return <SectionNav label="HR sections" testId="hr-sections" current={`hr_${current}`}
    items={hrNavItems(tabs, platform, tenant)} />;
}
