"use client";

import { useId, useRef, useState } from "react";
import {
  PICTURE_LIMITS,
  pictureFailure,
  pictureProblem,
  pictureUrl,
  putPicture,
  removePicture,
  type Picture,
} from "@/lib/pictures";
import { secondary } from "./ui";

/**
 * One picture on a QR method, a plan or a product: the preview through the read route and, for
 * people who may change it, Choose picture / Replace / Remove. A file is checked here for kind
 * and size, then sent at once (not with the surrounding form's Save), so the picture is its own
 * action; the server's refusal is shown as a plain sentence. `readOnly` shows the preview only
 * (and nothing when there is no picture). It is safe inside a form: every button is `type="button"`.
 */
export default function PictureField({
  tenantId, path, picture, hint, onChanged, testId, label = "Picture", alt, readOnly = false,
}: {
  /** Null for a tenant member; the target company for a platform admin (the read route needs it). */
  tenantId: string | null;
  /** The subject's `.../picture` route; not needed (and not used) when `readOnly`. */
  path?: string;
  picture: Picture | null;
  /** An extra line under the limits, such as the QR size advice. */
  hint?: string;
  /** Told the new picture after a set or replace, and null after a removal. */
  onChanged?: (picture: Picture | null) => void;
  testId: string;
  label?: string;
  /** What the preview shows, for screen readers. */
  alt?: string;
  readOnly?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const hintId = useId();
  const [current, setCurrent] = useState(picture);
  // The parent's picture wins when it changes (a reload, or another save returned a newer one);
  // until then what this field just saved or removed shows.
  const incoming = picture ? `${picture.id}:${picture.version}` : "";
  const [seen, setSeen] = useState(incoming);
  if (seen !== incoming) {
    setSeen(incoming);
    setCurrent(picture);
  }
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const preview = current && (
    // eslint-disable-next-line @next/next/no-img-element -- a same-origin, cookie-authenticated route
    <img src={pictureUrl(tenantId, current.id, current.version)} alt={alt ?? label}
      data-testid={`${testId}-preview`}
      className="max-h-40 max-w-full rounded-md border border-ink-200 object-contain" />
  );
  if (readOnly) return current ? <div data-testid={testId}>{preview}</div> : null;

  async function choose(file: File | undefined) {
    if (!file || busy) return;
    const problem = pictureProblem(file);
    if (problem) { setError(problem); return; }
    setBusy(true);
    setError("");
    try {
      const saved = await putPicture(path ?? "", file);
      setCurrent(saved);
      onChanged?.(saved);
    } catch (cause) {
      setError(pictureFailure(cause));
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await removePicture(path ?? "");
      setCurrent(null);
      onChanged?.(null);
    } catch (cause) {
      setError(pictureFailure(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div data-testid={testId} className="space-y-2">
      <p className="text-sm">{label}</p>
      {preview}
      <div className="flex flex-wrap gap-2">
        <button type="button" className={secondary} disabled={busy} data-testid={`${testId}-choose`}
          aria-describedby={hintId} onClick={() => inputRef.current?.click()}>
          {busy ? "Saving…" : current ? "Replace" : "Choose picture"}</button>
        {current && (
          <button type="button" className={secondary} disabled={busy} data-testid={`${testId}-remove`}
            onClick={() => void remove()}>Remove</button>
        )}
        <input ref={inputRef} type="file" hidden accept="image/jpeg,image/png" data-testid={`${testId}-input`}
          tabIndex={-1} onChange={(event) => {
            const file = event.target.files?.[0];
            event.target.value = "";
            void choose(file);
          }} />
      </div>
      <p id={hintId} className="text-xs text-ink-600">
        {PICTURE_LIMITS}.{hint ? ` ${hint}.` : ""}</p>
      {error && <p role="alert" data-testid={`${testId}-error`}
        className="text-xs text-[var(--fs-danger)]">{error}</p>}
    </div>
  );
}
