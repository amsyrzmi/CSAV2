import type { ReactNode } from "react";

export type StatusTone =
  | "neutral"
  | "info"
  | "attention"
  | "success"
  | "error"
  | "customer"
  | "team";

/**
 * Background and text per tone. Every pair is at least 4.5:1 (UI guide 11); the guide's
 * --fs-* tokens are used wherever one exists, the rest come from the approved mockup.
 */
const TONES: Record<StatusTone, { background: string; color: string }> = {
  neutral: { background: "#EAF0FA", color: "#445577" },
  info: { background: "var(--fs-info-bg)", color: "var(--fs-info)" },
  attention: { background: "var(--fs-warning-bg)", color: "var(--fs-warning)" },
  success: { background: "var(--fs-success-bg)", color: "var(--fs-success)" },
  error: { background: "var(--fs-danger-bg)", color: "var(--fs-danger)" },
  customer: { background: "#DCE8FF", color: "#183DA5" },
  team: { background: "#15203A", color: "#FFFFFF" },
};

/** A small pill. The meaning is always in the words, never in the colour alone. */
export default function StatusLabel({
  tone,
  children,
}: {
  tone: StatusTone;
  children: ReactNode;
}) {
  const { background, color } = TONES[tone];
  return (
    <span
      data-tone={tone}
      style={{
        display: "inline-flex",
        alignItems: "center",
        whiteSpace: "nowrap",
        padding: "2px 8px",
        borderRadius: "var(--fs-radius-pill)",
        fontSize: "var(--fs-text-xs)",
        fontWeight: 600,
        lineHeight: 1.4,
        background,
        color,
      }}
    >
      {children}
    </span>
  );
}
