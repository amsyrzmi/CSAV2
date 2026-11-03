"use client";

import { useEffect, useId, useRef, type ReactNode, type RefObject } from "react";
import { secondary } from "@/components/console/ui";
import { useMotionAllowed } from "./useMotionAllowed";

/**
 * A centred dialog on a native <dialog>, the same way SlideOver does it: showModal() gives the
 * focus move, the inert page behind it and the top layer, so there is no focus-trap library.
 * Escape, the Close button and a click on the backdrop all call `onClose`; the parent owns
 * `open`. Focus goes back to `returnFocusTo` (or whatever had it when the dialog opened), and
 * the page behind does not scroll while it is open. The panel is the <dialog> itself, so a
 * click whose target is the dialog is a click on the backdrop. The body scrolls inside
 * 85dvh; the title stays in view.
 */
export default function Dialog({
  open,
  onClose,
  title,
  children,
  testId,
  returnFocusTo,
  widthClass = "max-w-lg",
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children: ReactNode;
  testId?: string;
  returnFocusTo?: RefObject<HTMLElement | null>;
  widthClass?: string;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
  const downOnBackdropRef = useRef(false);
  const openRef = useRef(open);
  const returnRef = useRef(returnFocusTo);
  const titleId = useId();
  const motionAllowed = useMotionAllowed();

  useEffect(() => {
    openRef.current = open;
    returnRef.current = returnFocusTo;
  });

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog || !open) return;
    const active = document.activeElement;
    openerRef.current = active instanceof HTMLElement ? active : null;
    if (!dialog.open) dialog.showModal();

    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
      if (dialog.open) dialog.close();
      const target = returnRef.current?.current ?? openerRef.current;
      if (target?.isConnected) target.focus();
      openerRef.current = null;
    };
  }, [open]);

  return (
    <dialog
      ref={dialogRef}
      aria-labelledby={titleId}
      data-testid={testId}
      onCancel={(event) => {
        // Escape: let the parent decide, then the effect above closes the dialog.
        event.preventDefault();
        onClose();
      }}
      onClose={() => {
        // The browser closed it by itself (for example a second Escape) while still "open".
        if (openRef.current) onClose();
      }}
      onPointerDown={(event) => {
        // Remember whether this press began on the backdrop (the dialog itself).
        downOnBackdropRef.current = event.target === event.currentTarget;
      }}
      onClick={(event) => {
        // The inner wrapper fills the dialog, so a target of the dialog itself is the backdrop.
        // Close only if the press also began there: a drag that starts in the panel (selecting
        // text) and ends on the backdrop is dispatched as a click on the dialog.
        const closeIt = downOnBackdropRef.current && event.target === event.currentTarget;
        downOnBackdropRef.current = false;
        if (closeIt) onClose();
      }}
      className={`m-auto w-[calc(100%-2rem)] ${widthClass} overflow-hidden rounded-[var(--fs-radius-tile)] border border-[color:var(--fs-border)] bg-[var(--fs-surface-raised)] p-0 text-[color:var(--fs-text)] shadow-[var(--fs-shadow-dialog)] backdrop:bg-[var(--fs-scrim)]`}
    >
      <div
        className={`flex max-h-[85dvh] flex-col ${open && motionAllowed ? "fs-fade" : ""}`}
      >
        <div className="flex items-center justify-between gap-3 border-b border-[color:var(--fs-border)] px-4 py-3">
          <h2
            id={titleId}
            className="min-w-0 font-(family-name:--fs-font-heading) text-base font-semibold [overflow-wrap:anywhere]"
          >
            {title}
          </h2>
          <button type="button" className={`${secondary} shrink-0`} onClick={onClose}>
            Close
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4">
          {open ? children : null}
        </div>
      </div>
    </dialog>
  );
}
