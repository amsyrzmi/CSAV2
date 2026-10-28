/**
 * Shared motion rules for decorative animation. Pausing decoration never pauses a real
 * server job; these inputs only decide whether purely visual motion may run.
 */
export interface MotionInputs {
  /** The visitor's OS asks for reduced motion. */
  reducedMotion: boolean;
  /** The tab is hidden. */
  documentHidden: boolean;
  /** The animated element is scrolled out of view. */
  offscreen: boolean;
  /** The product-level "pause animations" setting is on. */
  paused: boolean;
}

/** localStorage key of the product-level pause preference. */
export const PAUSE_KEY = "fs-motion-paused";

/** Only the string "1" pauses decoration. */
export function parsePause(raw: string | null): boolean {
  return raw === "1";
}

/** Decoration may run only when every blocking input is false. */
export function motionAllowed(i: MotionInputs): boolean {
  return !i.reducedMotion && !i.documentHidden && !i.offscreen && !i.paused;
}
