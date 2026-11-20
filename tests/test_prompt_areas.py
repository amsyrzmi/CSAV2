"""The planner prompt as area-tagged bullets: nothing lost, nothing untagged, nothing unowned."""

from __future__ import annotations

import itertools
import json
import re
from pathlib import Path
from typing import Any

from csa.workspace.planner import SYSTEM_PROMPT, PlannerOutput, scoped_prompt
from csa.workspace.prompt_areas import (
    _HEAD,
    ACTION_AREA,
    AREAS,
    BULLETS,
    Area,
    allowed_actions,
    build_prompt,
    filter_schema,
)

GOLDEN = Path(__file__).parent / "fixtures" / "planner_system_prompt_full.txt"
# A one-area prompt must stay under this share of the full prompt. Measured on 2026-11-20 (full
# 36402 characters): email 20599 (0.566), hr 16088, training 15922, bookings 15337, inbox 13424,
# products 7871. Email is the largest because of the email_batch schema; 0.6 leaves little slack.
CEILING = 0.6


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


def _refs(node: Any) -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                found.append(value)
            else:
                found.extend(_refs(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_refs(item))
    return found


def _mapping(schema: dict[str, Any]) -> dict[str, str]:
    return schema["properties"]["proposal"]["discriminator"]["mapping"]


def _schema_text(prompt: str) -> str:
    head = prompt[len(_HEAD) :] if prompt.startswith(_HEAD) else prompt
    return head[: head.index("\n\nFacts:\n")]


def test_allowed_actions_follows_the_selected_areas() -> None:
    general = {"answer", "navigate", "clarification"}
    assert allowed_actions(frozenset()) == general
    assert allowed_actions(frozenset({"hr"})) == general | {
        action for action, area in ACTION_AREA.items() if area == "hr"
    }
    assert "plan" not in allowed_actions(frozenset({"hr"}))
    assert "plan" not in allowed_actions(frozenset({"products"}))
    for area in ("bookings", "training", "inbox", "email"):
        assert "plan" in allowed_actions(frozenset({area})), area
    assert "plan" in allowed_actions(frozenset({"email", "hr"}))


def test_scoped_schema_lists_only_allowed_actions() -> None:
    areas: frozenset[Area] = frozenset({"hr"})
    allowed = allowed_actions(areas)
    filtered = filter_schema(_schema(), allowed)
    assert set(_mapping(filtered)) == allowed
    union = filtered["properties"]["proposal"]["oneOf"]
    assert {ref["$ref"] for ref in union} == {_mapping(filtered)[a] for a in allowed}
    text = json.dumps(filtered)
    assert "email_compose" not in text
    assert "hr_process_read" in text
    assert "hr_process_read" in build_prompt(_schema(), areas)
    assert "email_compose" not in _schema_text(scoped_prompt(areas))


def test_scoped_schema_keeps_the_defs_it_references() -> None:
    selections: list[frozenset[Area]] = [
        frozenset({"bookings"}),
        frozenset({"email", "hr"}),
        frozenset(AREAS),
    ]
    for areas in selections:
        filtered = filter_schema(_schema(), allowed_actions(areas))
        assert "plan" in _mapping(filtered), areas
        for step in ("BookingActionProposal", "TrainingSubmitProposal", "InboxDecisionProposal"):
            assert step in filtered["$defs"], (areas, step)
        for ref in _refs(filtered):
            assert ref.startswith("#/$defs/")
            assert ref.removeprefix("#/$defs/") in filtered["$defs"], (areas, ref)


def test_the_top_level_union_lists_only_allowed_actions_even_with_plan_steps() -> None:
    areas: frozenset[Area] = frozenset({"email"})
    allowed = allowed_actions(areas)
    filtered = filter_schema(_schema(), allowed)
    assert set(_mapping(filtered)) == allowed
    assert "booking_action" not in _mapping(filtered)
    assert "BookingActionProposal" not in {
        ref["$ref"].removeprefix("#/$defs/") for ref in filtered["properties"]["proposal"]["oneOf"]
    }
    # plan's own steps still resolve
    assert "BookingActionProposal" in filtered["$defs"]
    steps = filtered["$defs"]["PlanProposal"]["properties"]["steps"]["items"]
    assert set(steps["discriminator"]["mapping"]) >= {"booking_action", "email_batch"}


def test_filter_schema_does_not_mutate_its_input() -> None:
    schema = _schema()
    before = json.dumps(schema)
    filter_schema(schema, allowed_actions(frozenset({"hr"})))
    assert json.dumps(schema) == before


def test_a_bullet_naming_plan_only_appears_when_plan_is_allowed() -> None:
    for size in range(len(AREAS) + 1):
        for combo in itertools.combinations(AREAS, size):
            areas = frozenset(combo)
            prompt = scoped_prompt(areas)
            plan_bullet_in = "\n- plan: " in prompt
            assert plan_bullet_in == ("plan" in allowed_actions(areas)), combo


def _bullets_naming(action: str) -> list[str]:
    word = re.compile(rf"\b{re.escape(action)}\b")
    return [b.text for b in BULLETS if word.search(b.text) and b.text.startswith("- ")]


def test_scoped_prompt_has_each_selected_actions_bullet_and_rules() -> None:
    for area in AREAS:
        prompt = scoped_prompt(frozenset({area}))
        owned = {a for a, owner in ACTION_AREA.items() if owner == area}
        for action in owned:
            for text in _bullets_naming(action):
                assert text in prompt, (area, action, text[:50])
        for bullet in BULLETS:
            if bullet.areas == {area} or "general" in bullet.areas:
                assert bullet.text in prompt, (area, bullet.text[:50])
        for bullet in BULLETS:
            if area not in bullet.areas and "general" not in bullet.areas:
                assert bullet.text not in prompt, (area, bullet.text[:50])


def test_a_one_area_prompt_is_much_smaller() -> None:
    for area in AREAS:
        size = len(scoped_prompt(frozenset({area})))
        print(area, size, len(SYSTEM_PROMPT))
        assert size < CEILING * len(SYSTEM_PROMPT), area


def test_scoped_prompt_is_valid_json_schema_text() -> None:
    for area in AREAS:
        prompt = scoped_prompt(frozenset({area}))
        assert prompt.startswith(_HEAD)
        parsed = json.loads(_schema_text(prompt))
        assert set(parsed["properties"]["proposal"]["discriminator"]["mapping"]) == (
            allowed_actions(frozenset({area}))
        )


def test_scoped_prompt_is_cached_per_areas_set() -> None:
    assert scoped_prompt(frozenset({"hr"})) is scoped_prompt(frozenset({"hr"}))
