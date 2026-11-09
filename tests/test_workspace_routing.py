"""Routing the planner's fact reads: the decider's call, the word rules as fallback."""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
from csa.decide import deciders
from csa.decide.deciders import Decider
from csa.decide.jev import JevError, JevResponse
from csa.workspace.routing import FACT_GROUPS, Needs, route_facts
from csa.workspace.service import word_rule_needs

FLOORS = {"noul": 0.7, "choice": 0.7}
NOTHING = Needs(manager=False, mail=False, team=False, training=False, people=False)


def _jev(**p: float) -> JevResponse:
    """Jev's answer per group (probability of yes); a group not named is a confident no."""
    return JevResponse(
        model="jev-test",
        answers={f"needs:{g}": {"noul": p.get(g, 0.02)} for g in FACT_GROUPS},
    )


class FakeJev:
    model = "jev-test"

    def __init__(
        self,
        response: JevResponse | None = None,
        error: Exception | None = None,
        hang: bool = False,
    ) -> None:
        self.response = response or _jev()
        self.error = error
        self.hang = hang
        self.calls: list[dict[str, Any]] = []

    async def ask(self, state: object, questions: Any, *, timeout: float) -> JevResponse:
        self.calls.append({"state": state, "questions": questions, "timeout": timeout})
        if self.hang:
            await asyncio.sleep(30)
        if self.error is not None:
            raise self.error
        return self.response


@pytest.fixture(autouse=True)
def no_database(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, ...]]:
    """Stands in for the tenant transaction and the decision_log insert."""
    written: list[tuple[Any, ...]] = []

    @asynccontextmanager
    async def fake_tx(pool: object, client_id: str) -> AsyncIterator[object]:
        yield object()

    async def fake_execute(path: str, params: tuple[Any, ...], **_: object) -> None:
        written.append(params)

    monkeypatch.setattr(deciders, "tx", fake_tx)
    monkeypatch.setattr(deciders.db.q, "execute", fake_execute)
    return written


def _decider(client: FakeJev, mode: str = "cascade") -> Decider:
    return Decider(
        "routing",
        mode,
        client,  # type: ignore[arg-type]
        object(),
        floors=FLOORS,
        audit_sample=0.0,
        rng=random.Random(1),
    )


async def _route(decider: Decider | None, body: str) -> Needs:
    return await route_facts(
        decider,
        body,
        fallback=word_rule_needs(body),
        trace_id=uuid4(),
        client_id=uuid4(),
    )


async def test_jev_yes_for_people_reads_people_access_without_a_trigger_word() -> None:
    body = "who handles the paperwork when somebody joins?"
    assert word_rule_needs(body) == NOTHING
    jev = FakeJev(_jev(people=0.93))
    needs = await _route(_decider(jev), body)
    assert needs == Needs(manager=False, mail=False, team=False, training=False, people=True)


async def test_below_floor_uses_the_word_rules() -> None:
    body = "reply to the email from my boss"
    # 0.6 is under the 0.7 floor for the three groups the words match, whatever Jev leaned to.
    jev = FakeJev(_jev(manager=0.4, mail=0.4, people=0.6, team=0.6, training=0.6))
    needs = await _route(_decider(jev), body)
    assert needs == word_rule_needs(body)
    assert needs.mail and needs.manager and not needs.training


async def test_each_group_is_judged_on_its_own() -> None:
    body = "reply to the email"  # words: mail only
    jev = FakeJev(_jev(mail=0.55, training=0.97))  # mail is unsure, training is a confident yes
    needs = await _route(_decider(jev), body)
    assert needs.mail is True  # the word rule answered it
    assert needs.training is True  # Jev answered it
    assert not (needs.manager or needs.team or needs.people)


async def test_a_confident_jev_no_never_removes_a_word_rule_read() -> None:
    # "add Ali to Sales": the word rule reads People access; a wrong confident no must not drop it.
    body = "add Ali to Sales"
    rule = word_rule_needs(body)
    assert rule.people
    jev = FakeJev(_jev())  # every group a confident no (0.02)
    needs = await _route(_decider(jev), body)
    assert needs.people is True
    assert needs == rule  # a confident no everywhere adds nothing and removes nothing


async def test_jev_only_adds_reads_on_every_group() -> None:
    body = "reply to my manager about the training course"  # words: manager, mail, training, people
    rule = word_rule_needs(body)
    assert rule == Needs(manager=True, mail=True, team=False, training=True, people=True)
    needs = await _route(_decider(FakeJev(_jev(team=0.95))), body)  # no for the four, yes for team
    assert needs == Needs(manager=True, mail=True, team=True, training=True, people=True)


@pytest.mark.parametrize(
    "body",
    [
        "tolong balas emel daripada pengurus saya",  # Malay
        "boss tu nak check team punya latihan lah, can add new staff or not",  # Manglish
    ],
)
@pytest.mark.parametrize("failure", ["error", "timeout"])
async def test_jev_error_uses_the_word_rules(
    body: str, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if failure == "timeout":
        monkeypatch.setattr(deciders, "CLASSIFY_TIMEOUT_S", 0.05)
        jev = FakeJev(hang=True)
    else:
        jev = FakeJev(error=JevError("jev_http_503"))
    needs = await _route(_decider(jev), body)
    assert needs == word_rule_needs(body)
    assert any(getattr(needs, g) for g in FACT_GROUPS)


async def test_no_decider_is_exactly_todays_behaviour() -> None:
    body = "reply to my manager about the training course"
    fallback = word_rule_needs(body)
    got = await route_facts(None, body, fallback=fallback, trace_id=uuid4(), client_id=uuid4())
    assert got is fallback
    assert fallback == Needs(manager=True, mail=True, team=False, training=True, people=True)


def test_word_rules_are_the_five_regexes() -> None:
    assert word_rule_needs("hello") == NOTHING
    assert word_rule_needs("my team") == Needs(
        manager=False, mail=False, team=True, training=False, people=False
    )


async def test_routing_questions_are_noul_per_group() -> None:
    jev = FakeJev()
    await _route(_decider(jev), "show me the inbox, mail me at ali@example.com")
    assert len(jev.calls) == 1  # one request for all five
    questions = jev.calls[0]["questions"]
    assert list(questions) == [f"needs:{g}" for g in FACT_GROUPS]
    assert all(q["type"] == "noul" and "`message`" in q["instructions"] for q in questions.values())
    # The message travels redacted, in state["message"].
    sent = jev.calls[0]["state"]
    assert set(sent) == {"message"} and "ali@example.com" not in sent["message"]
    assert jev.calls[0]["timeout"] == 2.0


async def test_the_decision_is_logged_without_the_message(
    no_database: list[tuple[Any, ...]],
) -> None:
    await _route(_decider(FakeJev(_jev(mail=0.9))), "private words here")
    assert len(no_database) == 1
    assert "private words here" not in repr(no_database[0])
