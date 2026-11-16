"""Every starting idea the console offers is an assistant smoke prompt for the people who see it.

The idea list (CSA_WebConsole/csa-console/web/lib/assistant-ideas.json) is shown as chips on
Overview and, later, in a dock bubble. An idea that is not a smoke case could fail when sent, so
each idea's English and Malay text must equal the ``message`` of a case in the same language that
runs as a role the idea is offered to, with every package the idea needs active in its preset.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from csa.workspace.rules import ADDRESS
from scripts import assistant_smoke as smoke

REPO_ROOT = Path(__file__).resolve().parent.parent
IDEAS_FILE = REPO_ROOT / "CSA_WebConsole" / "csa-console" / "web" / "lib" / "assistant-ideas.json"
TABS = {
    "any",
    "inbox",
    "hr",
    "bookings",
    "email",
    "subscriptions",
    "training_library",
}
ROLES = {"tenant_admin", "tenant_staff"}
# A chip is sent in the user's own company, so it must not name a person or a room that company
# will not have. The synthetic people of the smoke fixtures come from the fixtures themselves;
# the first names that appear only inside case messages are listed here because they cannot be
# derived cleanly from free text.
MESSAGE_ONLY_NAMES = ("Aina", "Danial", "Siti", "Razak", "Hakim", "Rahman", "Mei", "Ling", "Tan")
ROOM = re.compile(r"\b(?:room|bilik)\s+[A-Z]\b", re.IGNORECASE)


def _person_names() -> set[str]:
    fixtures = [
        smoke.MANAGER_NAME,
        *(member.name for member in smoke.REPORTS),
        *(member.name for member in smoke.SKIPPED),
        *(thread.sender_name for thread in smoke.THREADS),
    ]
    words = {word for name in fixtures for word in name.split()}
    return {word.lower() for word in (*words, *MESSAGE_ONLY_NAMES)}


def _ideas() -> list[dict[str, Any]]:
    ideas = json.loads(IDEAS_FILE.read_text(encoding="utf-8"))
    assert isinstance(ideas, list)
    return ideas


def _active_packages(case: smoke.SmokeCase) -> set[str]:
    preset = smoke.PRESETS[case.facts.preset]
    packages = {key for key in smoke._PACKAGES if key != "hr" or preset.hr_status == "active"}
    return packages | set(preset.extra_packages)


def _covered(idea: dict[str, Any], language: str) -> bool:
    for case in smoke.load_cases():
        role = str(case.facts.role or smoke.Role.TENANT_STAFF)
        if (
            case.message == idea[language]
            and case.language == language
            and role in idea["roles"]
            and set(idea["packages"]) <= _active_packages(case)
        ):
            return True
    return False


def test_every_idea_is_a_smoke_case_for_the_people_who_see_it() -> None:
    ideas = _ideas()
    assert 8 <= len(ideas) <= 12
    for idea in ideas:
        for language in ("en", "ms"):
            assert _covered(idea, language), f"idea {idea['id']} has no {language} smoke case"


def test_no_idea_names_a_person_or_room_a_real_company_will_not_have() -> None:
    names = _person_names()
    assert {"aina", "danial", "siti", "razak", "hafiz", "kumar"} <= names
    for idea in _ideas():
        for language in ("en", "ms"):
            text = idea[language]
            found = names & {word.lower() for word in re.findall(r"[^\W\d_]+", text)}
            assert not found, (idea["id"], language, sorted(found))
            assert not ROOM.search(text), (idea["id"], language)


def test_ideas_are_well_formed_and_spread_for_the_overview() -> None:
    ideas = _ideas()
    ids = [idea["id"] for idea in ideas]
    assert len(ids) == len(set(ids))
    for idea in ideas:
        assert set(idea) == {"id", "tab", "roles", "packages", "en", "ms"}, idea["id"]
        assert idea["tab"] in TABS, idea["id"]
        assert idea["roles"] and set(idea["roles"]) <= ROLES, idea["id"]
        assert set(idea["packages"]) <= {*smoke._PACKAGES, "subscriptions", "training_library"}
        for language in ("en", "ms"):
            text = idea[language]
            assert text.strip() and len(text) < 200, (idea["id"], language)
            assert not text.strip().isdigit(), (idea["id"], language)
            assert not ADDRESS.search(text), (idea["id"], language)
            assert not re.search(r"https?://|www\.", text), (idea["id"], language)
    # Overview draws only the "any" ideas: a staff member with the base packages gets several,
    # and a company with none of them still gets some.
    anywhere = [idea for idea in ideas if idea["tab"] == "any"]
    assert len(anywhere) >= 5
    base = set(smoke._PACKAGES)
    assert (
        len([i for i in anywhere if "tenant_staff" in i["roles"] and set(i["packages"]) <= base])
        >= 4
    )
    assert [i["id"] for i in anywhere if not i["packages"]]
