"""The staging roster: strict validation, package ordering, and keeping it out of git."""

from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from scripts.staging_roster import (
    REPO_ROOT,
    ROSTER_PATH,
    Company,
    Roster,
    RosterError,
    load_roster,
    parse_roster,
)

EXAMPLE = REPO_ROOT / "deploy" / "staging-accounts.example.json"


def _example() -> dict[str, Any]:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def test_example_roster_is_valid() -> None:
    roster = load_roster(EXAMPLE)

    assert len(roster.platform_admins) == 3
    assert [c.slug for c in roster.companies] == ["team-jo", "kedai-mawar", "syarikat-cekap"]
    assert [c.packages for c in roster.companies] == [
        (
            "customer_support",
            "knowledge_base",
            "orders_products",
            "subscriptions",
            "bookings",
            "hr",
            "staff_email",
        ),
        ("customer_support", "knowledge_base", "orders_products", "subscriptions"),
        ("hr",),
    ]
    assert [c.deployment_mode for c in roster.companies] == ["auto", "auto", None]
    assert {c.timezone for c in roster.companies} == {"Asia/Kuala_Lumpur"}
    assert sum(len(c.members) for c in roster.companies) == 7
    team_jo = roster.companies[0]
    assert [m.role for m in team_jo.members] == [
        "tenant_admin",
        "tenant_staff",
        "tenant_staff",
        "tenant_staff",
        "read_only",
    ]
    addresses = [a.email for a in roster.platform_admins] + [
        m.email for c in roster.companies for m in c.members
    ]
    assert len(addresses) == 10
    assert all(address.endswith("@example.test") for address in addresses)


def test_roster_path_is_the_local_file() -> None:
    assert ROSTER_PATH == REPO_ROOT / "deploy" / "staging-accounts.local.json"


def test_ordered_packages_puts_customer_support_first() -> None:
    company = Company(
        slug="demo-co",
        name="Demo Co",
        timezone="Asia/Kuala_Lumpur",
        packages=("hr", "subscriptions", "customer_support"),
        members=(),
    )

    ordered = company.ordered_packages()

    assert set(ordered) == {"hr", "subscriptions", "customer_support"}
    assert len(ordered) == 3
    assert ordered.index("customer_support") < ordered.index("subscriptions")


def test_models_are_frozen() -> None:
    roster = load_roster(EXAMPLE)

    with pytest.raises(ValueError):
        roster.companies[0].slug = "other"  # type: ignore[misc]


def _unknown_field(d: dict[str, Any]) -> None:
    d["companies"][0]["colour"] = "red"


def _no_platform_admin(d: dict[str, Any]) -> None:
    d["platform_admins"] = []


def _no_tenant_admin(d: dict[str, Any]) -> None:
    d["companies"][2]["members"][0]["role"] = "tenant_staff"


def _platform_admin_member(d: dict[str, Any]) -> None:
    d["companies"][0]["members"][1]["role"] = "platform_admin"


def _unknown_package(d: dict[str, Any]) -> None:
    d["companies"][2]["packages"] = ["hr", "telepathy"]


def _unsellable_package(d: dict[str, Any]) -> None:
    d["companies"][0]["packages"].append("e_invoicing")


def _missing_requirement(d: dict[str, Any]) -> None:
    d["companies"][2]["packages"] = ["subscriptions"]


def _repeated_package(d: dict[str, Any]) -> None:
    d["companies"][2]["packages"] = ["hr", "hr"]


def _bad_mode(d: dict[str, Any]) -> None:
    d["companies"][0]["deployment_mode"] = "live"


def _bad_slug(d: dict[str, Any]) -> None:
    d["companies"][0]["slug"] = "Team Jo"


def _bad_timezone(d: dict[str, Any]) -> None:
    d["companies"][0]["timezone"] = "Mars/Olympus"


def _empty_name(d: dict[str, Any]) -> None:
    d["companies"][0]["name"] = ""


def _same_slug(d: dict[str, Any]) -> None:
    d["companies"][1]["slug"] = d["companies"][0]["slug"]


def _same_address_other_case(d: dict[str, Any]) -> None:
    d["companies"][0]["members"][0]["email"] = "A@example.test"
    d["companies"][1]["members"][0]["email"] = "a@example.test"


def _same_address_in_one_company(d: dict[str, Any]) -> None:
    members = d["companies"][0]["members"]
    members[1]["email"] = members[0]["email"].upper()


def _admin_and_member(d: dict[str, Any]) -> None:
    d["companies"][0]["members"][0]["email"] = d["platform_admins"][0]["email"].upper()


def _admins_repeat(d: dict[str, Any]) -> None:
    d["platform_admins"][1]["email"] = d["platform_admins"][0]["email"].title()


def _invalid_email(d: dict[str, Any]) -> None:
    d["platform_admins"][0]["email"] = "not-an-address"


def _unknown_top_level(d: dict[str, Any]) -> None:
    d["extra"] = []


@pytest.mark.parametrize(
    "mutate",
    [
        _unknown_field,
        _unknown_top_level,
        _no_platform_admin,
        _no_tenant_admin,
        _platform_admin_member,
        _unknown_package,
        _unsellable_package,
        _missing_requirement,
        _repeated_package,
        _bad_mode,
        _bad_slug,
        _bad_timezone,
        _empty_name,
        _same_slug,
        _same_address_other_case,
        _same_address_in_one_company,
        _admin_and_member,
        _admins_repeat,
        _invalid_email,
    ],
)
def test_refusals(mutate: Any) -> None:
    data = copy.deepcopy(_example())
    mutate(data)

    with pytest.raises(RosterError):
        parse_roster(data)


def test_refuses_what_is_not_a_roster() -> None:
    for data in (None, [], "text", {}):
        with pytest.raises(RosterError):
            parse_roster(data)


def test_addresses_are_normalised() -> None:
    data = _example()
    data["platform_admins"][0]["email"] = "  Mixed.Case@Example.Test "

    roster = parse_roster(data)

    assert roster.platform_admins[0].email == "mixed.case@example.test"
    assert isinstance(roster, Roster)


def test_errors_never_echo_the_roster_address() -> None:
    secret = "private.person@example.test extra@example.test"  # two @ makes it invalid
    data = _example()
    data["companies"][0]["members"][2]["email"] = secret

    with pytest.raises(RosterError) as caught:
        parse_roster(data)

    text = str(caught.value)
    assert "private.person" not in text
    assert "example.test" not in text
    assert "companies[0].members[2]" in text


def test_duplicate_errors_name_positions_not_addresses() -> None:
    data = _example()
    data["companies"][1]["members"][0]["email"] = data["platform_admins"][1]["email"].upper()

    with pytest.raises(RosterError) as caught:
        parse_roster(data)

    text = str(caught.value)
    assert "example.test" not in text
    assert "companies[1].members[0]" in text
    assert "platform_admins[1]" in text


def test_load_roster_reports_unreadable_files_without_content(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(RosterError, match="not found"):
        load_roster(missing)

    broken = tmp_path / "broken.json"
    broken.write_text('{"platform_admins": ["private@example.test"', encoding="utf-8")
    with pytest.raises(RosterError) as caught:
        load_roster(broken)
    assert "private@example.test" not in str(caught.value)


def test_local_roster_is_ignored_by_git() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "deploy/staging-accounts.local.json"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0


def test_example_roster_is_not_ignored_by_git() -> None:
    result = subprocess.run(
        ["git", "check-ignore", "deploy/staging-accounts.example.json"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
