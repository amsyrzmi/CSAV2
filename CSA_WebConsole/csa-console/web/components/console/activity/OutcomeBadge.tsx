import type { Outcome } from "@/lib/activity-view";

const LOOK: Record<Outcome, { text: string; background: string; color: string; path: string }> = {
  worked: {
    text: "Worked",
    background: "var(--fs-success-bg)",
    color: "var(--fs-success)",
    path: "m5 12 5 5 9-10",
  },
  needs_attention: {
    text: "Needed attention",
    background: "var(--fs-warning-bg)",
    color: "var(--fs-warning)",
    path: "M12 6v7m0 4v.5",
  },
  partial: {
    text: "Partly finished",
    background: "var(--fs-neutral-bg)",
    color: "var(--fs-neutral-text)",
    path: "M6 12h12",
  },
};

/** How a run ended: the words and a small mark carry it; the colour only backs them up. */
export default function OutcomeBadge({ outcome, testId }: { outcome: Outcome; testId?: string }) {
  const look = LOOK[outcome];
  return (
    <span
      data-testid={testId}
      data-outcome={outcome}
      className="inline-flex items-center gap-1 whitespace-nowrap rounded-[var(--fs-radius-round)] px-2 py-0.5 text-[length:var(--fs-text-xs)] font-semibold leading-[1.4]"
      style={{ background: look.background, color: look.color }}
    >
      <svg
        aria-hidden="true"
        viewBox="0 0 24 24"
        width="12"
        height="12"
        fill="none"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
        strokeLinejoin="round"
        className="shrink-0"
      >
        <path d={look.path} />
      </svg>
      {look.text}
    </span>
  );
}
