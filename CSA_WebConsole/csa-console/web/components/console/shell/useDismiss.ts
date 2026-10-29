"use client";

import { useEffect, type KeyboardEvent, type RefObject } from "react";

/**
 * Light dismissal for the top-bar popovers. While `open`, a press or focus outside `rootRef`
 * closes it (leaving focus where the person put it), and Escape inside it closes it and puts
 * focus back on the trigger. Returns the keydown handler to put on the root element.
 */
export function useDismiss({
  open,
  rootRef,
  triggerRef,
  onClose,
}: {
  open: boolean;
  rootRef: RefObject<HTMLElement | null>;
  triggerRef: RefObject<HTMLElement | null>;
  onClose: () => void;
}): { onKeyDown: (event: KeyboardEvent) => void } {
  useEffect(() => {
    if (!open) return;
    const away = (event: Event) => {
      const root = rootRef.current;
      if (root && event.target instanceof Node && !root.contains(event.target)) onClose();
    };
    document.addEventListener("pointerdown", away);
    document.addEventListener("focusin", away);
    return () => {
      document.removeEventListener("pointerdown", away);
      document.removeEventListener("focusin", away);
    };
  }, [open, rootRef, onClose]);

  return {
    onKeyDown(event) {
      if (event.key !== "Escape" || !open) return;
      event.stopPropagation();
      onClose();
      triggerRef.current?.focus();
    },
  };
}
