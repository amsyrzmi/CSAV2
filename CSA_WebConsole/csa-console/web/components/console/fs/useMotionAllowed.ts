"use client";

import { useEffect, useState, type RefObject } from "react";
import { PAUSE_KEY, motionAllowed, parsePause } from "@/lib/motion";

const REDUCED_QUERY = "(prefers-reduced-motion: reduce)";
/** Same-tab signal; the browser's own `storage` event only reaches other tabs. */
const PAUSE_EVENT = "fs-motion-pause-change";

function readPaused(): boolean {
  try {
    return parsePause(window.localStorage.getItem(PAUSE_KEY));
  } catch {
    return false;
  }
}

/** Write the product-level pause preference and tell hooks in this tab. */
export function setMotionPaused(paused: boolean): void {
  if (typeof window === "undefined") return;
  try {
    if (paused) window.localStorage.setItem(PAUSE_KEY, "1");
    else window.localStorage.removeItem(PAUSE_KEY);
  } catch {
    // Storage is unavailable; the event below still updates this tab until reload.
  }
  window.dispatchEvent(new Event(PAUSE_EVENT));
}

/**
 * Whether decorative motion may run right now. False on the server and before mount, so a
 * reduced-motion visitor never sees a flash of motion. Pass a ref to also stop while that
 * element is offscreen. Missing browser APIs count as: not reduced, visible, not paused,
 * intersecting.
 */
export function useMotionAllowed(ref?: RefObject<Element | null>): boolean {
  const [allowed, setAllowed] = useState(false);

  useEffect(() => {
    const media =
      typeof window.matchMedia === "function" ? window.matchMedia(REDUCED_QUERY) : null;
    const target = ref?.current ?? null;
    const observable = target !== null && typeof IntersectionObserver === "function";

    let reducedMotion = media?.matches ?? false;
    let documentHidden = document.visibilityState === "hidden";
    // Offscreen until the observer's first report when we are watching an element.
    let offscreen = observable;
    let paused = readPaused();

    const update = () =>
      setAllowed(motionAllowed({ reducedMotion, documentHidden, offscreen, paused }));

    const onMedia = () => {
      reducedMotion = media?.matches ?? false;
      update();
    };
    const onVisibility = () => {
      documentHidden = document.visibilityState === "hidden";
      update();
    };
    const onPause = () => {
      paused = readPaused();
      update();
    };
    const onStorage = (event: StorageEvent) => {
      if (event.key === null || event.key === PAUSE_KEY) onPause();
    };

    media?.addEventListener("change", onMedia);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("storage", onStorage);
    window.addEventListener(PAUSE_EVENT, onPause);

    let observer: IntersectionObserver | null = null;
    if (observable && target) {
      observer = new IntersectionObserver((entries) => {
        const last = entries[entries.length - 1];
        if (!last) return;
        offscreen = !last.isIntersecting;
        update();
      });
      observer.observe(target);
    }

    update();
    return () => {
      media?.removeEventListener("change", onMedia);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("storage", onStorage);
      window.removeEventListener(PAUSE_EVENT, onPause);
      observer?.disconnect();
    };
  }, [ref]);

  return allowed;
}
