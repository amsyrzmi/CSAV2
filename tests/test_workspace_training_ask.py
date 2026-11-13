"""training_ask through the turn path: gates, finding the video, checked sources and storage.

The planner is scripted (``ScriptedCompletion``) and ``training_model`` is a stub, so no provider
is called. Every video, title and transcript line is synthetic.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import httpx
import pytest
from pydantic import BaseModel
from structlog.testing import capture_logs

from tests.conftest import requires_docker
from tests.test_console_services import activate
from tests.test_console_sessions import SessionWorld
from tests.test_training_video_items import video_row
from tests.test_workspace_planner import (  # noqa: F401 - fixture import
    THREADS,
    ScriptedCompletion,
    _thread,
    _turn_rows,
    planned,
)
from tests.test_workspace_registry import proposals_app  # noqa: F401 - fixture import

pytestmark = [requires_docker, pytest.mark.asyncio]
pytest_plugins = ["tests.test_console_sessions"]

TITLE = "Onboarding basics"
QUESTION = "What does this video say about refunds?"
ANSWER_TEXT = "SYNTHETICANSWER refunds need the receipt."
TRANSCRIPT_LINE = "SYNTHETICLINE bring the receipt for a refund"


class StubTrainingModel:
    """The training model: records every call and answers with ``answer``."""

    model = "stub-training-model"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.answer: dict[str, Any] = {
            "found": True,
            "answer": ANSWER_TEXT,
            "cited_seconds": [196, 761, 5000],
        }

    async def json_completion(
        self,
        schema: type[BaseModel],
        *,
        system: str,
        user: str,
        timeout: float,
        max_tokens: int = 1500,
    ) -> Any:
        self.calls.append({"system": system, "user": user})
        return schema.model_validate(self.answer)


@pytest.fixture
def training_model(planned, monkeypatch: pytest.MonkeyPatch) -> StubTrainingModel:  # noqa: F811
    w, _ = planned
    stub = StubTrainingModel()
    monkeypatch.setattr(w.app.state, "training_model", stub, raising=False)
    return stub


async def _video(
    w: SessionWorld, tenant: str, title: str = TITLE, *, status: str = "ready",
    speech: str | None = None,
) -> str:  # fmt: skip
    """A video with three transcript lines at 00:00, 03:15 and 12:40."""
    item = await video_row(w, tenant, status=status, title=title, speech=speech)
    for position, start in enumerate((0, 195_000, 760_000)):
        await w.q(
            "INSERT INTO training_transcript_segments (client_id, item_id, chunk_index, position,"
            " start_ms, end_ms, text) VALUES (%s, %s, 0, %s, %s, %s, %s)",
            tenant,
            item,
            position,
            start,
            start + 900,
            f"{TRANSCRIPT_LINE} {position}",
        )
    return item


async def _ask(
    w: SessionWorld,
    model: ScriptedCompletion,
    browser: httpx.AsyncClient,
    proposal: dict[str, Any],
    *,
    body: str = QUESTION,
    record: str | None = None,
    thread: str | None = None,
) -> dict[str, Any]:
    model.reply({"action": "training_ask", **proposal})
    context: dict[str, Any] = {"tab": "training_library"}
    if record is not None:
        context["section"] = "item"
        context["record"] = {"kind": "training_item", "id": record}
    thread = thread or await _thread(w, browser)
    response = await w.mutate(
        browser,
        f"{THREADS}/{thread}/turns",
        {"request_key": str(uuid4()), "body": body, "page_context": context},
    )
    assert response.status_code == 201, response.text
    return response.json()


async def test_question_about_an_open_video_answers_with_watch_from_sources(
    planned,  # noqa: F811
    training_model: StubTrainingModel,
) -> None:
    w, model = planned
    tenant = w.tenants[0]
    await activate(w, tenant, "training_library")
    item = await _video(w, tenant)
    browser, _ = await w.member(role="tenant_staff")
    turn = await _ask(w, model, browser, {"question": "What about refunds?"}, record=item)
    facts = model.facts()
    assert facts["record_summary"] == (
        f"Training video {item}: “{TITLE}”, length 01:30, transcript yes."
    )
    assert "training_ask" in facts["page_hint"]
    result = turn["result"]
    assert result["action"] == "training_ask"
    assert result["sources"] == [
        {"item_id": item, "title": TITLE, "start_seconds": 195},
        {"item_id": item, "title": TITLE, "start_seconds": 760},
    ]
    assert result["markdown"] == f"{ANSWER_TEXT}\n\nFrom *{TITLE}*, at 03:15 and 12:40."
    [call] = training_model.calls
    assert "[03:15] SYNTHETICLINE" in call["user"] and "What about refunds?" in call["user"]

    # The same in Malay: the answer's line is in the turn's language.
    malay = await _ask(
        w,
        model,
        browser,
        {"question": "Apa kata video ini tentang bayaran balik?"},
        body="Apa kata video ini tentang bayaran balik?",
        record=item,
    )
    assert malay["language"] == "ms"
    assert malay["result"]["markdown"].endswith(f"Daripada *{TITLE}*, pada 03:15 dan 12:40.")
    assert "Bahasa Melayu" in training_model.calls[-1]["user"]


async def test_title_words_with_several_matches_give_a_clarification_of_item_ids(
    planned,  # noqa: F811
    training_model: StubTrainingModel,
) -> None:
    w, model = planned
    tenant = w.tenants[0]
    await activate(w, tenant, "training_library")
    ready = [await _video(w, tenant, f"Onboarding part {i}") for i in range(1, 7)]
    processing = await _video(w, tenant, "Onboarding draft", status="transcribing")
    await _video(w, tenant, "Fire safety")
    browser, _ = await w.member(role="tenant_staff")
    choose = (await _ask(w, model, browser, {"question": QUESTION, "item": "onboarding video"}))[
        "result"
    ]
    assert choose["action"] == "clarification"
    assert choose["question"] == "Several training videos match. Which one do you mean?"
    assert len(choose["options"]) == 5
    values = [option["value"] for option in choose["options"]]
    assert set(values) <= set(ready) and processing not in values
    assert all(option["label"].startswith("Onboarding part ") for option in choose["options"])
    assert training_model.calls == []
    # Picking an option answers from that one video.
    picked = await _ask(w, model, browser, {"question": QUESTION, "item_id": values[0]})
    assert picked["result"]["action"] == "training_ask"
    assert {source["item_id"] for source in picked["result"]["sources"]} == {values[0]}
    # Words that match one video answer from it, whatever their case.
    one = await _ask(w, model, browser, {"question": QUESTION, "item": "FIRE safety"})
    assert one["result"]["sources"][0]["title"] == "Fire safety"


async def test_no_match_still_processing_and_no_speech_answers(
    planned,  # noqa: F811
    training_model: StubTrainingModel,
) -> None:
    w, model = planned
    tenant = w.tenants[0]
    await activate(w, tenant, "training_library")
    processing = await _video(w, tenant, "Payroll intro", status="transcribing")
    silent = await _video(w, tenant, "Warehouse tour", speech="no_speech")
    staff, _ = await w.member(role="tenant_staff")
    admin, _ = await w.member(role="tenant_admin")

    async def markdown(browser: httpx.AsyncClient, proposal: dict[str, Any], **kw: Any) -> str:
        result = (await _ask(w, model, browser, proposal, **kw))["result"]
        assert result["action"] == "answer", result
        return result["markdown"]

    assert (
        await markdown(staff, {"question": QUESTION, "item": "forklift"})
        == "I couldn't find a training video called forklift."
    )
    # A manager sees videos in every state; a reader only ready ones.
    assert (
        await markdown(admin, {"question": QUESTION}, record=processing)
        == "That video is still being processed."
    )
    assert (
        await markdown(admin, {"question": QUESTION, "item": "payroll"})
        == "That video is still being processed."
    )
    assert (
        await markdown(staff, {"question": QUESTION}, record=processing)
        == "I couldn't find that training video."
    )
    assert (
        await markdown(staff, {"question": QUESTION}, record=silent)
        == "That video has no speech, so I can't answer from it."
    )
    malay = await markdown(
        staff,
        {"question": "Apa kata video ini?", "item": "forklift"},
        body="Apa kata video ini tentang forklift?",
    )
    assert malay == "Saya tidak menjumpai video latihan bernama forklift."
    assert training_model.calls == []
    # With no training model the assistant says it cannot search, rather than guessing.
    ready = await _video(w, tenant, "Refund desk")
    w.app.state.training_model = None
    assert (
        await markdown(staff, {"question": QUESTION}, record=ready)
        == "I can't search this video right now. Try again later."
    )


async def test_answer_is_stored_as_answer_with_result_kind_training_ask(
    planned,  # noqa: F811
    training_model: StubTrainingModel,
) -> None:
    w, model = planned
    tenant = w.tenants[0]
    await activate(w, tenant, "training_library")
    item = await _video(w, tenant)
    browser, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, browser)
    turn = await _ask(w, model, browser, {"question": QUESTION}, record=item, thread=thread)
    [row] = await _turn_rows(w)
    assert row["action"] == "answer" and row["page_key"] == "training_library"
    assert row["result"]["action"] == "training_ask"
    assert row["result"]["sources"] == turn["result"]["sources"]
    assert await w.q("SELECT count(*) n FROM console_proposals") == [{"n": 0}]
    # The stored turn reads back as the same result, and the next plan sees its kind.
    listed = await browser.get(f"{THREADS}/{thread}/turns")
    assert listed.status_code == 200, listed.text
    assert [item["result"] for item in listed.json()["items"]] == [turn["result"]]
    await _ask(w, model, browser, {"question": QUESTION}, record=item, thread=thread)
    assert model.facts()["history"][0]["result"] == "training_ask"


async def test_other_companys_item_id_is_not_found(
    planned,  # noqa: F811
    training_model: StubTrainingModel,
) -> None:
    w, model = planned
    tenant, other = w.tenants[0], w.tenants[1]
    await activate(w, tenant, "training_library")
    await activate(w, other, "training_library")
    foreign = await _video(w, other, "Foreign onboarding")
    browser, _ = await w.member(role="tenant_staff")
    by_record = await _ask(w, model, browser, {"question": QUESTION}, record=foreign)
    assert (
        model.facts()["record_summary"] == "The open training_item record is not available to you."
    )
    by_id = await _ask(w, model, browser, {"question": QUESTION, "item_id": foreign})
    by_words = await _ask(w, model, browser, {"question": QUESTION, "item": "foreign"})
    for turn in (by_record, by_id):
        assert turn["result"] == {
            "action": "answer",
            "markdown": "I couldn't find that training video.",
        }
    assert by_words["result"]["markdown"] == "I couldn't find a training video called foreign."
    assert training_model.calls == []


async def test_without_the_package_it_answers_like_a_locked_tab(
    planned,  # noqa: F811
    training_model: StubTrainingModel,
) -> None:
    w, model = planned
    tenant = w.tenants[0]
    item = await _video(w, tenant)
    browser, _ = await w.member(role="tenant_staff")
    turn = await _ask(w, model, browser, {"question": QUESTION}, record=item)
    assert turn["result"]["action"] == "answer"
    assert turn["result"]["markdown"].startswith("The Training library tab needs the ")
    assert model.facts()["record_summary"] == (
        "The open training_item record's service is not active."
    )
    malay = await _ask(
        w, model, browser, {"question": QUESTION}, body="Apa kata video ini?", record=item
    )
    assert malay["result"]["markdown"].startswith("Tab Training library memerlukan ")
    assert training_model.calls == []


async def test_read_only_member_cannot_use_the_assistant(
    planned,  # noqa: F811
    training_model: StubTrainingModel,
) -> None:
    w, model = planned
    tenant = w.tenants[0]
    await activate(w, tenant, "training_library")
    item = await _video(w, tenant)
    staff, _ = await w.member(role="tenant_staff")
    thread = await _thread(w, staff)
    reader, _ = await w.member(role="read_only")
    created = await w.mutate(reader, THREADS, {"request_key": str(uuid4())})
    assert created.status_code == 403
    context = {"tab": "training_library", "record": {"kind": "training_item", "id": item}}
    refused = await w.mutate(
        reader,
        f"{THREADS}/{thread}/turns",
        {"request_key": str(uuid4()), "body": QUESTION, "page_context": context},
    )
    assert refused.status_code == 403
    assert model.calls == [] and training_model.calls == []
    assert await _turn_rows(w) == []


async def test_logs_hold_no_question_transcript_or_answer(
    planned,  # noqa: F811
    training_model: StubTrainingModel,
) -> None:
    w, model = planned
    tenant = w.tenants[0]
    await activate(w, tenant, "training_library")
    item = await _video(w, tenant, "SYNTHETICTITLE onboarding")
    browser, _ = await w.member(role="tenant_staff")
    question = "SYNTHETICQUESTION what about refunds?"
    with capture_logs() as logs:
        turn = await _ask(w, model, browser, {"question": question}, body=question, record=item)
        await _ask(w, model, browser, {"question": question, "item": "nothing matches"})
    assert turn["result"]["action"] == "training_ask"
    text = json.dumps(logs, default=str)
    for needle in ("SYNTHETICQUESTION", "SYNTHETICLINE", "SYNTHETICANSWER", "SYNTHETICTITLE"):
        assert needle not in text, needle
    asked = [entry for entry in logs if entry.get("event") == "workspace_training_ask"]
    answered = next(entry for entry in asked if entry["outcome"] == "answered")
    assert answered["item_id"] == item and answered["model"] == "stub-training-model"
    assert answered["context_chars"] > 0 and answered["sources"] == 2
    assert {entry["outcome"] for entry in asked} == {"answered", "not_found"}
