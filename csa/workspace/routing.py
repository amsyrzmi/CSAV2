"""Which reads the planner needs before it answers: the decider's call, the word rules as fallback.

``planner_facts`` reads a few things only when the message is about them (the caller's manager,
mailbox, team, training and People access). Which ones used to be decided by word rules alone, so
a message with no trigger word ("who do I report to?") never got the fact. ``route_facts`` asks the
routing decider one yes/no question per group in a single request (the decider applies the floor
per question, falling back to the word rules below it). The result per group is the decided answer
OR the word rules': Jev can only add a read, never remove one the words would make, because an
extra read is harmless and a missing one gives a wrong "no access" or "not available". The caller
computes the word-rule ``Needs`` and passes it in: this module never imports the service.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from csa.decide.deciders import Decider

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


@dataclass(frozen=True)
class Needs:
    manager: bool
    mail: bool
    team: bool
    training: bool
    people: bool


def routing_questions() -> dict[str, dict[str, Any]]:
    return {
        f"needs:{group}": {
            "type": "noul",
            "instructions": f"Does `message` need {_DESCRIPTIONS[group]}?",
        }
        for group in FACT_GROUPS
    }


def _from_answers(answers: Mapping[str, Any], fallback: Needs) -> Needs:
    """Decided answer OR word rule, per group: Jev can add a read, never drop one."""
    return Needs(
        **{
            group: bool(answers[f"needs:{group}"]) or getattr(fallback, group)
            for group in FACT_GROUPS
        }
    )


async def route_facts(
    decider: Decider | None,
    body: str,
    *,
    fallback: Needs,
    trace_id: UUID,
    client_id: UUID,
) -> Needs:
    """The reads this message needs: each group decided OR in ``fallback`` (no decider: that).

    A Jev error or timeout never reaches here (the decider falls back). The fallback is always
    complete, so ``FallbackAnswerError`` only means a bug and propagates as a server error.
    """
    if decider is None:
        return fallback

    async def word_rules() -> dict[str, object]:
        return {f"needs:{group}": getattr(fallback, group) for group in FACT_GROUPS}

    decided = await decider.decide(
        {"message": body},
        routing_questions(),
        fallback=word_rules,
        trace_id=trace_id,
        client_id=client_id,
    )
    return _from_answers(decided.answers, fallback)
