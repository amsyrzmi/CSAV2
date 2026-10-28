"use client";

import { useEffect, useId, useRef, type ReactNode, type RefObject } from "react";
import { secondary } from "@/components/console/ui";
import { useMotionAllowed } from "./useMotionAllowed";

/**
 * A panel from the right edge on a native <dialog>: showModal() gives the focus move, the
 * inert page behind it and the top layer, so there is no focus-trap library. Escape, the
 * Close button and a click on the backdrop all call `onClose`; the parent owns `open`.
 * Focus goes back to `returnFocusTo` (or whatever had it when the panel opened), and the
 * page behind does not scroll while it is open.
 */
export default function SlideOver({
  open,
  onClose,
  title,
  returnFocusTo,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  returnFocusTo?: RefObject<HTMLElement | null>;
  children: ReactNode;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const openerRef = useRef<HTMLElement | null>(null);
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
      onCancel={(event) => {
        // Escape: let the parent decide, then the effect above closes the dialog.
        event.preventDefault();
        onClose();
      }}
      onClose={() => {
        // The browser closed it by itself (for example a second Escape) while still "open".
        if (openRef.current) onClose();
      }}
      onClick={(event) => {
        // The panel fills the dialog, so a click that lands on the dialog itself is the backdrop.
        if (event.target === event.currentTarget) onClose();
      }}
      className="fixed inset-y-0 right-0 left-auto m-0 h-dvh max-h-none w-full max-w-sm border-0 bg-white p-0 text-[color:var(--fs-text)] shadow-[var(--fs-shadow-dialog)] backdrop:bg-[#1c2b40]/40"
    >
      <div
        className={`flex h-full flex-col ${open && motionAllowed ? "fs-slide-in-right" : ""}`}
      >
        <div className="flex items-center justify-between gap-3 border-b border-[color:var(--fs-border)] px-4 py-3">
          <h2
            id={titleId}
            className="font-(family-name:--fs-font-heading) text-base font-semibold"
          >
            {title}
          </h2>
          <button type="button" className={secondary} onClick={onClose}>
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
