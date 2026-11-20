"""The planner prompt as area-tagged bullets: nothing lost, nothing untagged, nothing unowned."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from csa.workspace.planner import SYSTEM_PROMPT, PlannerOutput
from csa.workspace.prompt_areas import ACTION_AREA, AREAS, BULLETS, build_prompt

GOLDEN = Path(__file__).parent / "fixtures" / "planner_system_prompt_full.txt"


def _golden() -> str:
    return GOLDEN.read_text(encoding="utf-8")


def _schema() -> dict[str, Any]:
    return PlannerOutput.model_json_schema()


def _actions() -> list[str]:
    return sorted(_schema()["properties"]["proposal"]["discriminator"]["mapping"])


def test_full_prompt_is_identical_to_the_golden_file() -> None:
    golden = _golden()
    assert build_prompt(_schema(), None) == golden
    assert SYSTEM_PROMPT == golden


def test_every_action_has_an_area() -> None:
    for action in _actions():
        assert action in ACTION_AREA, (
            f"action {action!r} has no area: add it to ACTION_AREA in csa/workspace/prompt_areas.py"
        )
    for name in ACTION_AREA:
        assert name in _actions(), f"ACTION_AREA names {name!r}, which is not a planner action"


def test_every_bullet_has_at_least_one_area() -> None:
    allowed = {*AREAS, "general"}
    for bullet in BULLETS:
        assert bullet.areas, f"bullet has no area: {bullet.text[:60]!r}"
        assert bullet.areas <= allowed, f"unknown area in {bullet.text[:60]!r}"


def test_every_bullet_that_names_an_action_carries_that_actions_area() -> None:
    for action, area in ACTION_AREA.items():
        if area == "general":
            continue
        word = re.compile(rf"\b{re.escape(action)}\b")
        for bullet in BULLETS:
            if word.search(bullet.text):
                assert area in bullet.areas or "general" in bullet.areas, (
                    f"bullet names {action!r} but lacks area {area!r}: {bullet.text[:60]!r}"
                )


def test_bullets_cover_the_prose_exactly() -> None:
    golden = _golden()
    prose = golden[golden.index("\nFacts:\n") + len("\nFacts:\n") :]
    assert "\n".join(bullet.text for bullet in BULLETS) + "\n" == prose


def test_the_history_continuation_rule_is_in_every_scoped_prompt() -> None:
    rule = "continue the original request from history instead of starting over."
    assert any(rule in bullet.text and "general" in bullet.areas for bullet in BULLETS)
    for area in AREAS:
        assert rule in build_prompt(_schema(), frozenset({area})), area
