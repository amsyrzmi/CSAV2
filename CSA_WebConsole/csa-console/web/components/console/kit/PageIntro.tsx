import type { ReactNode } from "react";

/**
 * One or two plain lines under a page title saying what the page is for. Keep the sentence
 * short enough for two lines at 320 px; it is never clipped, so nothing is hidden from a reader.
 */
export default function PageIntro({ children }: { children: ReactNode }) {
  return (
    <p
      data-testid="page-intro"
      className="mb-5 max-w-prose text-sm leading-6 text-[color:var(--fs-text-secondary)] [overflow-wrap:anywhere]"
    >
      {children}
    </p>
  );
}
