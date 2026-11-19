"""The "mark my task done" card's pure parts (slice 1c-ii): which steps are the caller's to mark,
how typed words and picked ids narrow them, the payload check, the card text and the wording in
both languages. The database-backed turns are in tests/test_workspace_hr_task_card.py.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from csa.hr.process_models import MyTask
from csa.workspace import hr_task_card
from csa.workspace.executors import _CARD_TEXT
from csa.workspace.service import _TEXT
from fastapi import HTTPException


def _task(**changes: Any) -> MyTask:
    values: dict[str, Any] = {
        "task_id": uuid4(),
        "process_id": uuid4(),
        "process_kind": "onboarding",
        "process_name": None,
        "employee_name": "Aina Rahman",
        "ordinal": 1,
        "title_en": "Submit bank details",
        "title_ms": "Hantar butiran bank",
        "due_on": date(2099, 12, 1),
        "approval_by": None,
        "auto_check": None,
        "status": "todo",
        "version": 3,
        "overdue": False,
        "pool": False,
        "needs_my_approval": False,
        "mine": True,
    }
    return MyTask.model_construct(**{**values, **changes})


def test_only_the_callers_own_open_plain_step_can_be_marked_done() -> None:
    assert hr_task_card.can_mark_done(_task())
    assert hr_task_card.can_mark_done(_task(approval_by="hr"))  # it then waits for approval
    for changes in (
        {"mine": False},  # someone else's step, seen by the HR team
        {"pool": True},  # the HR pool: the HR team completes it in My tasks
        {"needs_my_approval": True},  # an approval is never given by the assistant
        {"status": "awaiting_approval"},
        {"status": "done"},
        {"status": "skipped"},
        {"auto_check": "account_linked"},  # the console checks these itself
        {"auto_check": "access_removed"},
    ):
        assert not hr_task_card.can_mark_done(_task(**changes)), changes


def test_typed_words_and_picked_ids_narrow_the_steps() -> None:
    bank = _task(title_en="Submit bank details", title_ms="Hantar butiran bank")
    laptop = _task(title_en="Collect the laptop", title_ms=None)
    steps = [bank, laptop]
    assert hr_task_card._narrow(steps, "") == steps
    assert hr_task_card._narrow(steps, "laptop") == [laptop]
    assert hr_task_card._narrow(steps, "BANK details") == [bank]
    assert hr_task_card._narrow(steps, "butiran") == [bank]  # the Malay title counts
    assert hr_task_card._narrow(steps, str(laptop.task_id)) == [laptop]
    assert hr_task_card._narrow(steps, str(uuid4())) == []  # a picked id that is no longer open
    assert hr_task_card._narrow(steps, "nonsense") == []  # nothing matched: the user picks


def test_the_payload_is_exactly_a_task_id_and_a_version() -> None:
    task_id = uuid4()
    assert hr_task_card.HrTaskExecutor._ids({"task_id": str(task_id), "expected_version": 4}) == (
        task_id,
        4,
    )
    for bad in (
        {},
        {"task_id": str(task_id)},
        {"task_id": str(task_id), "expected_version": 4, "note": "anything"},
        {"task_id": str(task_id), "expected_version": "4"},
        {"task_id": str(task_id), "expected_version": True},
        {"task_id": "not-a-uuid", "expected_version": 4},
        {"task_id": 5, "expected_version": 4},
    ):
        with pytest.raises(HTTPException) as caught:
            hr_task_card.HrTaskExecutor._ids(bad)
        assert caught.value.status_code == 422, bad


def test_the_card_names_the_step_and_what_happens_after_confirm() -> None:
    text = _CARD_TEXT["en"]
    plain = hr_task_card._card(_task(), text, "en")
    assert plain.title == "Mark a step done"
    assert plain.summary == (
        'Mark "Submit bank details" as done for Aina Rahman\'s onboarding. Nothing changes '
        "until you select Confirm."
    )
    assert [(f.label, f.value) for f in plain.fields] == [
        ("Step", "Submit bank details"),
        ("For", "Aina Rahman (onboarding)"),
        ("Due", "1 December 2099"),
        ("After you confirm", "The step is marked done."),
    ]
    hr = hr_task_card._card(_task(approval_by="hr", overdue=True, due_on=None), text, "en")
    assert hr.fields[-1].value == "The step then waits for approval by HR."
    assert hr.fields[2].value == "None (overdue)" or hr.fields[2].value == "None"
    manager = hr_task_card._card(_task(approval_by="manager"), text, "en")
    assert manager.fields[-1].value == "The step then waits for approval by the manager."
    custom = hr_task_card._card(
        _task(process_kind="custom", process_name="Safety induction"), text, "en"
    )
    assert custom.fields[1].value == "Aina Rahman (Safety induction)"


def test_the_malay_card_uses_the_malay_title_and_month() -> None:
    card = hr_task_card._card(_task(), _CARD_TEXT["ms"], "ms")
    assert card.language == "ms"
    assert [(f.label, f.value) for f in card.fields] == [
        ("Langkah", "Hantar butiran bank"),
        ("Untuk", "Aina Rahman (onboarding)"),
        ("Tarikh akhir", "1 Disember 2099"),
        ("Selepas anda mengesahkan", "Langkah itu ditandakan selesai."),
    ]
    english_only = hr_task_card._card(_task(title_ms=None), _CARD_TEXT["ms"], "ms")
    assert english_only.fields[0].value == "Submit bank details"  # the template has no Malay title


def test_every_new_word_exists_in_both_languages() -> None:
    card_keys = {
        "hr_task_title", "hr_task_summary", "label_step", "label_for", "label_due",
        "label_after", "task_after_done", "task_after_approval", "task_approver_hr",
        "task_approver_manager", "task_kind_onboarding", "task_kind_offboarding",
        "task_kind_custom", "task_overdue",
    }  # fmt: skip
    turn_keys = {"task_complete_none", "task_complete_which", "task_option_stored"}
    for language in ("en", "ms"):
        assert card_keys <= set(_CARD_TEXT[language]), language
        assert turn_keys <= set(_TEXT[language]), language
    assert _CARD_TEXT["en"]["hr_task_title"] != _CARD_TEXT["ms"]["hr_task_title"]
