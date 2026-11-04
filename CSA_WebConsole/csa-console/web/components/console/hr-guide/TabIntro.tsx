import type { HrTab } from "@/lib/hr-paths";

/**
 * One plain sentence under the People & HR tab bar saying what the tab is for. Each sentence was
 * checked against what that tab shows: for instance, leave requests live under Requests, so the
 * Leave tab does not promise them, and some Leave parts depend on the viewer's access.
 */
const INTRO: Record<HrTab, string> = {
  home: "What needs you today, and quick ways to start.",
  people: "People in your company, their details and who they report to.",
  recruitment: "Candidates, their CVs and notes, and interview times.",
  leave: "Leave balances, work calendars and, where you have access, attendance and shifts.",
  payroll: "Prepare payroll figures for a second person to check.",
  training: "Training requests and decisions.",
  requests: "HR requests waiting for a decision, and the ones already decided.",
  settings: "How People & HR works for your company.",
};

export default function TabIntro({ tab }: { tab: HrTab }) {
  return (
    <p
      data-testid="hr-tab-intro"
      className="max-w-prose text-sm leading-6 text-[color:var(--fs-text-secondary)] [overflow-wrap:anywhere]"
    >
      {INTRO[tab]}
    </p>
  );
}
