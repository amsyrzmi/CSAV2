"""Pure people rules and contracts: status, search, filters, paging, org chart, history."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from csa.hr.automation_models import PersonalSave, PersonalValues
from csa.hr.people_contracts import HrPersonOut
from csa.hr.people_rules import (
    history_fields,
    kl_today,
    org_chart,
    page,
    person_from_row,
    person_status,
    search,
    sections_for,
    select,
)
from csa.hr.personal import mask
from csa.hr.policy import HrPolicyError
from pydantic import ValidationError

D = date(2026, 11, 1)


def person(**kw: Any) -> HrPersonOut:
    base: dict[str, Any] = {"employee_id": uuid4(), "display_name": "Person", "status": "active"}
    return HrPersonOut(**{**base, **kw})


def test_status_active_starting_left():
    assert person_status(True, None, D) == "active"
    assert person_status(True, D, D) == "active"
    assert person_status(True, D + timedelta(days=1), D) == "starting"
    assert person_status(False, D - timedelta(days=9), D) == "left"


def test_kl_today_uses_malaysia_time():
    assert kl_today(datetime(2026, 11, 1, 16, 30, tzinfo=UTC)) == date(2026, 11, 2)


def test_search_every_word_any_order_title_word_starts():
    aina = person(display_name="Nur Aina binti Rahman")
    fin = person(display_name="Lee", job_title="Finance Manager")
    asst = person(display_name="Wong", job_title="Assistant")
    assert search([aina, fin, asst], "Rahman Aina") == [aina]
    assert search([aina, fin, asst], "finance manager") == [fin]
    assert search([aina, fin, asst], "tan") == []  # not inside "Assistant"


def test_search_puts_name_or_email_matches_first():
    by_title = person(display_name="Zed", job_title="Sales Lead")
    by_name = person(display_name="Sales Ahmad")
    assert search([by_title, by_name], "sales") == [by_name, by_title]


def test_search_treats_metacharacters_literally():
    promo = person(display_name="Promo 50% Team", job_title="(acting) a+b")
    for q in ("50%", "a+b", "(acting)", "_", "\\"):
        search([promo], q)  # never raises
    assert search([promo], "50%") == [promo]
    assert search([promo], "(acting)") == []  # "(" never starts a word (Postgres \m)
    assert search([promo], "a+b") == [promo]


def test_search_uses_only_the_first_five_words():
    p = person(display_name="a b c d e")
    assert search([p], "a b c d e zzz") == [p]


def test_search_matches_work_email_and_blank_query_matches_nothing():
    mail = person(display_name="Lee", work_email="lee.tan@example.test")
    assert search([mail], "TAN@example") == [mail]
    assert search([mail], "  , ") == []


def test_select_department_manager_status():
    dept, other, boss = uuid4(), uuid4(), uuid4()
    a = person(display_name="A", department_id=dept, manager_id=boss)
    b = person(display_name="B", department_id=other)
    c = person(display_name="C")
    starting = person(display_name="D", status="starting", department_id=dept)
    left = person(display_name="E", status="left", department_id=dept, manager_id=boss)
    everyone = [a, b, c, starting, left]

    def pick(**kw: Any) -> list[HrPersonOut]:
        return select(everyone, **{"department": None, "manager": None, "status": None, **kw})

    # status None keeps active and starting only
    assert pick() == [a, b, c, starting]
    assert pick(status="left") == [left]
    assert pick(status="starting") == [starting]
    assert pick(status="active") == [a, b, c]
    # department: a given id, or "none" for people without a department
    assert pick(department=dept) == [a, starting]
    assert pick(department="none") == [c]
    assert pick(department=dept, status="left") == [left]
    # manager
    assert pick(manager=boss) == [a]
    assert pick(manager=boss, status="left") == [left]
    assert pick(manager=uuid4()) == []
    assert pick(department=dept, manager=boss) == [a]


def test_page_slices_fifty_with_offset_cursor_and_rejects_bad_cursor():
    people = [person(display_name=f"P{i:03}") for i in range(120)]
    first = page(people, None)
    assert len(first.items) == 50 and first.next_cursor == "50" and first.total == 120
    assert page(people, "100").next_cursor is None
    with pytest.raises(HrPolicyError, match="invalid_cursor"):
        page(people, "-1")


def test_page_cursor_forms_and_last_page():
    people = [person(display_name=f"P{i:03}") for i in range(120)]
    middle = page(people, "50")
    assert [p.display_name for p in middle.items][:1] == ["P050"] and middle.next_cursor == "100"
    assert len(page(people, "100").items) == 20
    assert page(people, "9999999").items == [] and page(people, "9999999").total == 120
    assert page([], None).items == [] and page([], None).next_cursor is None
    for bad in ("", "abc", "1.5", "12345678", " 5", "5\n", "٣"):
        with pytest.raises(HrPolicyError, match="invalid_cursor"):
            page(people, bad)


def test_org_chart_roots_children_hidden_managers_and_left():
    # manager not in the set (hidden or left) -> root (manager_id None); left people are not nodes;
    # nodes ordered by display_name casefold then employee_id
    hidden_boss, left_id = uuid4(), uuid4()
    ceo = person(display_name="zara", job_title="CEO", department="Exec")
    deputy = person(display_name="Bakar", manager_id=ceo.employee_id)
    orphan = person(display_name="amir", manager_id=hidden_boss)
    gone = person(employee_id=left_id, display_name="Gone", status="left")
    report_of_left = person(display_name="Cik", manager_id=left_id)
    chart = org_chart([ceo, deputy, orphan, gone, report_of_left])

    assert [n.display_name for n in chart.nodes] == ["amir", "Bakar", "Cik", "zara"]
    parents = {n.display_name: n.manager_id for n in chart.nodes}
    assert parents == {"amir": None, "Bakar": ceo.employee_id, "Cik": None, "zara": None}
    assert left_id not in {n.employee_id for n in chart.nodes}
    assert chart.looped == []
    root = next(n for n in chart.nodes if n.display_name == "zara")
    assert (root.job_title, root.department) == ("CEO", "Exec")


def test_org_chart_ties_order_by_employee_id():
    low, high = UUID(int=1), UUID(int=2)
    chart = org_chart(
        [person(employee_id=high, display_name="Sam"), person(employee_id=low, display_name="sam")]
    )
    assert [n.employee_id for n in chart.nodes] == [low, high]


def test_org_chart_loop_is_flat():
    a, b = uuid4(), uuid4()
    c = person(display_name="C", manager_id=a)
    chart = org_chart([person(employee_id=a, manager_id=b), person(employee_id=b, manager_id=a), c])
    assert set(chart.looped) == {a, b}
    assert {n.employee_id: n.manager_id for n in chart.nodes}[a] is None
    assert {n.employee_id: n.manager_id for n in chart.nodes}[c.employee_id] == a


def test_org_chart_self_manager_is_a_loop():
    a = uuid4()
    chart = org_chart([person(employee_id=a, manager_id=a)])
    assert chart.looped == [a] and chart.nodes[0].manager_id is None


def test_history_fields_fixed_order_drops_unknown():
    out = history_fields(
        {"manager": {"old": None, "new": "Siti"}, "job_title": {"old": "A", "new": "B"}, "x": {}}
    )
    assert [f.field for f in out] == ["job_title", "manager"]
    assert (out[1].old, out[1].new) == (None, "Siti")
    assert (out[0].old, out[0].new) == ("A", "B")


def test_sections_for_viewer():
    assert sections_for("hr", personal=False, documents=False) == ["overview", "access", "history"]
    assert sections_for("directory", personal=False, documents=False) == ["overview"]
    assert sections_for("self", personal=False, documents=False) == ["overview"]


def test_sections_for_orders_personal_and_documents():
    assert sections_for("hr", personal=True, documents=True) == [
        "overview",
        "personal",
        "documents",
        "access",
        "history",
    ]
    # A Manage people holder without Sensitive details sees documents but not personal details.
    assert sections_for("hr", personal=False, documents=True) == [
        "overview",
        "documents",
        "access",
        "history",
    ]
    assert sections_for("self", personal=True, documents=True) == [
        "overview",
        "personal",
        "documents",
    ]
    assert sections_for("directory", personal=False, documents=False) == ["overview"]


def test_history_fields_keep_personal_names_without_values():
    out = history_fields(
        {
            "tax_number": {"old": None, "new": None},
            "home_address": {"old": None, "new": None},
            "ic_or_passport": {},
            "job_title": {"old": "A", "new": "B"},
        }
    )
    assert [f.field for f in out] == ["job_title", "home_address", "ic_or_passport", "tax_number"]
    assert all(f.old is None and f.new is None for f in out[1:])


def test_mask_shows_last_four_only():
    assert mask("900101145678") == "•••• 5678"
    assert mask("12345") == "•••• 2345"
    assert mask("1234") == "••••"
    assert mask("") == "••••"
    assert mask(None) is None


def numbers(**kw: str | None) -> dict[str, Any]:
    return {
        "operation": "personal.save",
        "employee_id": str(uuid4()),
        "expected_version": None,
        "values": {},
        "encrypted": kw,
    }


@pytest.mark.parametrize(
    "field, value",
    [
        ("ic_or_passport", "AB1"),  # too short
        ("ic_or_passport", "A" * 21),
        ("ic_or_passport", "BAD!!VALUE"),
        ("ic_or_passport", "900101 14 5678"),  # spaces are not allowed
        ("bank_account", "1234"),
        ("bank_account", "1" * 35),
        ("bank_account", "ABCDE12345"),
        ("tax_number", "SG1"),
        ("tax_number", "SG-1234567"),
        ("tax_number", "x" * 21),
        ("epf_number", "1234"),
        ("epf_number", "12345A"),
        ("epf_number", "1" * 21),
        ("socso_number", "A-1"),
        ("socso_number", "AB 12345"),
        ("socso_number", "1" * 21),
        ("ic_or_passport", ""),
    ],
)
def test_personal_values_reject_bad_numbers(field: str, value: str):
    with pytest.raises(ValidationError) as caught:
        PersonalSave(**numbers(**{field: value}))
    # The refusal never repeats the submitted value.
    if value:
        assert value not in str(caught.value)
        assert value not in repr(caught.value.errors(include_input=False))


def test_personal_numbers_are_normalised_and_hidden():
    body = PersonalSave(
        **numbers(
            ic_or_passport=" ab-12345 ",
            bank_account="1234 5678-90",
            tax_number="SG 12345",
            epf_number="12345678",
            socso_number="A-12345",
        )
    )
    shown = {k: v.get_secret_value() for k, v in body.encrypted.items() if v is not None}
    assert shown["ic_or_passport"] == "AB-12345"
    assert shown["bank_account"] == "1234 5678-90"
    assert "AB-12345" not in repr(body) and "AB-12345" not in str(body.model_dump())
    # null clears a number and is allowed for every field.
    cleared = PersonalSave(**numbers(tax_number=None, epf_number=None))
    assert cleared.encrypted == {"tax_number": None, "epf_number": None}
    # Another key is not a number.
    with pytest.raises(ValidationError):
        PersonalSave(**numbers(home_address="1 Road"))


@pytest.mark.parametrize(
    "values",
    [
        {"home_address": "x" * 501},
        {"personal_phone": "call me"},
        {"personal_phone": "12"},
        {"emergency_name": "x" * 121},
        {"emergency_relationship": "x" * 61},
        {"emergency_phone": "+60 12 abc"},
        {"bank_name": "x" * 101},
        {"home_address": ""},
        {"date_of_birth": "2999-01-01"},
        {"nickname": "x"},
    ],
)
def test_personal_values_reject_bad_plain_fields(values: dict[str, Any]):
    with pytest.raises(ValidationError):
        PersonalValues(**values)


def test_personal_values_accept_the_limits_and_track_which_keys_were_sent():
    values = PersonalValues(
        home_address="x" * 500,
        personal_phone="+60 (12) 345-6789",
        emergency_name="x" * 120,
        emergency_relationship="x" * 60,
        emergency_phone="999",
        bank_name="x" * 100,
        date_of_birth=date(1990, 2, 3),
    )
    assert values.date_of_birth == date(1990, 2, 3)
    only = PersonalValues(home_address=None)
    assert only.model_fields_set == {"home_address"} and only.home_address is None
    assert PersonalValues().model_fields_set == set()
    # Born today is allowed; tomorrow is not.
    today = datetime.now(UTC).astimezone(ZoneInfo("Asia/Kuala_Lumpur")).date()
    assert PersonalValues(date_of_birth=today).date_of_birth == today
    with pytest.raises(ValidationError):
        PersonalValues(date_of_birth=today + timedelta(days=1))


def _row(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "employee_id": uuid4(),
        "display_name": "Aina",
        "job_title": "Analyst",
        "department_id": None,
        "department": None,
        "manager_id": None,
        "work_email": None,
        "work_phone": None,
        "start_date": None,
        "employment_type": None,
        "active": True,
    }
    return {**base, **kw}


def test_person_from_row_directory_view_has_no_hr_only_fields():
    boss = uuid4()
    row = _row(manager_id=boss, start_date=D + timedelta(days=2))
    out = person_from_row(row, D, {boss: "Siti"})
    assert (out.manager_name, out.status) == ("Siti", "starting")
    assert out.version is None and out.account_linked is None and out.employment_profile is None
    assert person_from_row(_row(manager_id=boss), D, {}).manager_name is None


def test_person_from_row_hr_view_profile_and_account_flag():
    full = _row(
        version=3,
        account_user_id=uuid4(),
        region="sabah",
        legal_entity_reference="LE-1",
        employment_category="staff",
        active=False,
    )
    out = person_from_row(full, D, {})
    assert out.version == 3 and out.account_linked is True and out.status == "left"
    assert out.employment_profile is not None and out.employment_profile.region == "sabah"
    unlinked = person_from_row(_row(version=0, account_user_id=None, region="sabah"), D, {})
    assert (
        unlinked.account_linked is False and unlinked.employment_profile is None
    )  # needs all three


def test_contracts_forbid_extra_fields():
    with pytest.raises(ValidationError):
        HrPersonOut.model_validate({**person().model_dump(), "ic_number": "x"})
