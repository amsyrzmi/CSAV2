"""The Malaysian default templates (HR PR 4, spec sections 6 and 7): data that validates, carries a
traceable source per step, never a figure, and a review document that cannot drift from it.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from csa.hr.default_templates import (
    DEFAULT_TEMPLATE_SOURCES,
    DEFAULT_TEMPLATES,
    UNCONFIRMED,
    SourceNote,
)
from csa.hr.process_models import TemplateCreate
from scripts.render_default_templates_review import OUTPUT, render

ROOT = Path(__file__).resolve().parents[1]
SOURCES_DOC = ROOT / "docs" / "hr" / "default-templates-sources.md"

# The official domains the default steps cite (each named in the sources doc, "Domains cited").
# A URL on any other host, a secondary source included, fails.
OFFICIAL_DOMAINS = (
    "kwsp.gov.my",
    "perkeso.gov.my",
    "hasil.gov.my",
    "jtksm.mohr.gov.my",
    "jtksm.gov.my",
    "pdp.gov.my",
)
CONFIDENCES = {"official_read", "official_excerpt", "secondary", "unclear", "practice"}
PRACTICE_BODY = "Company practice, no statutory source"
# Words that mean a note names a statutory body or instrument and so needs an official URL.
STATUTORY = re.compile(
    r"\b(EPF|KWSP|PERKESO|SOCSO|EIS|LHDN|HASiL|Employment Act|Regulations|PDP|JTKSM|Act 800)\b"
)
# Money figures: a percent sign, a currency symbol, RM followed by a digit, a digit next to a
# currency word. Statutory periods (days, months, years) are allowed.
MONEY = re.compile(
    r"%|\$|£|€|\bRM\s?\d|\d\s?(?:RM|MYR|ringgit|sen|USD)\b|\b(?:MYR|ringgit|USD)\s?\d",
    re.IGNORECASE,
)
URL = re.compile(r"https?://\S+")
# A note that states a due period: the wording of a statutory timing.
PERIOD = re.compile(
    r"\b(?:within|not later than|no later than|not less than|not more than|on or before)\b"
    r"|\b\d+(?:st|nd|rd|th)? day|\bdays\b",
    re.IGNORECASE,
)
ALLOWED_CHECKS = {
    "account_linked",
    "personal_details_complete",
    "access_removed",
    "document_uploaded:contract",
    "document_uploaded:identity",
    "document_uploaded:certificate",
    "document_uploaded:other",
}


def _template(kind: str) -> TemplateCreate:
    (found,) = [t for t in DEFAULT_TEMPLATES if t.kind == kind]
    return found


def _steps():
    for template in DEFAULT_TEMPLATES:
        for ordinal, step in enumerate(template.steps, start=1):
            yield template, ordinal, step


def _texts():
    for template, ordinal, step in _steps():
        for text in (step.title_en, step.description_en, step.reference_note):
            if text is not None:
                yield f"{template.kind}:{ordinal}", text
    for key, note in DEFAULT_TEMPLATE_SOURCES.items():
        yield key, note.body


def test_one_onboarding_and_one_offboarding_company_default_that_validate() -> None:
    assert sorted(t.kind for t in DEFAULT_TEMPLATES) == ["offboarding", "onboarding"]
    for template in DEFAULT_TEMPLATES:
        assert isinstance(template, TemplateCreate)
        # What the create route will send validates again unchanged (no defaults lost).
        assert TemplateCreate.model_validate(template.model_dump()) == template
        assert template.rules is None, "a default applies to everyone (no rule)"
        assert "Malaysia default" in template.name


def test_every_step_has_a_source_note_keyed_by_the_engine_ordinal() -> None:
    # hr_template_write_steps (0631) numbers steps from 1 in array order (WITH ORDINALITY).
    expected = {f"{t.kind}:{n}" for t, n, _ in _steps()}
    assert set(DEFAULT_TEMPLATE_SOURCES) == expected
    assert all(isinstance(n, SourceNote) for n in DEFAULT_TEMPLATE_SOURCES.values())


def test_source_notes_are_dated_labelled_and_point_at_official_pages_in_the_research() -> None:
    research = SOURCES_DOC.read_text(encoding="utf-8")
    for key, note in DEFAULT_TEMPLATE_SOURCES.items():
        assert note.checked_on <= date.today(), key
        assert note.confidence in CONFIDENCES, key
        assert note.body.strip(), key
        if note.confidence == "practice":
            assert note.body.startswith(PRACTICE_BODY), key
        else:
            assert note.url is not None, f"{key}: a statutory source needs a URL"
        if note.url is not None:
            parts = urlsplit(note.url)
            assert parts.scheme == "https", key
            host = parts.hostname or ""
            assert any(host == d or host.endswith("." + d) for d in OFFICIAL_DOMAINS), (key, host)
            assert note.url in research, f"{key}: URL not backed by the research file"


def test_no_step_rests_on_a_secondary_source_and_unconfirmed_timings_say_so() -> None:
    for template, ordinal, step in _steps():
        note = DEFAULT_TEMPLATE_SOURCES[f"{template.kind}:{ordinal}"]
        assert note.confidence != "secondary", f"{template.kind}:{ordinal}"
        if note.confidence in {"official_excerpt", "unclear"}:
            assert step.reference_note is not None
            assert UNCONFIRMED in step.reference_note, f"{template.kind}:{ordinal}"
    assert UNCONFIRMED == "Timing not confirmed on an official page: reviewer to confirm."


def test_a_weak_source_says_unconfirmed_and_never_states_a_period() -> None:
    # An "unclear" or "official_excerpt" source cannot carry a timing: its note says the exact
    # unconfirmed sentence, and an unclear source never states a due period at all.
    for template, ordinal, step in _steps():
        key = f"{template.kind}:{ordinal}"
        note = DEFAULT_TEMPLATE_SOURCES[key]
        reference = step.reference_note or ""
        if note.confidence in {"unclear", "official_excerpt"}:
            assert UNCONFIRMED in reference, key
        if PERIOD.search(reference):
            assert note.confidence != "unclear", key


def _research_confidence() -> dict[str, str]:
    """Each research row id (a1, b3, i3, ...) and the leading label of its confidence column."""
    rows: dict[str, str] = {}
    for line in SOURCES_DOC.read_text(encoding="utf-8").splitlines():
        match = re.match(r"\| ([a-z]\d{0,2})\. ", line)
        if match:
            label = line.rstrip(" |").rsplit("|", 1)[1].strip()
            if label.startswith("Official (read"):
                rows[match.group(1)] = "official_read"
            elif label.startswith("Official (excerpt"):
                rows[match.group(1)] = "official_excerpt"
            else:
                rows[match.group(1)] = "unclear"
    return rows


def test_a_source_note_is_never_stronger_than_the_research_rows_it_cites() -> None:
    # A note citing an "Unclear" or excerpt-only research row cannot be labelled official_read.
    strength = {"official_read": 2, "official_excerpt": 1, "unclear": 0, "secondary": 0}
    rows = _research_confidence()
    assert {"a2", "i1", "i2", "i3"} <= set(rows), "research table not parsed"
    for key, note in DEFAULT_TEMPLATE_SOURCES.items():
        if note.confidence == "practice":
            continue
        cited = re.findall(r"research rows? ([^)]*)", note.body)
        ids = {i for part in cited for i in re.findall(r"\b([a-z]\d{0,2})\b", part) if i in rows}
        assert ids, f"{key}: cites no research row"
        weakest = min(strength[rows[i]] for i in ids)
        assert strength[note.confidence] <= weakest, (key, sorted(ids))


@pytest.mark.parametrize("text", ["within 30 days", "no later than the 15th day", "7 days"])
def test_the_period_pattern_catches_timings(text: str) -> None:
    assert PERIOD.search(text)


def test_final_contributions_split_by_scheme_and_eis_is_unconfirmed() -> None:
    titles = [s.title_en for s in _template("offboarding").steps]
    socso = titles.index("SOCSO: final month's contribution paid") + 1
    eis = titles.index("EIS: final month's contribution paid") + 1
    assert DEFAULT_TEMPLATE_SOURCES[f"offboarding:{socso}"].confidence == "official_read"
    assert DEFAULT_TEMPLATE_SOURCES[f"offboarding:{eis}"].confidence == "unclear"
    assert len(titles) == 15


def test_the_leavers_records_step_is_required() -> None:
    # Employment Act s.61 register (date of leaving, benefits paid): optional steps are
    # skipped automatically when the process completes, so this one must be required.
    (step,) = [s for s in _template("offboarding").steps if "records" in s.title_en.lower()]
    assert step.required is True


def test_every_step_has_a_reference_note_with_its_checked_date() -> None:
    for template, ordinal, step in _steps():
        note = DEFAULT_TEMPLATE_SOURCES[f"{template.kind}:{ordinal}"]
        assert step.reference_note, f"{template.kind}:{ordinal}"
        assert note.checked_on.isoformat() in step.reference_note, f"{template.kind}:{ordinal}"


def test_a_note_that_names_a_statutory_body_has_an_official_url() -> None:
    for template, ordinal, step in _steps():
        if STATUTORY.search(step.reference_note or ""):
            note = DEFAULT_TEMPLATE_SOURCES[f"{template.kind}:{ordinal}"]
            assert note.url is not None, f"{template.kind}:{ordinal}"


def test_no_rate_ceiling_or_amount_anywhere_in_the_content() -> None:
    for key, text in _texts():
        assert not MONEY.search(text), (key, text)
        assert not URL.search(text), f"{key}: URLs live in the SourceNote, not the text"


def test_review_document_carries_no_amount_outside_its_urls() -> None:
    assert not MONEY.search(URL.sub("", render()))


@pytest.mark.parametrize("pattern", ["50%", "RM 5", "RM5", "5 ringgit", "MYR10", "10 sen"])
def test_the_money_pattern_catches_figures(pattern: str) -> None:
    assert MONEY.search(pattern)


@pytest.mark.parametrize("text", ["within 30 days", "not less than six years", "Form CP22A"])
def test_the_money_pattern_allows_periods_and_form_names(text: str) -> None:
    assert not MONEY.search(text)


def test_english_only() -> None:
    for _, _, step in _steps():
        assert step.title_ms is None
        assert step.description_ms is None


def test_owners_anchors_and_checks_are_in_the_allowed_sets() -> None:
    for template, ordinal, step in _steps():
        key = f"{template.kind}:{ordinal}"
        assert step.owner_role in {"hr", "manager", "employee"}, key
        assert step.owner_role != "person", key
        assert step.owner_employee_id is None, key
        if template.kind == "onboarding":
            assert step.anchor in {"start_date", "probation_end"}, key
        else:
            assert step.anchor == "last_day", key
        assert step.auto_check is None or step.auto_check in ALLOWED_CHECKS, key
        if step.auto_check == "access_removed":
            assert (template.kind, step.owner_role) == ("offboarding", "hr"), key


def test_the_leavers_own_steps_are_due_on_or_before_the_last_day() -> None:
    # 0631 hr_template_warnings warns step_after_last_day only for these; HR and manager steps
    # may fall after the last day where an official period runs after it.
    for step in _template("offboarding").steps:
        if step.owner_role == "employee":
            assert step.due_offset_days <= 0, step.title_en


def test_at_most_one_approval_per_template() -> None:
    for template in DEFAULT_TEMPLATES:
        assert sum(s.approval_by is not None for s in template.steps) <= 1, template.kind


def test_probation_review_is_anchored_on_probation_end_and_approved_by_hr() -> None:
    (step,) = [s for s in _template("onboarding").steps if s.anchor == "probation_end"]
    assert "probation" in step.title_en.lower()
    assert step.owner_role == "manager"
    assert step.approval_by == "hr"
    assert "No statutory probation rule found" in (step.reference_note or "")


def test_bank_and_statutory_numbers_step_has_no_auto_check() -> None:
    # personal_details_complete (0634) covers five contact fields only, never bank or IC numbers.
    (step,) = [s for s in _template("onboarding").steps if "bank" in s.title_en.lower()]
    assert step.auto_check is None
    (details,) = [s for s in _template("onboarding").steps if s.auto_check]
    assert details.auto_check == "personal_details_complete"
    assert details.owner_role == "employee"


def test_access_removed_is_the_system_step_of_the_offboarding() -> None:
    checks = [s for s in _template("offboarding").steps if s.auto_check == "access_removed"]
    assert len(checks) == 1
    assert checks[0].required is True


def test_no_step_for_the_vacancy_notification_that_is_not_in_force() -> None:
    for _, _, step in _steps():
        assert "vacanc" not in step.title_en.lower()
        assert "vacanc" not in (step.description_en or "").lower()


def test_committed_review_document_matches_the_rendered_one() -> None:
    assert OUTPUT.read_text(encoding="utf-8") == render(), (
        "Run python -m scripts.render_default_templates_review and commit the result"
    )


def test_review_document_says_what_it_is_and_ends_with_the_sign_off() -> None:
    text = render()
    head = " ".join(text[:1500].split())
    assert "not legal advice" in head
    assert "not a filing" in head
    for heading in (
        "## Left out on purpose",
        "## What could not be confirmed",
        "## Open questions for the reviewer",
        "## Sign-off",
    ):
        assert heading in text
    sign_off = " ".join(text[text.index("## Sign-off") :].split())
    assert "Reviewer name and role: ____" in sign_off
    assert "Date: ____" in sign_off
    assert "Decision: approved / changes needed ____" in sign_off
    assert "reviewed pull request" in sign_off
    for template, ordinal, _ in _steps():
        note = DEFAULT_TEMPLATE_SOURCES[f"{template.kind}:{ordinal}"]
        if note.url:
            assert note.url in text
