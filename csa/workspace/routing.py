"""Which reads the planner needs before it answers: the decider's call, the word rules as fallback.

``planner_facts`` reads a few things only when the message is about them (the caller's manager,
mailbox, team, training and People access). Which ones used to be decided by word rules alone, so
a message with no trigger word ("who do I report to?") never got the fact. ``route_facts`` asks the
routing decider one yes/no question per group in a single request (the decider applies the floor
per question, falling back to the word rules below it). The result per group is the decided answer
OR the word rules': Jev can only add a read, never remove one the words would make, because an
extra read is harmless and a missing one gives a wrong "no access" or "not available". The caller
computes the word-rule ``Needs`` and passes it in: this module never imports the service.

The same request also asks one yes/no question per prompt area (``needs:area:<area>``, phase B of
the planner-area-prompts design). The result per area is the decided answer OR the word-rule
areas the caller passes in (``area_fallback``), so Jev can only add an area to the prompt's
selection, never remove one. ``area_select`` imports this module for ``Needs``, so the word-rule
areas come in from the caller and nothing here imports ``area_select``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, get_args
from uuid import UUID

from csa.decide.deciders import Decider
from csa.workspace.prompt_areas import Area

FACT_GROUPS = ("manager", "mail", "team", "training", "people")

_DESCRIPTIONS = {
    "manager": "the user's own boss, manager or supervisor",
    "mail": "email: a reply, a thread, a sender or the inbox",
    "team": "the user's team or direct reports",
    "training": "training: a course, workshop, seminar or certification",
    "people": (
        "people records: adding, changing or removing a person, who reports to whom, "
        "departments or someone leaving"
    ),
}


AREA_NAMES: tuple[Area, ...] = get_args(Area)

_AREA_DESCRIPTIONS: dict[Area, str] = {
    "email": (
        "email: writing, replying to or sending a message, looking up a person to email, "
        "the mailbox or an inbox summary"
    ),
    "inbox": "the inbox of held customer replies: approving or rejecting a held reply",
    "training": "training: a training course, a training request or a training video",
    "hr": ("HR: people records, onboarding or offboarding, leave, or an HR process or its tasks"),
    "bookings": (
        "bookings: booking, moving or cancelling a meeting or appointment, or finding free times"
    ),
    "products": "TikTok Shop products, orders or stock",
}

AREA_QUESTIONS: dict[str, dict[str, Any]] = {
    f"needs:area:{area}": {
        "type": "noul",
        "instructions": f"Does `message` need {_AREA_DESCRIPTIONS[area]}?",
    }
    for area in AREA_NAMES
}


@dataclass(frozen=True)
class Needs:
    manager: bool
    mail: bool
    team: bool
    training: bool
    people: bool


@dataclass(frozen=True)
class RoutedNeeds:
    """What one routing request decided: the fact reads and the prompt areas Jev wants added."""

    needs: Needs
    areas: frozenset[Area]


def fact_questions() -> dict[str, dict[str, Any]]:
    """The five fact questions (manager, mail, team, training, people)."""
    return {
        f"needs:{group}": {
            "type": "noul",
            "instructions": f"Does `message` need {_DESCRIPTIONS[group]}?",
        }
        for group in FACT_GROUPS
    }


def routing_questions() -> dict[str, dict[str, Any]]:
    """The five fact questions then the six area questions: one request, eleven answers.

    The Jev evaluation (``scripts/assistant_jev_eval.py``) always asks all eleven.
    """
    return fact_questions() | AREA_QUESTIONS


def should_ask_areas(decider: Decider | None, *, area_prompts: bool) -> bool:
    """Whether the routing request also asks the six area questions.

    Only when they can be used: the scoped prompt is on (``area_prompts``), or the decider runs
    in ``shadow`` (production keeps its real-traffic area log there). Otherwise the request holds
    the five fact questions it held before phase B, until the owner's evaluation passes.
    """
    return area_prompts or (decider is not None and decider.mode == "shadow")


def _from_answers(
    answers: Mapping[str, Any],
    fallback: Needs,
    area_fallback: frozenset[Area],
    *,
    ask_areas: bool,
) -> RoutedNeeds:
    """Decided answer OR word rule, per question: Jev can add a read or an area, never drop one.

    Without the area questions the areas are the word rules' alone."""
    needs = Needs(
        **{
            group: bool(answers[f"needs:{group}"]) or getattr(fallback, group)
            for group in FACT_GROUPS
        }
    )
    if not ask_areas:
        return RoutedNeeds(needs=needs, areas=area_fallback)
    areas: frozenset[Area] = frozenset(
        area for area in AREA_NAMES if bool(answers[f"needs:area:{area}"]) or area in area_fallback
    )
    return RoutedNeeds(needs=needs, areas=areas)


async def route_facts(
    decider: Decider | None,
    body: str,
    *,
    fallback: Needs,
    area_fallback: frozenset[Area],
    ask_areas: bool,
    trace_id: UUID,
    client_id: UUID,
) -> RoutedNeeds:
    """The reads and areas this message needs: each decided OR in the word rules' answer.

    ``fallback`` is the word-rule ``Needs`` and ``area_fallback`` the word-rule areas (no
    decider: exactly those). The area answers only widen the planner prompt's selection. With
    ``ask_areas`` false (see ``should_ask_areas``) the request holds only the five fact
    questions and the areas are the word rules'.

    A Jev error or timeout never reaches here (the decider falls back). The fallback is always
    complete, so ``FallbackAnswerError`` only means a bug and propagates as a server error.
    """
    if decider is None:
        return RoutedNeeds(needs=fallback, areas=area_fallback)

    async def word_rules() -> dict[str, object]:
        answers: dict[str, object] = {
            f"needs:{group}": getattr(fallback, group) for group in FACT_GROUPS
        }
        if ask_areas:
            answers |= {f"needs:area:{area}": area in area_fallback for area in AREA_NAMES}
        return answers

    decided = await decider.decide(
        {"message": body},
        routing_questions() if ask_areas else fact_questions(),
        fallback=word_rules,
        trace_id=trace_id,
        client_id=client_id,
    )
    return _from_answers(decided.answers, fallback, area_fallback, ask_areas=ask_areas)
