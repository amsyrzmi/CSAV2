"""The pixel avatar generator's motion and prop code against the committed data module.

`python -m scripts.pixelate_avatars --check` needs Chromium to rasterise the SVGs, so it is not
run here (the hand-drawn E-invoicing avatar needs no browser and is rebuilt below). These tests
need no browser: they start from the committed rest poses (idle frame 0) and rebuild every other
frame with the generator's own code. That proves frame 0 is the plain
rest pose the motions start from, and that the committed idle and working frames match the code.
"""

from __future__ import annotations

import json
import re

import pytest
from scripts import pixelate_avatars as pa

ENTRY = re.compile(
    r"  (\w+): \{\n    palette: (\[.*?\]),\n    idle: \{\n      \"32\": \[\n(.*?)      \],\n"
    r"      \"48\": \[\n(.*?)      \],\n    \},\n    working: \{\n      \"48\": \[\n(.*?)      \],",
    re.S,
)


def frames(block: str) -> list[str]:
    """The JSON strings of one frame list in the data module."""
    return [json.loads(line.strip().rstrip(",")) for line in block.strip().splitlines()]


def decode(frame: str) -> pa.Grid:
    """Rows of palette indices (-1 transparent) from the documented run-length format."""
    return [
        [-1 if s == "." else ord(s) - 97 for s, count in re.findall(r"([.a-l])(\d+)", row)
         for _ in range(int(count))]
        for row in frame.split("/")
    ]  # fmt: skip


def load() -> dict[str, dict]:
    """Every agent's palette and frames, parsed from lib/pixel-avatars-data.ts."""
    source = pa.OUTPUT.read_text(encoding="utf-8")
    data = {
        m.group(1): {
            "palette": json.loads(m.group(2)),
            "idle": {"32": frames(m.group(3)), "48": frames(m.group(4))},
            "working": {"48": frames(m.group(5))},
        }
        for m in ENTRY.finditer(source)
    }
    assert sorted(data) == sorted(pa.HUE), "the data module lists every agent once"
    return data


DATA = load()
AGENTS = sorted(DATA)


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("size", ["32", "48"])
def test_idle_frames_are_the_motions_applied_to_frame_0(agent: str, size: str) -> None:
    entry = DATA[agent]
    rest = decode(entry["idle"][size][0])
    rebuilt = pa.idle_frames(agent, rest, entry["palette"])
    # Frames 1-3 are the generator's motions applied to the stored frame 0; frame 0 being the
    # rest pose of the source art is what `python -m scripts.pixelate_avatars --check` proves.
    assert [pa.encode_frame(f) for f in rebuilt] == entry["idle"][size]


@pytest.mark.parametrize("agent", AGENTS)
def test_working_frames_are_rebuilt_from_the_48_rest_pose(agent: str) -> None:
    entry = DATA[agent]
    rest = decode(entry["idle"]["48"][0])
    rebuilt = pa.working_frames(agent, rest, entry["palette"])
    assert [pa.encode_frame(f) for f in rebuilt] == entry["working"]["48"]


@pytest.mark.parametrize("agent", AGENTS)
def test_working_frame_7_returns_close_to_the_rest_pose(agent: str) -> None:
    # A wake ends on frame 7 and the idle loop starts from the rest pose: at most 2 px apart.
    entry = DATA[agent]
    rest = decode(entry["idle"]["48"][0])
    role = pa.roles(rest, entry["palette"])
    poses, _ = pa.working_plan(agent, rest, entry["palette"], role)
    near = [pa.shift(rest, dx, dy) for dx in range(-2, 3) for dy in range(-2, 3)]
    assert poses[7] in near


def test_hand_drawn_avatar_rebuilds_the_committed_data_without_a_browser() -> None:
    assert sorted(pa.HAND_DRAWN) == ["einvoicing"]
    for agent in pa.HAND_DRAWN:
        built = pa.build_agent(agent, pa.hand_drawn_sources(agent))
        assert built == DATA[agent], f"{agent}: regenerate with python -m scripts.pixelate_avatars"


def test_hand_drawn_svg_is_the_48_grid_and_both_copies_agree() -> None:
    svg = pa.hand_drawn_svg("einvoicing")
    assert (pa.AVATARS / "einvoicing.svg").read_text(encoding="utf-8") == svg
    assert (pa.UI_GUIDE_AGENTS / "einvoicing.svg").read_text(encoding="utf-8") == svg
    # Every cell of the 48 art is covered exactly once by a rect of its own colour.
    cells: dict[tuple[int, int], str] = {}
    pattern = r'<rect x="(\d+)" y="(\d+)" width="(\d+)" height="1" fill="(#[0-9A-F]{6})"/>'
    rects = re.findall(pattern, svg)
    for x, y, width, fill in rects:
        for dx in range(int(width)):
            assert (int(x) + dx, int(y)) not in cells
            cells[(int(x) + dx, int(y))] = fill
    art = pa.HAND_DRAWN["einvoicing"][48]
    expected = {
        (x, y): pa.SOURCE_COLOURS[c]
        for y, row in enumerate(art)
        for x, c in enumerate(row)
        if c != "."
    }
    assert cells == expected


def test_hand_drawn_grids_share_the_receipt_and_seal_shape_across_sizes() -> None:
    # Both grids carry the two eyes (white cells), the seal's dark check and the lightest tendril.
    for size, art in pa.HAND_DRAWN["einvoicing"].items():
        flat = "".join(art)
        assert len(art) == size and all(len(row) == size for row in art)
        for letter in "lamdsow":
            assert letter in flat, f"{size}: missing {letter}"


def test_knowledge_blink_closes_the_eye_but_keeps_the_antenna_tips_white() -> None:
    entry = DATA["knowledge"]
    white = entry["palette"].index(pa.WHITE)
    for size in ("32", "48"):
        rest = decode(entry["idle"][size][0])
        closed = pa.blink(rest, entry["palette"], pa.KNOWLEDGE_EYE)
        n = len(rest)
        top = [cell for row in closed[: n // 3] for cell in row]
        middle = [cell for row in closed[n // 3 :] for cell in row]
        assert white in top, f"{size}: antenna tips stay white"
        assert white not in middle, f"{size}: the eye closes"


def test_prop_stamps_are_well_formed_and_small() -> None:
    for agent, stamps in pa.PROPS.items():
        assert agent in pa.HUE
        for name, art in stamps.items():
            assert art, f"{agent}.{name} is empty"
            assert len({len(row) for row in art}) == 1, f"{agent}.{name} rows differ in width"
            assert set("".join(art)) <= pa.STAMP_LETTERS, f"{agent}.{name} unknown letter"
            # About 12 x 12; the email trays are two trays side by side.
            limit = (19, 5) if (agent, name) == ("email", "trays") else (14, 15)
            assert len(art[0]) <= limit[0] and len(art) <= limit[1], f"{agent}.{name} too big"


def test_roles_pick_the_outline_white_and_three_body_tones() -> None:
    for agent in AGENTS:
        entry = DATA[agent]
        role = pa.roles(decode(entry["idle"]["48"][0]), entry["palette"])
        palette = entry["palette"]
        assert palette[role["o"]] == pa.OUTLINE
        assert palette[role["w"]] == pa.WHITE
        tones = {role["h"], role["m"], role["d"]}
        assert not tones & {role["o"], role["w"]}, agent
        assert pa.lightness(palette[role["h"]]) >= pa.lightness(palette[role["m"]])
        assert pa.lightness(palette[role["m"]]) >= pa.lightness(palette[role["d"]])


def test_hand_drawn_titles_are_the_ones_the_console_shows() -> None:
    source = (pa.WEB / "lib" / "agent-avatars.ts").read_text(encoding="utf-8")
    block = re.search(r"AGENT_TITLES[^=]*=\s*\{(.*?)\};", source, re.S)
    assert block is not None
    titles = dict(re.findall(r'(\w+):\s*"([^"]+)"', block.group(1)))
    assert sorted(pa.AGENT_TITLE) == sorted(pa.HAND_DRAWN)
    for agent, title in pa.AGENT_TITLE.items():
        assert titles[agent] == title, (agent, titles.get(agent), title)


def test_stale_hand_drawn_svgs_are_found_and_rewritten(tmp_path, monkeypatch) -> None:
    web, guide = tmp_path / "web", tmp_path / "guide"
    web.mkdir()
    guide.mkdir()
    monkeypatch.setattr(pa, "AVATARS", web)
    monkeypatch.setattr(pa, "UI_GUIDE_AGENTS", guide)
    assert [p.parent for p in pa.stale_hand_drawn_svgs()] == [web, guide]  # missing counts as stale
    (web / "einvoicing.svg").write_text("<svg/>", encoding="utf-8")
    written = pa.write_hand_drawn_svgs()
    assert sorted(p.parent for p in written) == sorted([web, guide])
    assert pa.stale_hand_drawn_svgs() == []
    assert (guide / "einvoicing.svg").read_text(encoding="utf-8") == pa.hand_drawn_svg("einvoicing")
    assert pa.write_hand_drawn_svgs() == []
