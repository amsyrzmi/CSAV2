"use client";

import { useEffect, useId, useRef, useState } from "react";
import { action, secondary } from "../ui";

/**
 * A button that asks before it acts. Pressing it opens a short question with Confirm and Cancel
 * under it; focus moves to Cancel (the safe choice). Escape or Cancel closes the question and
 * puts focus back on the button; Confirm closes it first, so a second click of a double click
 * finds nothing to press, then runs `onConfirm` once.
 */
export default function ConfirmAction({
  label,
  question,
  onConfirm,
  disabled = false,
  testId,
}: {
  label: string;
  question: string;
  onConfirm: () => void;
  disabled?: boolean;
  testId: string;
}) {
  const [asking, setAsking] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const questionId = useId();

  useEffect(() => {
    if (asking) cancel.current?.focus();
  }, [asking]);

  function close() {
    setAsking(false);
    trigger.current?.focus();
  }
  function confirm() {
    // Focus first: the busy state that follows disables the button, and a disabled one cannot take it.
    close();
    onConfirm();
  }

  return (
    <div
      className="min-w-0"
      onKeyDown={(event) => {
        if (asking && event.key === "Escape") {
          event.stopPropagation();
          close();
        }
      }}
    >
      <button
        ref={trigger}
        type="button"
        data-testid={testId}
        className={secondary}
        aria-expanded={asking}
        disabled={disabled}
        onClick={() => setAsking(true)}
      >
        {label}
      </button>
      {asking && (
        <div
          role="group"
          aria-labelledby={questionId}
          data-testid={`${testId}-ask`}
          className="mt-2 space-y-3 rounded-[var(--fs-radius-control)] border border-[color:var(--fs-border-strong)] bg-[var(--fs-surface-raised)] p-3"
        >
          <p id={questionId} className="text-sm">{question}</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" data-testid={`${testId}-confirm`} className={action} disabled={disabled} onClick={confirm}>
              Confirm
            </button>
            <button ref={cancel} type="button" data-testid={`${testId}-cancel`} className={secondary} onClick={close}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
