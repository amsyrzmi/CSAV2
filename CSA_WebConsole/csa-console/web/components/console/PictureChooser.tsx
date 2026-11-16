"use client";

import { useId, useRef, useState } from "react";
import { PICTURE_LIMITS, pictureProblem } from "@/lib/pictures";
import { secondary } from "./ui";

/**
 * A picture chosen now and uploaded later, for a subject that does not exist yet (a QR method in
 * its add form): the owner sends the file after the subject is created. `PictureField` is the
 * control for a subject that exists, and uploads at once.
 *
 * A file that will certainly be refused (wrong kind, empty, over 2 MB) is never kept: its sentence
 * shows at once and nothing is chosen, so the surrounding form still saves, without a picture.
 * It is safe inside a form: every button is `type="button"`.
 */
export default function PictureChooser({ file, onChange, testId, label, hint, disabled = false }: {
  /** The file the owner holds now, or null. */
  file: File | null;
  onChange: (file: File | null) => void;
  testId: string;
  label: string;
  /** An extra line under the limits, such as the QR size advice. */
  hint?: string;
  /** While the surrounding form saves, the choice cannot change. */
  disabled?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const chooseRef = useRef<HTMLButtonElement>(null);
  const hintId = useId();
  const [error, setError] = useState("");
  function choose(chosen: File | undefined) {
    if (!chosen) return;
    const problem = pictureProblem(chosen);
    setError(problem ?? "");
    onChange(problem ? null : chosen);
  }
  return (
    <div data-testid={testId} className="space-y-2">
      <p className="text-sm">{label}</p>
      <p className="text-sm text-ink-700 break-words" aria-live="polite" data-testid={`${testId}-name`}>
        {file ? file.name : "No picture chosen."}</p>
      <div className="flex flex-wrap gap-2">
        <button ref={chooseRef} type="button" className={secondary} disabled={disabled}
          data-testid={`${testId}-choose`} aria-describedby={hintId}
          onClick={() => inputRef.current?.click()}>{file ? "Choose another" : "Choose picture"}</button>
        {file && (
          <button type="button" className={secondary} disabled={disabled} data-testid={`${testId}-clear`}
            aria-label="Remove the chosen picture"
            onClick={() => { onChange(null); setError(""); chooseRef.current?.focus(); }}>Remove</button>
        )}
        <input ref={inputRef} type="file" hidden accept="image/jpeg,image/png" data-testid={`${testId}-input`}
          tabIndex={-1} aria-label={label} disabled={disabled} onChange={(event) => {
            const chosen = event.target.files?.[0];
            event.target.value = "";
            choose(chosen);
          }} />
      </div>
      <p id={hintId} className="text-xs text-ink-600">
        {PICTURE_LIMITS}.{hint ? ` ${hint}.` : ""}</p>
      {error && <p role="alert" data-testid={`${testId}-error`}
        className="text-xs text-[var(--fs-danger)]">{error}</p>}
    </div>
  );
}
