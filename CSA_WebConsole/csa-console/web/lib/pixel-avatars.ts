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
      if (cells.length + run > grid) break;
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
      if (index < 0) {
        x += 1;
        continue;
      }
      const fill = palette[index];
      if (fill === undefined) throw new Error(`Palette has no entry ${index}`);
      let end = x + 1;
      while (end < row.length && row[end] === index) end += 1;
      rects.push({ x, y, w: end - x, fill });
      x = end;
    }
  });
  return rects;
}

export type AvatarState = "idle" | "still" | "pending" | "working" | "wake";

/** Which generated frame set a state plays. */
export type FrameSet = "idle";

/**
 * Fallback, on purpose: `working` and `wake` have no frames of their own yet, so they play the
 * idle loop. Task 12 adds a "working" frame set to the data and changes only this function.
 */
export function frameSetFor(state: AvatarState): FrameSet {
  switch (state) {
    case "idle":
    case "still":
    case "pending":
    case "working":
    case "wake":
      return "idle";
    default:
      throw new Error(`Unknown avatar state: ${String(state)}`);
  }
}

export type AvatarPlan = { animate: boolean; dimmed: boolean; frameSet: FrameSet };

/**
 * What to draw. `still` and `pending` are dimmed and never animate; the other states animate
 * only while decorative motion is allowed (see `useMotionAllowed`). When `animate` is false
 * the sprite is frame 0 alone.
 */
export function avatarPlan(state: AvatarState, motionAllowed: boolean): AvatarPlan {
  const frameSet = frameSetFor(state);
  const dimmed = state === "still" || state === "pending";
  return { animate: !dimmed && motionAllowed, dimmed, frameSet };
}
