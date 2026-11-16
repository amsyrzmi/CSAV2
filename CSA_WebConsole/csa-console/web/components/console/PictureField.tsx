"use client";

import { useEffect, useId, useRef, useState } from "react";
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
 *
 * The field is controlled: it always shows `picture`, and a successful upload or removal is
 * reported through `onChanged`, which the owner must write into the data it keeps (so a remount,
 * a refresh or another person's change can never leave an older picture on screen). `onChanged`
 * is called even when the answer arrives after the field is gone (its form was closed during the
 * upload): the server kept the picture, so the owner must record it by the subject's id, not in
 * the closed form's state.
 */
export default function PictureField({
  tenantId, path, picture, hint, onChanged, onBusy, testId, label = "Picture", subject, readOnly = false,
}: {
  /** Null for a tenant member; the target company for a platform admin (the read route needs it). */
  tenantId: string | null;
  /** The subject's `.../picture` route; not needed (and not used) when `readOnly`. */
  path?: string;
  /** The picture the owner holds now. */
  picture: Picture | null;
  /** An extra line under the limits, such as the QR size advice. */
  hint?: string;
  /** Told the new picture after a set or replace, and null after a removal. */
  onChanged?: (picture: Picture | null) => void;
  /** Told when an upload or removal starts (true) and ends (false), so the owner can hold other actions. */
  onBusy?: (busy: boolean) => void;
  testId: string;
  label?: string;
  /** Names what the picture belongs to ("Blue mug 1"): the preview's alt text and the buttons' names. */
  subject: string;
  readOnly?: boolean;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const chooseRef = useRef<HTMLButtonElement>(null);
  const refocus = useRef(false);
  const hintId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  // After the field is gone only `onChanged` still runs (see above); its own state is left alone.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);
  const working = (value: boolean) => {
    setBusy(value);
    onBusy?.(value);
  };
  // The busy state disables the button that had focus; once it is enabled again, focus returns to it.
  useEffect(() => {
    if (busy || !refocus.current) return;
    refocus.current = false;
    chooseRef.current?.focus();
  }, [busy]);
  const preview = picture && (
    // eslint-disable-next-line @next/next/no-img-element -- a same-origin, cookie-authenticated route
    <img src={pictureUrl(tenantId, picture.id, picture.version)} alt={`Picture of ${subject}`}
      data-testid={`${testId}-preview`}
      className="max-h-40 max-w-full rounded-md border border-ink-200 object-contain" />
  );
  if (readOnly) return picture ? <div data-testid={testId}>{preview}</div> : null;

  async function choose(file: File | undefined) {
    if (!file || busy) return;
    const problem = pictureProblem(file);
    if (problem) { setError(problem); return; }
    working(true);
    setError("");
    refocus.current = true;
    try {
      const saved = await putPicture(path ?? "", file);
      onChanged?.(saved);
    } catch (cause) {
      if (mounted.current) setError(pictureFailure(cause));
    } finally {
      if (mounted.current) working(false);
    }
  }

  async function remove() {
    if (busy) return;
    working(true);
    setError("");
    refocus.current = true;
    try {
      await removePicture(path ?? "");
      onChanged?.(null);
    } catch (cause) {
      if (mounted.current) setError(pictureFailure(cause));
    } finally {
      if (mounted.current) working(false);
    }
  }

  return (
    <div data-testid={testId} className="space-y-2">
      <p className="text-sm">{label}</p>
      {preview}
      <div className="flex flex-wrap gap-2">
        <button ref={chooseRef} type="button" className={secondary} disabled={busy}
          data-testid={`${testId}-choose`} aria-describedby={hintId}
          aria-label={`${picture ? "Replace" : "Choose"} picture for ${subject}`}
          onClick={() => inputRef.current?.click()}>
          {busy ? "Saving…" : picture ? "Replace" : "Choose picture"}</button>
        {picture && (
          <button type="button" className={secondary} disabled={busy} data-testid={`${testId}-remove`}
            aria-label={`Remove picture for ${subject}`} onClick={() => void remove()}>Remove</button>
        )}
        <input ref={inputRef} type="file" hidden accept="image/jpeg,image/png" data-testid={`${testId}-input`}
          tabIndex={-1} aria-label={`Picture file for ${subject}`} onChange={(event) => {
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
