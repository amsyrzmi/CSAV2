"use client";

import { useEffect, useId, useRef, useState } from "react";
import { getSummary, makeSummary, type TrainingItem } from "@/lib/training-api";
import { summaryView, type SummaryState } from "@/lib/training-form";
import { TechnicalDetails } from "../kit";
import { action, Notice, secondary } from "../ui";
import { codeOf, muted } from "./shared";

/**
 * The item's shared summary. Until one exists there is "Summarise this PDF" with the provider
 * notice beside it, for every role; the first press makes and stores it. A stored summary is
 * read on arrival and shown as text only (React escapes it; never HTML or Markdown). Managers
 * may replace it after a confirm, because everyone in the company sees the same one.
 */
export default function SummaryPanel({
  tenantId,
  item,
  canManage,
}: {
  tenantId: string | null;
  item: TrainingItem;
  canManage: boolean;
}) {
  // Whether a summary existed when the page opened: only then is there one to read.
  const [stored] = useState(item.has_summary);
  const [state, setState] = useState<SummaryState>(
    stored ? { phase: "reading" } : { phase: "ready", summary: null, error: null },
  );
  const [confirming, setConfirming] = useState(false);
  const running = useRef(false);
  const headingId = useId();
  const noticeId = useId();
  const regenerateRef = useRef<HTMLButtonElement>(null);
  const itemId = item.id;

  useEffect(() => {
    if (!stored) return;
    let live = true;
    getSummary(tenantId, itemId).then(
      (summary) => {
        if (live) setState({ phase: "ready", summary, error: null });
      },
      (cause: unknown) => {
        if (live) setState({ phase: "ready", summary: null, error: codeOf(cause) });
      },
    );
    return () => {
      live = false;
    };
  }, [stored, tenantId, itemId]);

  async function make(regenerate: boolean) {
    if (running.current || state.phase === "reading") return;
    running.current = true;
    const before = state.summary;
    setConfirming(false);
    setState({ phase: "making", summary: before, regenerate });
    try {
      setState({ phase: "ready", summary: await makeSummary(tenantId, itemId, regenerate), error: null });
    } catch (cause) {
      setState({ phase: "ready", summary: before, error: codeOf(cause) });
    } finally {
      running.current = false;
    }
  }

  const view = summaryView(state, canManage);

  return (
    <section
      aria-labelledby={headingId}
      aria-busy={view.busy}
      data-testid="training-summary-panel"
      className="card min-w-0 space-y-4 rounded-[14px] p-4 sm:p-5"
    >
      <h2 id={headingId} className="font-(family-name:--fs-font-heading) text-base font-semibold">Summary</h2>
      <p role="status" aria-live="polite" data-testid="training-summary-status" className="text-sm empty:hidden">
        {view.status ?? ""}
      </p>
      {view.error && <Notice error testId="training-summary-error">{view.error}</Notice>}
      {view.stored && (
        <div data-testid="training-summary" className="min-w-0 space-y-3 [overflow-wrap:anywhere]">
          {view.stored.paragraphs.map((paragraph, index) => (
            <p key={index} className="text-sm leading-6 whitespace-pre-line">{paragraph}</p>
          ))}
          {view.stored.points.length > 0 && (
            <div>
              <h3 className="text-sm font-semibold">Key points</h3>
              <ul data-testid="training-summary-points" className="mt-1 list-disc space-y-1 pl-5 text-sm leading-6">
                {view.stored.points.map((point, index) => <li key={index}>{point}</li>)}
              </ul>
            </div>
          )}
          {view.stored.madeOn && <p className={muted} data-testid="training-summary-made">{view.stored.madeOn}</p>}
          <p className={muted} data-testid="training-summary-note">{view.stored.note}</p>
          <TechnicalDetails testId="training-summary-tech">
            <p className="text-sm">Model: {view.stored.model}</p>
          </TechnicalDetails>
        </div>
      )}
      {view.ask && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <button
            type="button"
            className={action}
            disabled={view.askDisabled}
            aria-describedby={view.notice ? noticeId : undefined}
            data-testid="training-summarise"
            onClick={() => void make(false)}
          >
            Summarise this PDF
          </button>
          {view.notice && (
            <p id={noticeId} className={`${muted} min-w-0 flex-1 basis-56`} data-testid="training-provider-notice">
              {view.notice}
            </p>
          )}
        </div>
      )}
      {view.regenerate && !confirming && (
        <button
          ref={regenerateRef}
          type="button"
          className={secondary}
          disabled={view.regenerateDisabled}
          data-testid="training-regenerate"
          onClick={() => setConfirming(true)}
        >
          Make a new summary
        </button>
      )}
      {view.regenerate && confirming && (
        <div
          role="group"
          aria-label="Make a new summary"
          data-testid="training-regenerate-confirm-box"
          className="space-y-3 rounded-[var(--fs-radius-panel)] border border-[color:var(--fs-border-control)] bg-[var(--fs-surface-blue)] p-3"
        >
          <p className="text-sm">This replaces the summary everyone in your company sees.</p>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className={action}
              autoFocus
              data-testid="training-regenerate-confirm"
              onClick={() => void make(true)}
            >
              Replace the summary
            </button>
            <button
              type="button"
              className={secondary}
              onClick={() => {
                setConfirming(false);
                // The button that opened the confirm comes back; focus returns to it.
                requestAnimationFrame(() => regenerateRef.current?.focus());
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
