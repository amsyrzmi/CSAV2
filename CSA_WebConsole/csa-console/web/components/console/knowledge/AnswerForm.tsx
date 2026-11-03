"use client";

import { useId, useRef, useState } from "react";
import { post } from "@/lib/api";
import type { KbGap } from "@/lib/identity-contracts";
import { action, field, Notice, secondary } from "../ui";

/**
 * "Add an answer" for one unanswered question: a short text saved as a note in the library.
 * It posts once per press, says it is saved only after the server agrees, and on a failure
 * says nothing was confirmed so the owner checks the library before trying again.
 */
export default function AnswerForm({
  gap,
  path,
  disabled,
  onSaved,
}: {
  gap: KbGap;
  path: (resource: string) => string;
  disabled: boolean;
  onSaved: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [answer, setAnswer] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  const pressed = useRef(false);
  const region = useId();

  async function save() {
    // A second press before the first answer arrives must not post again.
    if (pressed.current || !answer.trim()) return;
    pressed.current = true;
    setSaving(true); setError("");
    try {
      await post(path("kb/documents"), { title: gap.query.slice(0, 255), content: answer.trim() });
      setSaved(true); setOpen(false);
      onSaved();
    } catch (cause) {
      setError(`We couldn't save the answer: ${(cause as Error).message}. Check What it knows before you try again, so it is not added twice.`);
    } finally {
      pressed.current = false;
      setSaving(false);
    }
  }

  return (
    <div className="w-full min-w-0">
      {!saved && <button type="button" className={secondary} disabled={disabled} aria-expanded={open} aria-controls={region}
        data-testid={`kb-gap-${gap.id}-answer`} onClick={() => setOpen((current) => !current)}>
        Add an answer
      </button>}
      {saved && <Notice testId={`kb-answer-saved-${gap.id}`}>Saved. Your assistant can use this answer now.</Notice>}
      <div id={region} hidden={!open} className="mt-3 space-y-3">
        {open && <>
          <p className="font-medium [overflow-wrap:anywhere]">{gap.query}</p>
          <div>
            <label htmlFor={`${region}-text`} className="mb-2 block text-sm">Your answer</label>
            <textarea id={`${region}-text`} data-testid={`kb-answer-text-${gap.id}`} value={answer} rows={4}
              onChange={(event) => setAnswer(event.target.value)}
              className={`${field} h-auto min-h-24 w-full py-2`} />
          </div>
          {error && <Notice error testId={`kb-answer-error-${gap.id}`}>{error}</Notice>}
          <div className="flex flex-wrap gap-3">
            <button type="button" className={action} data-testid={`kb-answer-save-${gap.id}`}
              disabled={saving || !answer.trim()} onClick={() => void save()}>Save answer</button>
            <button type="button" className={secondary} disabled={saving} onClick={() => setOpen(false)}>Cancel</button>
          </div>
        </>}
      </div>
    </div>
  );
}
