"""Convert the twelve agent avatars into pixel-art idle and working frames.

Run ``python -m scripts.pixelate_avatars`` to rewrite ``lib/pixel-avatars-data.ts`` in the web
console, or add ``--check`` to fail when the committed file differs from a fresh generation.

Eleven avatars come from vector art. The twelfth (``einvoicing``) is drawn cell by cell in
``scripts/hand_drawn_avatars.py``; it skips steps 1 and 2 (no browser), then joins the pipeline.

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

from scripts.hand_drawn_avatars import HAND_DRAWN, SOURCE_COLOURS

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "CSA_WebConsole" / "csa-console" / "web"
AVATARS = WEB / "public" / "fourstacks" / "agents"
AGENT_IDS_TS = WEB / "lib" / "agent-avatars.ts"
UI_GUIDE_AGENTS = ROOT / "ui-guide" / "assets" / "agents"
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
    # The largest free arc: 25 degrees from lime (95) and from green (145); the receipt shape and
    # its seal, not the hue alone, make this avatar recognisable.
    "einvoicing": 120,
}
# Title of each hand-drawn agent (the same text as AGENT_TITLES in lib/agent-avatars.ts).
AGENT_TITLE = {"einvoicing": "E-invoicing agent"}
SOURCE_HUE = 215
KNOWLEDGE_EYE = (0.35, 0.4, 0.65, 0.7)  # its blink region: x0, y0, x1, y1 as grid fractions
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


def hand_drawn_sources(agent: str) -> dict[int, tuple[list[str], Grid]]:
    """The hand-drawn grids of one agent as {size: (source palette, grid)}, with no browser."""
    letters = list(SOURCE_COLOURS)
    palette = [SOURCE_COLOURS[letter] for letter in letters]
    sources: dict[int, tuple[list[str], Grid]] = {}
    for size in SIZES:
        art = HAND_DRAWN[agent][size]
        if len(art) != size or any(len(row) != size for row in art):
            raise SystemExit(f"{agent}: the hand-drawn {size} grid is not {size} x {size}")
        unknown = set("".join(art)) - set(letters) - {"."}
        if unknown:
            raise SystemExit(f"{agent}: unknown letters {sorted(unknown)} in the {size} grid")
        grid = [[letters.index(c) if c != "." else -1 for c in row] for row in art]
        sources[size] = (palette, grid)
    return sources


def hand_drawn_svg(agent: str) -> str:
    """The source SVG of a hand-drawn agent: one rect per run of cells on the 48 grid."""
    art = HAND_DRAWN[agent][48]
    title = f"{AGENT_TITLE[agent]} avatar"
    rects: list[str] = []
    for y, row in enumerate(art):
        x = 0
        while x < len(row):
            end = x
            while end + 1 < len(row) and row[end + 1] == row[x]:
                end += 1
            if row[x] != ".":
                fill = SOURCE_COLOURS[row[x]]
                rects.append(
                    f'<rect x="{x}" y="{y}" width="{end - x + 1}" height="1" fill="{fill}"/>'
                )
            x = end + 1
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="160" height="160" '
        f'shape-rendering="crispEdges" role="img" aria-labelledby="{agent}-t">'
        f'<title id="{agent}-t">{title}</title>{"".join(rects)}</svg>\n'
    )


def rasterise(ids: list[str]) -> dict[str, dict[int, tuple[list[str], Grid]]]:
    """Render every SVG with Chromium; returns {id: {size: (source palette, grid)}}.

    Hand-drawn agents are read from ``scripts/hand_drawn_avatars.py`` instead.
    """
    from playwright.sync_api import sync_playwright

    out: dict[str, dict[int, tuple[list[str], Grid]]] = {
        agent: hand_drawn_sources(agent) for agent in ids if agent in HAND_DRAWN
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content("<html><body></body></html>")
            for agent in (a for a in ids if a not in HAND_DRAWN):
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


def blink(grid: Grid, palette: list[str], box: tuple[float, float, float, float]) -> Grid:
    """Fill the white eyes with the surrounding body colour.

    Only white cells inside ``box`` (x0, y0, x1, y1 as fractions of the grid) close, so white
    details elsewhere (the knowledge antenna tips) stay lit.
    """
    n = len(grid)
    white = palette.index(WHITE) if WHITE in palette else -2
    out = [row[:] for row in grid]
    x0, y0, x1, y1 = (round(f * n) for f in box)
    for y in range(y0, y1):
        for x in range(x0, x1):
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
    if agent == "knowledge":  # floats, then blinks (the eye only; the antenna tips stay white)
        closed = blink(grid, palette, KNOWLEDGE_EYE)
        return [grid, shift(grid, 0, -1), shift(closed, 0, -2), shift(grid, 0, -1)]
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
    if agent == "invoices":  # lifts, stamps down, rebounds
        return [grid, shift(grid, 0, -2), shift(scale(grid, 1.05, 0.93), 0, 1), shift(grid, 0, -1)]
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
    if agent == "bookings":  # wiggles its ears: left, a hop with the ears flopping down, right
        ears = int(n * 0.32)
        return [
            grid,
            region_shift(grid, 0, ears, -1, 0),
            shift(region_shift(grid, 0, ears, 0, 1), 0, -2),
            region_shift(grid, 0, ears, 1, 0),
        ]
    if agent == "einvoicing":  # the paper hem flutters, with one little hop
        hem = int(n * 0.84)
        return [
            grid,
            region_shift(grid, hem, n, 1, 0),
            shift(grid, 0, -1),
            region_shift(grid, hem, n, -1, 0),
        ]
    raise ValueError(f"no idle motion for {agent}")


# ---------------------------------------------------------------------------------------------
# Working loop (48 grid only): eight frames, the creature at work with one small prop.
#
# Props are hand-drawn stamps: tuples of equal-length strings, top row first. Letters:
#   "."  transparent (the creature shows through)
#   "o"  the navy outline          "w"  white
#   "h"  the agent's lightest tone "m"  its main body tone (most cells)  "d"  its darkest tone
# so every prop is drawn only in the agent's own palette plus the outline and white.
# Stamps are placed at absolute 48-grid cells over the creature (drawn in front of it).
# Frame 0 and frame 7 keep the creature at (or one pixel from) its rest pose, so a `wake`
# that ends on frame 7 hands over to the idle loop without a jump.
# ---------------------------------------------------------------------------------------------

WORK_GRID = 48
WORK_FRAMES = 8
STAMP_LETTERS = frozenset(".owhmd")

Stamp = tuple[str, ...]
Place = tuple[str, int, int]  # stamp name, x, y


def lightness(colour: str) -> float:
    """HLS lightness of #RRGGBB."""
    r, g, b = (int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5))
    return colorsys.rgb_to_hls(r, g, b)[1]


def roles(grid: Grid, palette: list[str]) -> dict[str, int]:
    """Palette index for each stamp letter, chosen from the tones the rest pose uses."""
    counts = [0] * len(palette)
    for row in grid:
        for cell in row:
            if cell >= 0:
                counts[cell] += 1
    body = [i for i, c in enumerate(palette) if c not in (OUTLINE, WHITE) and counts[i]]
    return {
        "o": palette.index(OUTLINE),
        "w": palette.index(WHITE),
        "h": max(body, key=lambda i: (lightness(palette[i]), -i)),
        "m": max(body, key=lambda i: (counts[i], -i)),
        "d": min(body, key=lambda i: (lightness(palette[i]), i)),
    }


def stamp(grid: Grid, art: Stamp, x: int, y: int, role: dict[str, int]) -> Grid:
    """Draw a prop stamp over the grid with its top-left cell at (x, y)."""
    n = len(grid)
    out = [row[:] for row in grid]
    for dy, line in enumerate(art):
        for dx, letter in enumerate(line):
            if letter != "." and 0 <= y + dy < n and 0 <= x + dx < n:
                out[y + dy][x + dx] = role[letter]
    return out


def box_shift(
    grid: Grid, box: tuple[int, int, int, int], dx: int, dy: int, colours: set[int]
) -> Grid:
    """Move the cells of the given colours inside box (x0, y0, x1, y1, end exclusive).

    Used to make a tendril reach: only that limb's tone moves, the body around it stays.
    """
    x0, y0, x1, y1 = box
    n = len(grid)
    out = [row[:] for row in grid]
    moved = []
    for y in range(y0, y1):
        for x in range(x0, x1):
            if grid[y][x] in colours:
                out[y][x] = -1
                moved.append((x + dx, y + dy, grid[y][x]))
    for x, y, cell in moved:
        if 0 <= x < n and 0 <= y < n:
            out[y][x] = cell
    return out


def lean(grid: Grid, top: int) -> Grid:
    """Lean sideways: the top row moves by `top` cells, the bottom row stays."""
    n = len(grid)
    return shear(grid, lambda y: round(top * (1 - y / n)))


# fmt: off
PROPS: dict[str, dict[str, Stamp]] = {
    # support: a speech card above its right ear; typing dots fill in, then the reply appears.
    "support": {
        "card": (
            ".oooooooooo.",
            "owwwwwwwwwwo",
            "owwwwwwwwwwo",
            "owwwwwwwwwwo",
            "owwwwwwwwwwo",
            ".owooooooooo",
            ".oo.........",
            "oo..........",
        ),
        "dot": ("..", "..", "mm", "mm"),
        "dot_up": ("..", "mm", "mm", "ww"),
        "reply": (
            "............",
            "............",
            "..dddddddd..",
            "............",
            "..ddddd.....",
        ),
    },
    # knowledge: an open book held by its right tendril; a page lifts, turns over and lands.
    "knowledge": {
        "book": (
            "..............",
            "..............",
            "..............",
            "..............",
            ".ooooo..ooooo.",
            "owwwwwoowwwwwo",
            "owdddwoowdddwo",
            "owwwwwoowwwwwo",
            "owddwwoowddwwo",
            "owwwwwoowwwwwo",
            "ommmmmoommmmmo",
            ".oooooooooooo.",
        ),
        "book_next": (
            "..............",
            "..............",
            "..............",
            "..............",
            ".ooooo..ooooo.",
            "owwwwwoowwwwwo",
            "owddwwoowdddwo",
            "owwwwwoowwwwwo",
            "owdddwoowddwwo",
            "owwwwwoowwwwwo",
            "ommmmmoommmmmo",
            ".oooooooooooo.",
        ),
        "page_lift": (
            "..............",
            "...........oo.",
            ".........oowo.",
            "........owwwo.",
            ".ooooo.owwwo..",
            "owwwwwoowwwwwo",
            "owdddwoowwwwwo",
            "owwwwwoowwwwwo",
            "owddwwoowwwwwo",
            "owwwwwoowwwwwo",
            "ommmmmoommmmmo",
            ".oooooooooooo.",
        ),
        "page_up": (
            "......ooo.....",
            "......owo.....",
            "......owo.....",
            "......owo.....",
            ".oooooowoooooo",
            "owwwwwoowwwwwo",
            "owdddwoowwwwwo",
            "owwwwwoowwwwwo",
            "owddwwoowwwwwo",
            "owwwwwoowwwwwo",
            "ommmmmoommmmmo",
            ".oooooooooooo.",
        ),
        "page_fall": (
            "..............",
            ".oo...........",
            ".owoo.........",
            ".owwwo........",
            "..owwwo.ooooo.",
            "owwwwwoowwwwwo",
            "owdddwoowwwwwo",
            "owwwwwoowwwwwo",
            "owddwwoowwwwwo",
            "owwwwwoowwwwwo",
            "ommmmmoommmmmo",
            ".oooooooooooo.",
        ),
    },
    # orders: an order card above its head; it hops and ticks the three lines one by one.
    "orders": {
        "card": (
            ".oooooooo.",
            "ommmmmmmmo",
            "owwwwwwwwo",
            "owwwwwwwwo",
            "owwwwddddo",
            "owwwwwwwwo",
            "owwwwwwwwo",
            "owwwwwwwwo",
            "owwwwddddo",
            "owwwwwwwwo",
            "owwwwwwwwo",
            "owwwwwwwwo",
            "owwwwddddo",
            "owwwwwwwwo",
            ".oooooooo.",
        ),
        "tick": ("..d", "d.d", ".d."),
    },
    # tickets: a ticket stack; it brings a rubber stamp down on the top ticket and lifts it.
    "tickets": {
        "stack": (
            "..oooooooooo",
            "..ohhhhhhhho",
            "oooooooooooo",
            "owwdwwwwwwwo",
            ".owwwwwwwwo.",
            "owwdwwwwwwwo",
            ".owwwwwwwwo.",
            "owwdwwwwwwwo",
            "oooooooooooo",
        ),
        "mark": ("dddd", "d..d", "dddd"),
        "tool": (
            "..oo..",
            ".oddo.",
            ".oddo.",
            "oooooo",
            "ommmmo",
            "oooooo",
        ),
    },
    # ecommerce: a parcel; the flaps fold shut and a strip of tape goes over and down.
    "ecommerce": {
        "open": (
            "oo.......oo",
            "oho.....oho",
            ".oho...oho.",
            "..ooooooo..",
            "ooooooooooo",
            "owwwwwwwwwo",
            "owwwwwwwwwo",
            "owwwwwwwwwo",
            "owwwwwwwwwo",
            "owwwwwwwwwo",
            "ooooooooooo",
        ),
        "folding": (
            "...........",
            "...........",
            ".ooo...ooo.",
            "ohhhoooohho",
            "ooooooooooo",
            "owwwwwwwwwo",
            "owwwwwwwwwo",
            "owwwwwwwwwo",
            "owwwwwwwwwo",
            "owwwwwwwwwo",
            "ooooooooooo",
        ),
        "closed": (
            "...........",
            "...........",
            "...........",
            "ooooooooooo",
            "ohhhhhhhhho",
            "ooooooooooo",
            "owwwwwwwwwo",
            "owwwwwwwwwo",
            "owwwwwwwwwo",
            "owwwwwwwwwo",
            "ooooooooooo",
        ),
        "tape_top": ("...........", "...........", "...........", "....ddd....", "....ddd...."),
        "tape_half": (
            "...........", "...........", "...........", "....ddd....", "....ddd....",
            "....ddd....", "....ddd....", "....ddd....",
        ),
        "tape_full": (
            "...........", "...........", "...........", "....ddd....", "....ddd....",
            "....ddd....", "....ddd....", "....ddd....", "....ddd....", "....ddd....",
            "....ooo....",
        ),
    },
    # invoices: an invoice sheet; it presses on it with its right tendril and a tick appears.
    "invoices": {
        "sheet": (
            "ooooooo...",
            "owwwwwoo..",
            "odddwwowo.",
            "owwwwwoooo",
            "owmmmmmmwo",
            "owwwwwwwwo",
            "owmmmmwwwo",
            "owwwwwwwwo",
            "owwwwwwwwo",
            "owwwwwwwwo",
            "owwwwwwwwo",
            "oooooooooo",
        ),
        "tick1": ("......", "......", "d.....", "dd....", ".d...."),
        "tick2": ("....dd", "...dd.", "d.dd..", "dddd..", ".dd..."),
    },
    # assistant: a proposal card held up by its right tendril; it rises, glows and sparkles.
    "assistant": {
        "card": (
            ".oooooooooo.",
            "owwwwwwwwwwo",
            "owwwmwwwwwwo",
            "owwmmmwddwwo",
            "owwwmwwwwwwo",
            "owwwwwwwwwwo",
            "owddddddddwo",
            "owwwwwwwwwwo",
            "owddddddwwwo",
            ".oooooooooo.",
        ),
        "glow": (
            "..hhhhhhhhhh..",
            ".h..........h.",
            "h............h",
            "h............h",
            "h............h",
            "h............h",
            "h............h",
            "h............h",
            "h............h",
            "h............h",
            ".h..........h.",
            "..hhhhhhhhhh..",
        ),
        "sparkle": (".m.", "mwm", ".m."),
    },
    # hr: two name cards; it pulls the back card up and over, so the cards swap places.
    "hr": {
        "card_a": (
            "oooooooo",
            "owwwwwwo",
            "owwmmwwo",
            "owwmmwwo",
            "owmmmmwo",
            "owwwwwwo",
            "owddddwo",
            "owwwwwwo",
            "oooooooo",
        ),
        "card_b": (
            "oooooooo",
            "owwwwwwo",
            "owwddwwo",
            "owwddwwo",
            "owddddwo",
            "owwwwwwo",
            "owmmmmwo",
            "owwwwwwo",
            "oooooooo",
        ),
    },
    # email: two trays below it; a letter rises out of the left tray and drops into the right.
    "email": {
        "trays": (
            "o......o...o......o",
            "o......o...o......o",
            "ommmmmmo...ommmmmmo",
            "ommmmmmo...ommmmmmo",
            "oooooooo...oooooooo",
        ),
        "letter": (
            "oooooo",
            "oowwoo",
            "owoowo",
            "owwwwo",
            "oooooo",
        ),
    },
    # subscriptions: a renewal dial in the hollow of its crescent; the hand goes round once
    # and a renewal arrow runs round the right of the dial.
    "subscriptions": {
        "dial": (
            "...ooooo......",
            ".oowwwwwoo....",
            ".owwwwwwwo.d..",
            "owwwwwwwwwo.d.",
            "owwwwwwwwwo.d.",
            "owwwwowwwwo.d.",
            "owwwwwwwwwo.d.",
            "owwwwwwwwwoddd",
            ".owwwwwwwo..d.",
            ".oowwwwwoo....",
            "...ooooo......",
        ),
    },
    # bookings: a mini calendar held by its right tendril; it picks a day and ticks it.
    "bookings": {
        "calendar": (
            "..o.....o..",
            "ooooooooooo",
            "ommmmmmmmmo",
            "ommmmmmmmmo",
            "ooooooooooo",
            "owwwwwwwwwo",
            "owhwwhwwhwo",
            "owwwwwwwwwo",
            "owhwwhwwhwo",
            "owwwwwwwwwo",
            "ooooooooooo",
        ),
        "pick": ("mmm", "mwm", "mmm"),
        "tick1": ("......", "......", "o.....", "oo....", ".o...."),
        "tick2": ("....oo", "...oo.", "o.oo..", "oooo..", ".oo..."),
    },
    # einvoicing: a receipt slip on the ground and a round seal stamp held in its right tendril;
    # the stamp comes down onto the slip and lifts off, leaving a ring (the seal) behind.
    "einvoicing": {
        "slip": (
            "ooooooooooooo",
            "owwwwwwwwwwwo",
            "owdddwwwwwwwo",
            "owwwwwwwwwwwo",
            "owddwwwwwwwwo",
            "owwwwwwwwwwwo",
            "owwwwwwwwwwwo",
            "ooooooooooooo",
        ),
        "mark": (
            ".mmm.",
            "m...m",
            "m.d.m",
            "m...m",
            ".mmm.",
        ),
        "stamp": (
            "..ooo..",
            ".ohhho.",
            ".ohhmo.",
            "..odo..",
            "..odo..",
            "ooooooo",
            "ommmmmo",
            "odddddo",
            "ooooooo",
        ),
    },
}
# fmt: on

# Clock hand cells for the renewal dial, relative to the dial's top-left, one list per frame.
DIAL_HANDS = (
    ((5, 2), (5, 3), (5, 4)),
    ((7, 3), (6, 4)),
    ((6, 5), (7, 5), (8, 5)),
    ((6, 6), (7, 7)),
    ((5, 6), (5, 7), (5, 8)),
    ((4, 6), (3, 7)),
    ((2, 5), (3, 5), (4, 5)),
    ((3, 3), (4, 4)),
)


def dial_hand(cells: tuple[tuple[int, int], ...]) -> Stamp:
    """An 11 x 11 stamp with only the clock hand cells (dark tone) set."""
    rows = [["."] * 11 for _ in range(11)]
    for x, y in cells:
        rows[y][x] = "d"
    return tuple("".join(row) for row in rows)


PROPS["subscriptions"].update({f"hand{i}": dial_hand(cells) for i, cells in enumerate(DIAL_HANDS)})


def tendril_reach(
    grid: Grid, role: dict[str, int], box: tuple[int, int, int, int], dx: int, dy: int
) -> Grid:
    """Move the light-tone limb pixels inside box (the reaching tendril)."""
    return box_shift(grid, box, dx, dy, {role["h"]})


def working_frames(agent: str, rest: Grid, palette: list[str]) -> list[Grid]:
    """Eight frames of the agent at work on the 48 grid, from its rest pose and final palette.

    Each agent returns the creature's eight poses and, per frame, the stamps drawn over it.
    """
    if len(rest) != WORK_GRID:
        raise ValueError("working frames are drawn on the 48 grid only")
    role = roles(rest, palette)
    poses, props = working_plan(agent, rest, palette, role)
    if len(poses) != WORK_FRAMES or len(props) != WORK_FRAMES:
        raise ValueError(f"{agent}: working loop must have {WORK_FRAMES} frames")
    art = PROPS[agent]
    frames = []
    for pose, places in zip(poses, props, strict=True):
        frame = pose
        for name, x, y in places:
            frame = stamp(frame, art[name], x, y, role)
        frames.append(frame)
    return frames


def working_plan(
    agent: str, g: Grid, palette: list[str], role: dict[str, int]
) -> tuple[list[Grid], list[list[Place]]]:
    """The creature's eight poses and, per frame, the prop stamps drawn over it."""
    if agent == "support":  # bobs while it types into the speech card, leans in to send
        tail = (34, 30, 48, 42)
        tap = tendril_reach(g, role, tail, 1, -1)
        poses = [
            g,
            shift(tap, 0, -1),
            tap,
            shift(tap, 0, -1),
            lean(g, 2),
            lean(g, 3),
            lean(g, 2),
            g,
        ]
        card = ("card", 35, 1)
        dot = [("dot", 37, 1), ("dot", 40, 1), ("dot", 43, 1)]
        props = [
            [card],
            [card, dot[0]],
            [card, dot[0], dot[1]],
            [card, *dot],
            [card, dot[0], ("dot_up", 40, 1), dot[2]],
            [card, dot[0], dot[1], ("dot_up", 43, 1)],
            [card, ("reply", 35, 1)],
            [card, ("reply", 35, 1)],
        ]
        return poses, props
    if agent == "knowledge":  # bows over the book, blinks, its antenna twitches at the page
        reading = region_shift(g, 0, 30, 0, 1)
        blinked = blink(reading, palette, KNOWLEDGE_EYE)
        antenna = (33, 4, 41, 15)
        twitch = box_shift(reading, antenna, 1, 0, {role["o"], role["w"], role["h"]})
        poses = [g, reading, blinked, reading, twitch, reading, shift(g, 0, -1), g]
        names = ["book", "book", "page_lift", "page_up", "page_fall"] + ["book_next"] * 3
        return poses, [[(name, 32, 34)] for name in names]
    if agent == "orders":  # crouches, hops up to tick each line, lands
        low = shift(g, 0, 3)
        crouch = shift(scale(g, 1.04, 0.95), 0, 3)
        poses = [low, crouch, g, low, g, crouch, g, shift(g, 0, 1)]
        card = ("card", 37, 1)
        tick = [("tick", 38, 4), ("tick", 38, 8), ("tick", 38, 12)]
        ticked = [0, 0, 1, 1, 2, 2, 3, 3]
        return poses, [[card, *tick[: ticked[i]]] for i in range(WORK_FRAMES)]
    if agent == "tickets":  # spreads its wings, lifts the stamp, brings it down, lifts again
        spread = scale(g, 1.06, 0.96, 0.5, 0.5)
        poses = [
            g,
            spread,
            shift(g, 0, -1),
            shift(lean(g, 2), 0, 1),
            lean(g, 1),
            g,
            scale(g, 0.94, 1.04, 0.5, 0.5),
            g,
        ]
        stack, mark = ("stack", 34, 35), ("mark", 39, 38)
        tool_y = [27, 25, 24, 32, 30, 28, 26, 27]
        marked = [False, False, False, True, True, True, True, True]
        props = []
        for i in range(WORK_FRAMES):
            props.append([stack, *([mark] if marked[i] else []), ("tool", 38, tool_y[i])])
        return poses, props
    if agent == "ecommerce":  # leans over the parcel; its tail pulls the tape along
        tail = (38, 31, 48, 37)
        pull = tendril_reach(g, role, tail, 1, 1)
        poses = [
            g,
            lean(g, 1),
            lean(g, 2),
            pull,
            shift(pull, 0, 1),
            pull,
            scale(g, 1.03, 1.03, 0.5, 0.55),
            g,
        ]
        at = 35, 34
        box = ["open", "open", "folding"] + ["closed"] * 5
        tape = [None, None, None, "tape_top", "tape_half", "tape_full", "tape_full", "tape_full"]
        props = []
        for i in range(WORK_FRAMES):
            props.append([(box[i], *at), *([(tape[i], *at)] if tape[i] else [])])
        return poses, props
    if agent == "invoices":  # lifts, presses down on the sheet, a tick appears, settles
        arm = (32, 26, 48, 38)
        press = tendril_reach(g, role, arm, 1, 1)
        poses = [
            g,
            shift(g, 0, -2),
            shift(press, 0, -1),
            shift(scale(press, 1.04, 0.95), 0, 1),
            press,
            shift(g, 0, -1),
            g,
            shift(g, 0, -1),
        ]
        sheet = ("sheet", 36, 34)
        tick = [None, None, None, "tick1", "tick2", "tick2", "tick2", "tick2"]
        return poses, [[sheet, *([(tick[i], 38, 40)] if tick[i] else [])] for i in range(8)]
    if agent == "assistant":  # bounces as it holds the proposal up; at the top it glows
        arm = (36, 27, 48, 37)
        hold = tendril_reach(g, role, arm, 0, -2)
        lift = tendril_reach(g, role, arm, 0, -3)
        poses = [
            hold,
            scale(hold, 0.98, 1.04),
            lift,
            scale(lift, 0.97, 1.06),
            lift,
            scale(hold, 1.03, 0.96),
            hold,
            g,
        ]
        card_y = [17, 16, 14, 13, 13, 14, 16, 17]
        props = [[("card", 35, y)] for y in card_y]
        props[3] += [("glow", 34, 12)]
        props[4] += [("glow", 34, 12), ("sparkle", 32, 9), ("sparkle", 44, 24)]
        props[5] += [("sparkle", 43, 9)]
        return poses, props
    if agent == "hr":  # nods at each card as it pulls the back one up and over the front one
        head = int(WORK_GRID * 0.55)
        nod1 = region_shift(g, 0, head, 0, 1)
        nod2 = region_shift(g, 0, head, 0, 2)
        poses = [g, nod1, lean(nod1, 1), nod2, g, nod1, lean(nod2, 1), g]
        front, back = (34, 37), (38, 34)
        # (card at the front, its place), (card behind, its place) per frame.
        steps = [
            ("card_a", front, "card_b", back),
            ("card_a", front, "card_b", (39, 31)),
            ("card_b", (36, 30), "card_a", front),
            ("card_b", (35, 34), "card_a", (36, 36)),
            ("card_b", front, "card_a", back),
            ("card_b", front, "card_a", (39, 31)),
            ("card_a", (36, 30), "card_b", front),
            ("card_a", (35, 34), "card_b", (36, 36)),
        ]
        props = [[(b, *pb), (a, *pa)] for a, pa, b, pb in steps]
        return poses, props
    if agent == "email":  # wiggles as it lifts a letter from one tray into the other
        up = shift(g, 0, -3)
        reach = tendril_reach(up, role, (33, 33, 40, 42), 1, 1)
        poses = [
            up,
            reach,
            shift(up, 1, 0),
            shift(up, 1, -1),
            shift(up, 0, -1),
            shift(up, -1, 0),
            reach,
            shift(g, 0, -2),
        ]
        letter = [(27, 40), (28, 36), (31, 33), (34, 32), (36, 33), (38, 36), (38, 40), (38, 40)]
        return poses, [[("letter", x, y), ("trays", 26, 42)] for x, y in letter]
    if agent == "subscriptions":  # rocks around the dial while its hand goes round once
        antenna = (32, 9, 41, 15)
        twitch = box_shift(g, antenna, 1, -1, {role["d"]})
        poses = [
            g,
            shift(g, 1, 0),
            shift(twitch, 1, -1),
            shift(g, 0, -1),
            shift(g, -1, -1),
            shift(twitch, -1, 0),
            shift(g, 0, 1),
            g,
        ]
        return poses, [[("dial", 27, 17), (f"hand{i}", 27, 17)] for i in range(WORK_FRAMES)]
    if agent == "bookings":  # wiggles its ears, moves along the days, taps the tick in
        ears = int(WORK_GRID * 0.32)
        arm = (37, 29, 48, 38)
        tap = tendril_reach(g, role, arm, 0, 1)
        poses = [
            g,
            region_shift(g, 0, ears, -1, 0),
            lean(g, 1),
            tap,
            region_shift(tap, 0, ears, 1, 0),
            lean(g, 1),
            shift(g, 0, -1),
            g,
        ]
        cal, pick = ("calendar", 35, 35), ("pick", 39, 40)
        tick1, tick2 = ("tick1", 38, 39), ("tick2", 38, 39)
        props = [
            [cal],
            [cal, ("pick", 36, 40)],
            [cal, pick],
            [cal, pick, tick1],
            [cal, pick, tick2],
            [cal, pick, tick2],
            [cal, pick, tick2],
            [cal, pick, tick2],
        ]
        return poses, props
    if agent == "einvoicing":  # leans in, brings the stamp down on the slip, lifts it off
        arm = (33, 26, 46, 36)
        up = tendril_reach(g, role, arm, 0, -1)
        fwd = tendril_reach(g, role, arm, 1, 1)
        down = tendril_reach(g, role, arm, 1, 3)
        poses = [
            g,
            lean(up, 1),
            lean(fwd, 2),
            shift(lean(down, 2), 0, 1),
            scale(down, 1.03, 0.96),
            lean(fwd, 1),
            lean(up, 1),
            g,
        ]
        slip, mark = ("slip", 35, 39), ("mark", 41, 40)
        stamp_y = [22, 26, 30, 33, 33, 29, 25, 23]
        props = []
        for i in range(WORK_FRAMES):
            places = [slip, *([mark] if i >= 4 else []), ("stamp", 40, stamp_y[i])]
            props.append(places)
        return poses, props
    raise ValueError(f"no working loop for {agent}")


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
    """Palette, four encoded idle frames per grid size and eight working frames (48 grid)."""
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
    idle = {size: [[[remap[c] for c in row] for row in f] for f in frames[size]] for size in SIZES}
    # The working loop is drawn in the final palette from the 48 rest pose (idle frame 0), so it
    # can be rebuilt from the committed data alone (tests/test_pixelate_avatars.py does).
    working = working_frames(agent, idle[WORK_GRID][0], final)
    return {
        "palette": final,
        "idle": {str(size): [encode_frame(f) for f in idle[size]] for size in SIZES},
        "working": {str(WORK_GRID): [encode_frame(f) for f in working]},
    }


HEADER = """/**
 * Pixel avatar frames. GENERATED by `python -m scripts.pixelate_avatars`; do not edit by hand.
 * `python -m scripts.pixelate_avatars --check` fails when this file differs from a fresh run.
 *
 * Format
 * - `palette`: up to 12 colours, upper-case `#RRGGBB`; index 0 is the letter `a`, 11 is `l`.
 * - `idle["32" | "48"]`: four frames of a one-second loop, in order; each grid is that many cells
 *   wide and high (see `PIXEL_GRIDS`). Frame 0 is the rest pose (the still frame).
 * - `working["48"]`: eight frames of the agent at work with one small prop, in order, on the 48
 *   grid only (a 32-grid avatar plays its idle loop instead). Frame 7 is close to the rest pose,
 *   so a one-shot wake that ends there hands over to the idle loop without a jump.
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
        "  {",
        "    palette: string[];",
        '    idle: Record<"32" | "48", string[]>;',
        '    working: Record<"48", string[]>;',
        "  }",
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
        lines.append("    working: {")
        lines.append(f'      "{WORK_GRID}": [')
        lines.extend(f"        {json.dumps(frame)}," for frame in entry["working"][str(WORK_GRID)])
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
