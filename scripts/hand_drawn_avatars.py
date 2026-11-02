"""Hand-drawn pixel sources for agents drawn cell by cell, not rasterised from vector art.

`scripts/pixelate_avatars.py` rasterises the other eleven agents from their SVGs with Chromium.
The E-invoicing avatar (a receipt with a seal) is drawn directly on the 32 and 48 grids, so the
generator needs no browser for it and its output is the same on every machine. The SVG in
`public/fourstacks/agents/einvoicing.svg` (and the ui-guide copy) is drawn from the 48 grid below
by `pixelate_avatars.hand_drawn_svg`; a test keeps them equal.

Letters are source colours, in the blue family every source SVG uses (the generator moves them to
the agent's hue): `l` lightest, `a` light, `m` main, `d` dark, `s` darkest, `o` the navy outline,
`w` the white of the eyes, `.` transparent. The 32 and 48 grids share one source palette.
"""

from __future__ import annotations

SOURCE_COLOURS: dict[str, str] = {
    "l": "#94B8FF",
    "a": "#7FA2FF",
    "m": "#2F58E0",
    "d": "#1A35A3",
    "s": "#162E85",
    "o": "#15203A",
    "w": "#FFFFFF",
}

# fmt: off
EINVOICING_32 = (
    "................................",
    "................................",
    "................................",
    "........lllllllllld.............",
    "......laaaaaaammmmld............",
    ".....laaaaaaammmmmlld...........",
    ".....laaaaaaammmmmllld..........",
    ".....laaaaaammmmmmlllld.........",
    ".....laaaaaammmmmmllllld........",
    ".....laaaaammmmmmmmmdddd........",
    ".....laaaaoommmmmoomdddd........",
    ".....laaaowwommmowwodddd........",
    ".....laaaowwommmowwodddd........",
    ".....laaamoommmmmooddddd........",
    ".....laammmmmmmmmddddddd........",
    ".....laalllllllllllldddd........",
    ".....lammmmmmmmmddddddds........",
    ".....lmmllllllllddddddds........",
    ".....lmmmmmmmmmdddddddss........",
    ".....lmmmmlllmddddddddsslll.....",
    ".....lmmmllllsdddddddsss.lll....",
    ".....lmmllllssldddddssss...ll...",
    ".....lmmlslsslldddddssss........",
    ".....lmmllsslllddddsssss........",
    ".....lmmmllllldddddsssss........",
    ".....lmmmmllldddddssssss........",
    ".....lmmmmdddddddsssssss........",
    ".....lmmmmdddddddsssssss........",
    ".....lmm.mdd.ddd.sss.sss........",
    "......m...d...d...s...s.........",
    "................................",
    "................................",
)

EINVOICING_48 = (
    "................................................",
    "................................................",
    "................................................",
    "................................................",
    "................................................",
    "..........lllllllllllllllllmd...................",
    ".........aaaaaaaaaaaammmmmmmld..................",
    "........laaaaaaaaaaammmmmmmmlld.................",
    "........laaaaaaaaaaammmmmmmmllld................",
    "........laaaaaaaaaammmmmmmmmlllld...............",
    "........laaaaaaaaaammmmmmmmmllllld..............",
    "........laaaaaaaaammmmmmmmmmlllllld.............",
    "........laaaaaaooommmmmmmooommmdddd.............",
    "........laaaaaooooommmmmooooommdddd.............",
    "........laaaaoowwwoommmoowwwooddddd.............",
    "........laaaaoowwwoommmoowwwooddddd.............",
    "........laaaaoowwwoommmoowwwooddddd.............",
    "........laaaaaooooommmmmooooodddddd.............",
    "........laaaaamooommmmmmmoooddddddd.............",
    "........laaaammmmmmmmmmmmmmdddddddd.............",
    "........laaaammmmmmmmmmmmmmdddddddd.............",
    "........laaalmmmmmmmmmmmmmddddddddd.............",
    "........laaallllllllllllllllllddddd.............",
    "........laammmmmmmmmmmmmmdddddddddd.............",
    "........lammmmmmmmmmmmmmddddddddddd.............",
    "........lammllllllllllllddddddddddd.............",
    "........lmmmmmmmmmmmmmmddddddddddds.............",
    "........lmmmmmmmmmmmmmdddddddddddss.............",
    "........lmmmmmmmmmlllllldddddddddlllll..........",
    "........lmmmmmmmmlllllllldddddddssllllll........",
    "........lmmmmmmmlllllllldlddddddsss..llll.......",
    "........lmmmmmmmllllllldlldddddssss....ll.......",
    "........lmmmmmmmlldlllddllldddsssss.....l.......",
    "........lmmmmmmmllddlddlllldddsssss.............",
    "........lmmmmmmmllldddlllldddssssss.............",
    "........lmmmmmmmlllldllllldddssssss.............",
    "........lmmmmmmmmlllllllldddsssssss.............",
    "........lmmmmmmmmdlllllldddssssssss.............",
    "........lmmmmmmmddddlldddddssssssss.............",
    "........lmmmmmmdddddddddddsssssssss.............",
    ".........mmmmm..ddddd..ddds..sssss..............",
    ".........mmmmm..ddddd..ddds...ssss..............",
    "..........mmm....ddd....dds....sss..............",
    "...........m......d......d......s...............",
    "................................................",
    "................................................",
    "................................................",
    "................................................",
)
# fmt: on

HAND_DRAWN: dict[str, dict[int, tuple[str, ...]]] = {
    "einvoicing": {32: EINVOICING_32, 48: EINVOICING_48},
}
