"""Convert the eleven approved vector agent avatars into pixel-art idle frames.

Run ``python -m scripts.pixelate_avatars`` to rewrite ``lib/pixel-avatars-data.ts`` in the web
console, or add ``--check`` to fail when the committed file differs from a fresh generation.

Pipeline, per avatar and per grid size (32 and 48):

1. Chromium (Playwright) draws the SVG at 16x the grid size.
2. Every grid cell takes the most frequent entry of the SVG's own palette among its 16x16 samples
   (nearest colour per sample; mostly transparent cells stay empty).
3. Orphan pixels (no occupied 8-neighbour) are removed.
4. Palette entries are recoloured to the agent's hue; the navy outline and the white eyes stay.
5. Four idle frames are built with the agent's own motion.
6. Rows are run-length encoded into the data module (format documented in its header).

Determinism: the output depends only on the SVG bytes and on Chromium's 2D canvas. Nearest-colour
voting over 256 samples per cell is robust to anti-aliasing differences, so ``--check`` compares
the generated text exactly. If a different Chromium build ever flips a cell, regenerate and commit.
"""

from __future__ import annotations

import argparse
import colorsys
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "CSA_WebConsole" / "csa-console" / "web"
AVATARS = WEB / "public" / "fourstacks" / "agents"
AGENT_IDS_TS = WEB / "lib" / "agent-avatars.ts"
OUTPUT = WEB / "lib" / "pixel-avatars-data.ts"

SIZES = (32, 48)
SUPERSAMPLE = 16
MAX_PALETTE = 12
OUTLINE = "#15203A"
WHITE = "#FFFFFF"

# Hue (degrees) of each agent; the source art is blue (about 215 degrees).
HUE = {
    "support": 225,
    "knowledge": 172,
    "orders": 28,
    "tickets": 50,
    "ecommerce": 145,
    "invoices": 200,
    "assistant": 268,
    "hr": 330,
    "email": 5,
    "subscriptions": 95,
    "bookings": 305,
}
SOURCE_HUE = 215
HUE_SPREAD = 0.35  # how much of the source's own hue variation survives
SATURATION_BOOST = 1.05

Grid = list[list[int]]  # palette index per cell, -1 for transparent

# Runs in the browser: draws the SVG at SUPERSAMPLE x the grid and votes per cell.
RASTERISE_JS = """
async ({svg, n, k}) => {
  const W = n * k;
  const img = new Image();
  img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
  await img.decode();
  const c = document.createElement('canvas');
  c.width = c.height = W;
  const cx = c.getContext('2d', {willReadFrequently: true});
  cx.drawImage(img, 0, 0, W, W);
  const d = cx.getImageData(0, 0, W, W).data;
  const hexes = [...new Set((svg.match(/#[0-9A-Fa-f]{6}/g) || []).map(h => h.toUpperCase()))];
  const pal = hexes.map(h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16)));
  const grid = [];
  for (let y = 0; y < n; y++) {
    const row = [];
    for (let x = 0; x < n; x++) {
      const votes = new Array(pal.length + 1).fill(0);
      for (let yy = 0; yy < k; yy++) for (let xx = 0; xx < k; xx++) {
        const i = (((y * k + yy) * W) + (x * k + xx)) * 4;
        if (d[i + 3] < 140) { votes[pal.length]++; continue; }
        let best = 0, bd = 1e9;
        for (let q = 0; q < pal.length; q++) {
          const e = (pal[q][0] - d[i]) ** 2 + (pal[q][1] - d[i + 1]) ** 2
            + (pal[q][2] - d[i + 2]) ** 2;
          if (e < bd) { bd = e; best = q; }
        }
        votes[best]++;
      }
      let bi = 0;
      for (let q = 1; q < votes.length; q++) if (votes[q] > votes[bi]) bi = q;
      row.push(bi === pal.length ? -1 : bi);
    }
    grid.push(row);
  }
  return {palette: hexes, grid};
}
"""


def agent_ids() -> list[str]:
    """Read AGENT_IDS from lib/agent-avatars.ts so the generator never drifts from it."""
    source = AGENT_IDS_TS.read_text(encoding="utf-8")
    block = re.search(r"AGENT_IDS\s*=\s*\[(.*?)\]\s*as const", source, re.S)
    if block is None:
        raise SystemExit(f"AGENT_IDS not found in {AGENT_IDS_TS}")
    ids = re.findall(r'"([a-z_]+)"', block.group(1))
    if sorted(ids) != sorted(HUE):
        raise SystemExit(f"HUE table {sorted(HUE)} does not match AGENT_IDS {sorted(ids)}")
    return ids


def rasterise(ids: list[str]) -> dict[str, dict[int, tuple[list[str], Grid]]]:
    """Render every SVG with Chromium; returns {id: {size: (source palette, grid)}}."""
    from playwright.sync_api import sync_playwright

    out: dict[str, dict[int, tuple[list[str], Grid]]] = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content("<html><body></body></html>")
            for agent in ids:
                svg = (AVATARS / f"{agent}.svg").read_text(encoding="utf-8")
                out[agent] = {}
                for size in SIZES:
                    result = page.evaluate(RASTERISE_JS, {"svg": svg, "n": size, "k": SUPERSAMPLE})
                    out[agent][size] = (result["palette"], result["grid"])
        finally:
            browser.close()
    return out


def drop_orphans(grid: Grid) -> Grid:
    """Remove pixels that have no occupied 8-neighbour."""
    n = len(grid)
    out = [row[:] for row in grid]
    for y in range(n):
        for x in range(n):
            if grid[y][x] < 0:
                continue
            lonely = not any(
                0 <= y + dy < n and 0 <= x + dx < n and grid[y + dy][x + dx] >= 0
                for dy in (-1, 0, 1)
                for dx in (-1, 0, 1)
                if dy or dx
            )
            if lonely:
                out[y][x] = -1
    return out


def recolour(colour: str, hue: float) -> str:
    """Move a colour to the agent's hue, keeping its lightness. Outline and white stay."""
    if colour in (OUTLINE, WHITE):
        return colour
    r, g, b = (int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    h, lightness, s = colorsys.rgb_to_hls(r, g, b)
    new_hue = (hue + (h * 360 - SOURCE_HUE) * HUE_SPREAD) % 360 / 360
    r, g, b = colorsys.hls_to_rgb(new_hue, lightness, min(1.0, s * SATURATION_BOOST))
    return f"#{round(r * 255):02X}{round(g * 255):02X}{round(b * 255):02X}"


# ---------------------------------------------------------------------------------------------
# Motion helpers. Each returns a new grid; cells moved outside the grid are dropped.
# ---------------------------------------------------------------------------------------------


def blank(n: int) -> Grid:
    """An empty n x n grid."""
    return [[-1] * n for _ in range(n)]


def shift(grid: Grid, dx: int, dy: int) -> Grid:
    """Move the whole sprite."""
    n = len(grid)
    out = blank(n)
    for y in range(n):
        for x in range(n):
            if grid[y][x] >= 0 and 0 <= y + dy < n and 0 <= x + dx < n:
                out[y + dy][x + dx] = grid[y][x]
    return out


def shear(grid: Grid, offset: Callable[[int], int]) -> Grid:
    """Move each row sideways by offset(row)."""
    n = len(grid)
    out = blank(n)
    for y in range(n):
        dx = offset(y)
        for x in range(n):
            if grid[y][x] >= 0 and 0 <= x + dx < n:
                out[y][x + dx] = grid[y][x]
    return out


def scale(grid: Grid, sx: float, sy: float, ax: float = 0.5, ay: float = 1.0) -> Grid:
    """Nearest-neighbour scale about the anchor (ax, ay), given as fractions of the grid."""
    n = len(grid)
    out = blank(n)
    for y in range(n):
        for x in range(n):
            src_y = round(ay * n + (y - ay * n) / sy)
            src_x = round(ax * n + (x - ax * n) / sx)
            if 0 <= src_y < n and 0 <= src_x < n:
                out[y][x] = grid[src_y][src_x]
    return out


def region_shift(grid: Grid, y0: int, y1: int, dx: int, dy: int) -> Grid:
    """Move only the rows y0..y1 (exclusive)."""
    n = len(grid)
    out = [row[:] for row in grid]
    for y in range(y0, y1):
        out[y] = [-1] * n
    for y in range(y0, y1):
        for x in range(n):
            if grid[y][x] >= 0 and 0 <= x + dx < n and 0 <= y + dy < n:
                out[y + dy][x + dx] = grid[y][x]
    return out


def blink(grid: Grid, palette: list[str]) -> Grid:
    """Fill the white eyes with the surrounding body colour."""
    n = len(grid)
    white = palette.index(WHITE) if WHITE in palette else -2
    out = [row[:] for row in grid]
    for y in range(n):
        for x in range(n):
            if grid[y][x] != white:
                continue
            near = [
                grid[y + dy][x + dx]
                for dy in range(-2, 3)
                for dx in range(-2, 3)
                if 0 <= y + dy < n and 0 <= x + dx < n and grid[y + dy][x + dx] not in (-1, white)
            ]
            near = [c for c in near if palette[c] != OUTLINE] or near
            if near:
                out[y][x] = max(sorted(set(near)), key=near.count)
    return out


def idle_frames(agent: str, grid: Grid, palette: list[str]) -> list[Grid]:
    """Four idle frames, one second loop, each agent with its own motion.

    Frame 0 is always the plain rest pose: it doubles as the still frame (reduced motion, pause,
    offscreen), so it must read as the approved avatar, never as a mid-motion distortion.
    """
    n = len(grid)
    if agent == "support":  # sways side to side
        return [
            grid,
            shear(grid, lambda y: round(1.6 * (1 - y / n))),
            grid,
            shear(grid, lambda y: round(-1.6 * (1 - y / n))),
        ]
    if agent == "knowledge":  # floats, then blinks
        return [grid, shift(grid, 0, -1), shift(blink(grid, palette), 0, -2), shift(grid, 0, -1)]
    if agent == "orders":  # hops and lands
        return [
            grid,
            shift(scale(grid, 0.96, 1.07), 0, -2),
            shift(grid, 0, -4),
            shift(scale(grid, 0.97, 1.05), 0, -1),
        ]
    if agent == "tickets":  # flutters its wings
        return [
            grid,
            scale(grid, 1.1, 0.95, 0.5, 0.5),
            grid,
            scale(grid, 0.92, 1.06, 0.5, 0.5),
        ]
    if agent == "ecommerce":  # breathes in and out
        return [
            grid,
            scale(grid, 1.04, 1.04, 0.5, 0.55),
            scale(grid, 1.08, 1.08, 0.5, 0.55),
            scale(grid, 1.04, 1.04, 0.5, 0.55),
        ]
    if agent == "invoices":  # stamps down
        return [grid, shift(grid, 0, -2), shift(scale(grid, 1.05, 0.93), 0, 1), grid]
    if agent == "assistant":  # bounces like a drop
        return [grid, scale(grid, 0.97, 1.06), grid, scale(grid, 1.04, 0.94)]
    if agent == "hr":  # nods
        head = int(n * 0.55)
        return [
            grid,
            region_shift(grid, 0, head, 0, 1),
            region_shift(grid, 0, head, 0, 2),
            region_shift(grid, 0, head, 0, 1),
        ]
    if agent == "email":  # wiggles
        return [grid, shift(grid, 1, 0), grid, shift(grid, -1, 0)]
    if agent == "subscriptions":  # orbits (renewal loop)
        return [grid, shift(grid, 1, -1), shift(grid, 0, -2), shift(grid, -1, -1)]
    if agent == "bookings":  # wiggles its ears
        ears = int(n * 0.32)
        return [
            grid,
            region_shift(grid, 0, ears, -1, 0),
            shift(grid, 0, -2),
            region_shift(grid, 0, ears, 1, 0),
        ]
    raise ValueError(f"no idle motion for {agent}")


# ---------------------------------------------------------------------------------------------
# Encoding and output
# ---------------------------------------------------------------------------------------------


def encode_row(row: list[int]) -> str:
    """Run-length encode one row: '.' transparent, 'a'..'l' palette 0..11, then the count."""
    parts: list[str] = []
    x = 0
    while x < len(row):
        end = x
        while end + 1 < len(row) and row[end + 1] == row[x]:
            end += 1
        parts.append(("." if row[x] < 0 else chr(97 + row[x])) + str(end - x + 1))
        x = end + 1
    return "".join(parts)


def encode_frame(grid: Grid) -> str:
    """Rows joined by '/'."""
    return "/".join(encode_row(row) for row in grid)


def build_agent(agent: str, sources: dict[int, tuple[list[str], Grid]]) -> dict:
    """Palette plus four encoded idle frames per grid size for one agent."""
    source_palette = sources[SIZES[0]][0]
    frames: dict[int, list[Grid]] = {}
    for size in SIZES:
        palette, grid = sources[size]
        if palette != source_palette:
            raise SystemExit(f"{agent}: palette differs between grid sizes")
        frames[size] = idle_frames(agent, drop_orphans(grid), source_palette)
    used = sorted({c for fs in frames.values() for f in fs for row in f for c in row if c >= 0})
    # Recolour, then merge entries that became the same colour.
    final: list[str] = []
    remap: dict[int, int] = {}
    for index in used:
        colour = recolour(source_palette[index], HUE[agent])
        if colour not in final:
            final.append(colour)
        remap[index] = final.index(colour)
    if len(final) > MAX_PALETTE:
        raise SystemExit(f"{agent}: {len(final)} palette entries, at most {MAX_PALETTE}")
    remap[-1] = -1
    idle = {
        str(size): [encode_frame([[remap[c] for c in row] for row in f]) for f in frames[size]]
        for size in SIZES
    }
    return {"palette": final, "idle": idle}


HEADER = """/**
 * Pixel avatar idle frames. GENERATED by `python -m scripts.pixelate_avatars`; do not edit by hand.
 * `python -m scripts.pixelate_avatars --check` fails when this file differs from a fresh run.
 *
 * Format
 * - `palette`: up to 12 colours, upper-case `#RRGGBB`; index 0 is the letter `a`, 11 is `l`.
 * - `idle["32" | "48"]`: four frames of a one-second loop, in order; each grid is that many cells
 *   wide and high (see `PIXEL_GRIDS`).
 * - A frame is one string of rows separated by `/`, top row first.
 * - A row is run-length encoded as tokens `<symbol><count>`: the symbol is `.` for a transparent
 *   cell or a lower-case letter `a`..`l` for a palette index, the count is a decimal integer >= 1.
 *   Every row adds up to the grid width. Example, width 8: `.2a3b2.1` is two transparent cells,
 *   three of palette 0, two of palette 1, one transparent.
 * - The navy outline `#15203A` and the white eyes `#FFFFFF` are in every palette.
 */
"""


def render(data: dict[str, dict]) -> str:
    """The text of lib/pixel-avatars-data.ts."""
    lines = [
        HEADER.rstrip("\n"),
        'import type { AgentId } from "@/lib/agent-avatars";',
        "",
        'export const PIXEL_GRIDS = { "32": 32, "48": 48 } as const;',
        "",
        "export const PIXEL_DATA: Record<",
        "  AgentId,",
        '  { palette: string[]; idle: Record<"32" | "48", string[]> }',
        "> = {",
    ]
    for agent, entry in data.items():
        lines.append(f"  {agent}: {{")
        lines.append(f"    palette: {json.dumps(entry['palette'])},")
        lines.append("    idle: {")
        for size in ("32", "48"):
            lines.append(f'      "{size}": [')
            lines.extend(f"        {json.dumps(frame)}," for frame in entry["idle"][size])
            lines.append("      ],")
        lines.append("    },")
        lines.append("  },")
    lines.append("};")
    return "\n".join(lines) + "\n"


def generate() -> str:
    """Rasterise, convert and render the whole data module."""
    ids = agent_ids()
    rendered = rasterise(ids)
    return render({agent: build_agent(agent, rendered[agent]) for agent in ids})


def main(argv: list[str] | None = None) -> int:
    """Write the data module, or with --check compare it with a fresh generation."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail if the committed file differs")
    args = parser.parse_args(argv)
    text = generate()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != text:
            print(
                f"{OUTPUT} is out of date; run python -m scripts.pixelate_avatars", file=sys.stderr
            )
            return 1
        print(f"{OUTPUT.name} is up to date ({len(text.encode())} bytes)")
        return 0
    OUTPUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT} ({len(text.encode())} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
