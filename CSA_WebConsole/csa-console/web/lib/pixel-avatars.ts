/**
 * Pure helpers for the pixel avatars: decoding the generated frame strings (format documented
 * in `pixel-avatars-data.ts`), merging them into rectangles, and the size and state rules.
 * No React and no data import, so node tests load this file on its own.
 */

export type SpriteGrid = 32 | 48;

/** Rendered sizes: whole-number multiples of a grid, so pixels stay crisp. */
export const AVATAR_SIZES = [32, 48, 64, 96, 144] as const;
export type AvatarSize = (typeof AVATAR_SIZES)[number];

/** The grid a rendered size draws from: 32 and 64 use the 32 grid; 48, 96 and 144 the 48 grid. */
export function gridForSize(size: number): SpriteGrid {
  if (size === 32 || size === 64) return 32;
  if (size === 48 || size === 96 || size === 144) return 48;
  throw new Error(`Unsupported avatar size: ${String(size)}`);
}

const ROW = /^(?:[.a-l][0-9]+)+$/;
const TOKEN = /([.a-l])([0-9]+)/g;

/**
 * Decode one frame to rows of palette indices (-1 for a transparent cell). Throws when the
 * grid is not 32 or 48, the row count differs from the grid, a row is not made only of
 * `<symbol><count>` tokens (symbol `.` or `a`..`l`, count >= 1), or a row does not add up to
 * the grid width.
 */
export function decodeFrame(encoded: string, grid: SpriteGrid): number[][] {
  if (grid !== 32 && grid !== 48) throw new Error(`Unsupported sprite grid: ${String(grid)}`);
  const rows = encoded.split("/");
  if (rows.length !== grid) {
    throw new Error(`Sprite frame has ${rows.length} rows, expected ${grid} (height)`);
  }
  return rows.map((row, y) => {
    if (!ROW.test(row)) throw new Error(`Malformed sprite row ${y}: unknown symbol or count`);
    const cells: number[] = [];
    for (const [, symbol, count] of row.matchAll(TOKEN)) {
      const run = Number(count);
      if (run < 1) throw new Error(`Malformed sprite row ${y}: count must be at least 1`);
      // Check before pushing, so a runaway count never allocates and a full row plus more throws.
      if (cells.length + run > grid) throw new Error(`Sprite row ${y} is wider than the grid ${grid}`);
      const value = symbol === "." ? -1 : symbol.charCodeAt(0) - 97;
      for (let i = 0; i < run; i += 1) cells.push(value);
    }
    if (cells.length !== grid) {
      throw new Error(`Sprite row ${y} does not add up to the grid width ${grid}`);
    }
    return cells;
  });
}

export type SpriteRect = { x: number; y: number; w: number; fill: string };

/** Horizontal runs of one colour merged into rectangles, row-major; transparent cells skipped. */
export function frameRects(frame: number[][], palette: string[]): SpriteRect[] {
  const rects: SpriteRect[] = [];
  frame.forEach((row, y) => {
    let x = 0;
    while (x < row.length) {
      const index = row[x];
      if (index === -1) {
        x += 1;
        continue;
      }
      const fill = index >= 0 ? palette[index] : undefined;
      if (fill === undefined) throw new Error(`Palette has no entry ${index}`);
      let end = x + 1;
      while (end < row.length && row[end] === index) end += 1;
      rects.push({ x, y, w: end - x, fill });
      x = end;
    }
  });
  return rects;
}

/**
 * `rest` is frame 0, undimmed, never animated: for a sprite that only marks who spoke (a past
 * reply in a thread). `still` and `pending` are the same but dimmed, to say "not active".
 */
export type AvatarState = "idle" | "still" | "pending" | "rest" | "working" | "wake";

/** Which generated frame set a state draws from: "working" exists only on the 48 grid. */
export type FrameSet = "idle" | "working";

/**
 * How the frames move: `none` is frame 0 alone; `idle` loops four idle frames over one second;
 * `working` loops eight working frames over 1.2 s; `wake` plays the eight working frames once
 * over 1.5 s and holds the last (the parent switches back to `idle` afterwards).
 */
export type AvatarMotion = "none" | "idle" | "working" | "wake";

const STATES: readonly AvatarState[] = ["idle", "still", "pending", "rest", "working", "wake"];

/**
 * The frame set a state plays when it animates. `working` and `wake` use the working frames,
 * which exist only on the 48 grid; on the 32 grid they fall back to the idle loop. Every other
 * state uses the idle frames.
 */
export function frameSetFor(state: AvatarState, grid: SpriteGrid): FrameSet {
  if (!STATES.includes(state)) throw new Error(`Unknown avatar state: ${String(state)}`);
  if (grid !== 32 && grid !== 48) throw new Error(`Unsupported sprite grid: ${String(grid)}`);
  return (state === "working" || state === "wake") && grid === 48 ? "working" : "idle";
}

export type AvatarPlan = {
  animate: boolean;
  dimmed: boolean;
  motion: AvatarMotion;
  frameSet: FrameSet;
  /** How many frames to render, from frame 0: 1 when still, 4 for idle, 8 for working/wake. */
  frameCount: 1 | 4 | 8;
};

/**
 * What to draw. `still` and `pending` are dimmed and never animate; `rest` never animates and is
 * not dimmed; the other states animate only while decorative motion is allowed (see
 * `useMotionAllowed`). When nothing animates the sprite is idle frame 0 alone, the rest pose,
 * whatever the state (reduced motion, the pause preference and offscreen included).
 */
export function avatarPlan(state: AvatarState, motionAllowed: boolean, grid: SpriteGrid): AvatarPlan {
  const frameSet = frameSetFor(state, grid);
  const dimmed = state === "still" || state === "pending";
  const animate = state !== "rest" && !dimmed && motionAllowed;
  if (!animate) return { animate, dimmed, motion: "none", frameSet: "idle", frameCount: 1 };
  if (frameSet === "idle") return { animate, dimmed, motion: "idle", frameSet, frameCount: 4 };
  return { animate, dimmed, motion: state === "wake" ? "wake" : "working", frameSet, frameCount: 8 };
}

/**
 * The sticker outline: four crisp drop-shadows (right, left, down, up) of one sprite cell, in the
 * theme's `--fs-sprite-halo` (light on night, transparent on cobalt and bureau). One cell is
 * `size / grid` px, so the outline is one sprite pixel wide at every size. Nothing in the dim
 * state (`undefined`): a dimmed avatar says "not active" and is quieter than a live one.
 */
export function haloFilter(size: number, dimmed: boolean): string | undefined {
  if (dimmed) return undefined;
  const cell = size / gridForSize(size);
  return [`${cell}px 0`, `-${cell}px 0`, `0 ${cell}px`, `0 -${cell}px`]
    .map((offset) => `drop-shadow(${offset} 0 var(--fs-sprite-halo))`)
    .join(" ");
}
