"""The process output models against what the migrations write (no database).

The models take any short event action, extra detail keys and unknown warning codes, so a later
migration never breaks a read. These guards make sure every action, detail key, warning code and
anchor date a migration from 0630 on writes is still listed in the models (and `_WARNINGS`), so the
typed contract stays complete: a migration that adds one fails here until the models list it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args
from uuid import uuid4

import pytest
from csa.hr.process_models import (
    AnchorDates,
    MyTask,
    ProcessCloseReceipt,
    ProcessEventAction,
    ProcessEventDetail,
    ProcessTask,
    TaskReceipt,
    TemplatePreview,
    TemplatePreviewTie,
    TemplateStep,
    TemplateStepOut,
    TemplateWarningCode,
)
from csa.hr.processes import _WARNINGS
from pydantic import ValidationError

MIGRATIONS = Path(__file__).resolve().parents[1] / "db/migrations"
_INSERT = re.compile(r"INSERT INTO public\.hr_process_events\b(.*?);", re.S)
_ACTION = re.compile(r"'([a-z_]+\.[a-z_]+)'")
_WARNING = re.compile(r"'code'\s*,\s*'([a-z_]+)'")


def process_migrations() -> str:
    """The text of every migration from 0630 (HR processes) on."""
    files = sorted(p for p in MIGRATIONS.glob("[0-9][0-9][0-9][0-9]_*.sql") if p.name >= "0630")
    assert files and files[0].name.startswith("0630")
    return "\n".join(p.read_text() for p in files)


def build_objects(text: str) -> list[list[str]]:
    """The top-level arguments of every jsonb_build_object(...) call in `text`."""
    found = []
    for match in re.finditer(r"jsonb_build_object\(", text):
        depth, start, args, quoted = 1, match.end(), [], False
        i = start
        while depth:
            c = text[i]
            if c == "'":
                quoted = not quoted
            elif not quoted and c == "(":
                depth += 1
            elif not quoted and c == ")":
                depth -= 1
            elif not quoted and c == "," and depth == 1:
                args.append(text[start:i].strip())
                start = i + 1
            i += 1
        args.append(text[start : i - 1].strip())
        found.append(args)
    return found


def keys(args: list[str]) -> set[str]:
    """The keys of one jsonb_build_object call; a key that is not a literal fails the guard."""
    names = set()
    for key in args[::2]:
        assert re.fullmatch(r"'[a-z_]+'", key), f"a detail key that is not a literal: {key}"
        names.add(key.strip("'"))
    return names


def written_events(text: str) -> tuple[set[str], set[str]]:
    """The actions and detail keys of every insert into hr_process_events in `text`."""
    actions: set[str] = set()
    detail: set[str] = set()
    statements = _INSERT.findall(text)
    for statement in statements:
        named = _ACTION.findall(statement)
        assert named, f"an event whose action is not a literal: {statement[:200]}"
        actions |= set(named)
        for args in build_objects(statement):
            detail |= keys(args)
    return actions, detail


def detail_names() -> set[str]:
    return {field.alias or name for name, field in ProcessEventDetail.model_fields.items()}


def missing(text: str) -> dict[str, set[str]]:
    """What `text` writes that the models do not list."""
    actions, detail = written_events(text)
    anchors = set().union(*(keys(a) for a in build_objects(text) if "'start_date'" in a[::2]))
    return {
        "actions": actions - set(get_args(ProcessEventAction)),
        "detail": detail - detail_names(),
        "warnings": set(_WARNING.findall(text)) - set(_WARNINGS),
        "anchor_dates": anchors - set(AnchorDates.model_fields),
    }


def test_every_event_action_detail_key_warning_and_anchor_date_is_modelled() -> None:
    text = process_migrations()
    actions, detail = written_events(text)
    # The parser finds what 0632 writes today, so an empty parse cannot pass silently.
    assert {"process.started", "task.created", "task.reassigned"} <= actions
    assert {"reason", "from", "to", "clamped"} <= detail
    assert missing(text) == {
        "actions": set(),
        "detail": set(),
        "warnings": set(),
        "anchor_dates": set(),
    }
    assert set(_WARNINGS.values()) == set(get_args(TemplateWarningCode))


def test_the_guard_fails_for_a_new_action_key_or_warning() -> None:
    synthetic = """
    INSERT INTO public.hr_process_events(client_id,process_id,task_id,actor,action,detail)
    VALUES(p_client,pid,tid,p_actor,'task.escalated',
     jsonb_build_object('escalated_to',a,'reason',r) || jsonb_build_object('second_person',false));
    SELECT jsonb_build_object('code','hr_template_step_without_owner','ordinal',s.ordinal);
    SELECT jsonb_build_object('start_date',e.start_date,'contract_end',e.contract_end);
    """
    assert missing(synthetic) == {
        "actions": {"task.escalated"},
        "detail": {"escalated_to", "second_person"},
        "warnings": {"hr_template_step_without_owner"},
        "anchor_dates": {"contract_end"},
    }
    with pytest.raises(AssertionError, match="action is not a literal"):
        written_events(
            "INSERT INTO public.hr_process_events(action) VALUES('task.'||verb_name, x);"
        )
    with pytest.raises(AssertionError, match="not a literal"):
        written_events(
            "INSERT INTO public.hr_process_events(action,detail) "
            "VALUES('task.done',jsonb_build_object(k,v));"
        )


def test_reads_tolerate_what_a_later_migration_adds() -> None:
    from csa.hr.process_models import ProcessEvent

    event = ProcessEvent.model_validate(
        {
            "event_id": str(uuid4()),
            "at": "2026-11-16T00:00:00+08:00",
            "actor": None,
            "action": "task.escalated",
            "task_id": None,
            "detail": {"reason": "ok", "second_person": False},
        }
    )
    assert event.action == "task.escalated"
    assert event.detail.model_dump(by_alias=True, exclude_unset=True) == {
        "reason": "ok",
        "second_person": False,
    }
    assert AnchorDates.model_validate({"start_date": None, "contract_end": "2027-01-01"}) == (
        AnchorDates(start_date=None)
    )


def _task(**over: object) -> dict[str, object]:
    return {
        "task_id": str(uuid4()),
        "ordinal": 1,
        "title_en": "Synthetic",
        "title_ms": None,
        "owner_role": "hr",
        "assignee_name": None,
        "due_on": None,
        "status": "todo",
        "required": True,
        "overdue": False,
        "mine": False,
        **over,
    }


def _step_out(**over: object) -> dict[str, object]:
    return {
        "ordinal": 1,
        "title_en": "Synthetic",
        "title_ms": None,
        "description_en": None,
        "description_ms": None,
        "owner_role": "hr",
        "owner_employee_id": None,
        "anchor": "start_date",
        "due_offset_days": 0,
        "required": True,
        "approval_by": None,
        "auto_check": None,
        "reference_note": None,
        **over,
    }


@pytest.mark.parametrize("field", ["description_en", "description_ms"])
def test_step_descriptions_keep_line_breaks_and_refuse_other_controls(field: str) -> None:
    text = "Bring:\n- the identity card\n- a bank statement"
    step = {"title_en": "Synthetic", "owner_role": "hr", "anchor": "start_date"}
    assert (
        getattr(TemplateStep.model_validate({**step, "due_offset_days": 0, field: text}), field)
        == text
    )
    windows = TemplateStep.model_validate({**step, "due_offset_days": 0, field: "one\r\ntwo"})
    assert getattr(windows, field) == "one\ntwo"
    assert getattr(TemplateStepOut.model_validate(_step_out(**{field: text})), field) == text
    assert getattr(ProcessTask.model_validate(_task(**{field: text})), field) == text
    for bad in ("tab\there", "nul\x00here", "lone\rreturn", "bell\x07"):
        with pytest.raises(ValidationError):
            TemplateStep.model_validate({**step, "due_offset_days": 0, field: bad})
        with pytest.raises(ValidationError):
            TemplateStepOut.model_validate(_step_out(**{field: bad}))
        with pytest.raises(ValidationError):
            ProcessTask.model_validate(_task(**{field: bad}))
    # Only the descriptions (and a task's note, below): a title keeps the single-line rule.
    for model, row in ((TemplateStepOut, _step_out()), (ProcessTask, _task())):
        with pytest.raises(ValidationError):
            model.model_validate({**row, "title_en": "two\nlines"})
    with pytest.raises(ValidationError):
        TemplateStep.model_validate({**step, "due_offset_days": 0, "reference_note": "a\nb"})
    with pytest.raises(ValidationError):
        ProcessTask.model_validate(_task(reference_note="a\nb"))


def test_a_task_note_keeps_line_breaks_and_refuses_other_controls() -> None:
    # 0632 hr_process_person_left appends "person left" to a note on a new line.
    note = "Synthetic note\nperson left"
    assert ProcessTask.model_validate(_task(note=note)).note == note
    for bad in ("tab\there", "nul\x00here", "lone\rreturn", "bell\x07"):
        with pytest.raises(ValidationError):
            ProcessTask.model_validate(_task(note=bad))


def test_event_detail_returns_every_stored_value_as_it_is() -> None:
    from csa.hr.process_models import ProcessEvent

    detail = {
        "reason": "  sent back:\nthe form is unsigned  ",
        "fallback": "hr_team",
        "to": "the HR pool",
        "from": "not-a-uuid",
        "old": "sometime",
        "new": 3,
        "kind": "secondment",
        "owner_role": "contractor",
        "anchor": "contract_end",
        "auto_check": "document_uploaded:visa",
        "template_id": "x",
        "template_version": "one",
        "assignee_employee_id": 7,
        "due_on": "soon",
        "clamped": "yes",
        "remark": "  wrong form,\nsee me  ",
        "sent_back": "yes",
        "awaiting_approval": 1,
        "on_behalf": None,
        "approver": "board",
        "approved_without_second_person": "true",
        "note": "tab\there",
    }
    assert set(detail) - {"note"} == detail_names()
    event = ProcessEvent.model_validate(
        {
            "event_id": str(uuid4()),
            "at": "2026-11-16T00:00:00+08:00",
            "actor": None,
            "action": "task.sent_back",
            "task_id": None,
            "detail": detail,
        }
    )
    assert event.detail.model_dump(by_alias=True, exclude_unset=True) == detail
    assert event.model_dump(mode="json", by_alias=True)["detail"]["reason"] == (
        "  sent back:\nthe form is unsigned  "
    )


def test_my_tasks_and_the_task_receipts_are_what_the_latest_migration_returns() -> None:
    """MyTask, TaskReceipt and ProcessCloseReceipt forbid unknown keys: a migration that changes
    hr_task_rows_mine's columns or hr_task_apply's result fails here, not as a 500 on a read."""
    text = process_migrations()
    *_, columns = re.findall(
        r"CREATE (?:OR REPLACE )?FUNCTION public\.hr_task_rows_mine\("
        r".*?RETURNS TABLE\((.*?)\)\s*LANGUAGE",
        text,
        re.S,
    )
    assert {c.split()[0] for c in columns.split(",")} == set(MyTask.model_fields)
    *_, apply = text.split("CREATE OR REPLACE FUNCTION public.hr_task_apply(")
    results = [keys(a) for a in build_objects(apply) if "'status'" in a[::2]]
    assert set(TaskReceipt.model_fields) in results
    assert set(ProcessCloseReceipt.model_fields) in results


def test_the_template_preview_is_what_the_latest_migration_returns() -> None:
    """TemplatePreview forbids unknown keys: a migration that changes hr_template_preview's result
    fails here, not as a 500 on the template editor's preview."""
    *_, body = process_migrations().split("CREATE OR REPLACE FUNCTION public.hr_template_preview(")
    body = body.split("END $$;")[0]
    found = [keys(a) for a in build_objects(body)]
    assert set(TemplatePreview.model_fields) in found
    assert set(TemplatePreviewTie.model_fields) in found


def _my_task(**over: object) -> dict[str, object]:
    return {
        **dict.fromkeys(MyTask.model_fields),
        "task_id": str(uuid4()),
        "process_id": str(uuid4()),
        "process_kind": "custom",
        "employee_name": "Synthetic",
        "ordinal": 1,
        "title_en": "Synthetic",
        "owner_role": "hr",
        "required": True,
        "status": "todo",
        "version": 1,
        "overdue": False,
        "pool": True,
        "needs_my_approval": False,
        "mine": False,
        **over,
    }


@pytest.mark.parametrize("field", ["description_en", "description_ms", "note", "sent_back_reason"])
def test_a_my_tasks_row_keeps_line_breaks_and_refuses_other_controls(field: str) -> None:
    text = "Synthetic: line one\nline two"
    assert getattr(MyTask.model_validate(_my_task(**{field: text})), field) == text
    for bad in ("tab\there", "nul\x00here", "lone\rreturn"):
        with pytest.raises(ValidationError):
            MyTask.model_validate(_my_task(**{field: bad}))
    for single in ("title_en", "reference_note"):
        with pytest.raises(ValidationError):
            MyTask.model_validate(_my_task(**{single: "two\nlines"}))
