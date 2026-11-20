"""Which areas of the planner prompt a turn needs (phase A of the planner-area-prompts design).

A pure union of signals: the open console tab, the message's words (English and Bahasa Melayu),
the fact reads already decided (``Needs``), the open record's kind, a colleague pick, and the
previous turn (its action and, because the usual follow-up is stored as ``clarification``, its
message). It errs on including more; no signal at all returns ``None`` and the caller keeps the
full prompt. It never imports the turn service (the service imports this module).
"""

from __future__ import annotations

import re

from csa.workspace.planner import PriorTurn
from csa.workspace.prompt_areas import ACTION_AREA, Area
from csa.workspace.routing import Needs
from csa.workspace.words import (
    _MAIL_WORDS,
    _MANAGER_WORDS,
    _PEOPLE_WORDS,
    _TEAM_WORDS,
    _TRAINING_WORDS,
)

# The open console tab. Tabs not listed (overview, services, payments, ...) add nothing.
_TAB_AREAS: dict[str, frozenset[Area]] = {
    "hr": frozenset({"hr"}),
    "email": frozenset({"email"}),
    "inbox": frozenset({"inbox", "email"}),
    "bookings": frozenset({"bookings"}),
    "training_library": frozenset({"training"}),
    "business": frozenset({"products"}),
}

# RecordRef.kind: what the open record belongs to.
_RECORD_AREAS: dict[str, frozenset[Area]] = {
    "conversation": frozenset({"inbox"}),
    "approval": frozenset({"inbox"}),
    "order": frozenset({"products"}),
    "product": frozenset({"products"}),
    "employee": frozenset({"hr"}),
    "training_request": frozenset({"training"}),
    "training_item": frozenset({"training"}),
    "draft": frozenset({"email"}),
}

# The picked-colleague message the console sends after a directory lookup.
_COLLEAGUE_PICK = re.compile(r"^Use employee \S+ for: ")


def _words(pattern: str) -> re.Pattern[str]:
    return re.compile(rf"\b(?:{pattern})\b", re.IGNORECASE)


# Trigger words of each area's action bullets, in English and Malay. The mail, team, training,
# people and manager words of the fact reads are added by ``word_rule_areas`` from
# ``csa.workspace.words``. A word shared by two areas is listed in both: over-inclusion is the rule.
_AREA_WORDS: dict[Area, re.Pattern[str]] = {
    # directory_lookup and inbox_summary are email-only actions: a name lookup selects email so
    # that an hr or bookings turn naming a person can still use directory_lookup.
    "email": _words(
        r"who is|who's|who was|siapa|siapakah|find|look ?up|search|cari(?:kan)?|directory"
        r"|direktori|draft|drafts|drafted|compose|send|sending|sent|cc|bcc|recipients?|memo"
        r"|subscribers?|newsletter|remind|hantar|draf|penerima|ingatkan|langganan|pelanggan"
    ),
    "inbox": _words(
        r"approv(?:e|es|ed|ing|al|als)|reject(?:s|ed|ing|ion)?|held|hold|customers?|pelanggan"
        r"|conversations?|perbualan|lulus(?:kan)?|tolak|ditahan|tahan"
    ),
    "training": _words(
        r"videos?|transcripts?|library|first aid|mohon|belajar|lessons?|pustaka|perpustakaan"
    ),
    "hr": _words(
        r"onboarding|offboarding|tasks?|tugasan|process(?:es)?|proses|overdue|tertunggak|lewat"
        r"|profiles?|profil|leave|leaves|leaving|cuti|berhenti|resign\w*|hire[sd]?|hiring"
        r"|staff|people|kakitangan|pekerja|job titles?|jawatan|start date|tarikh mula|department"
        r"|departments|jabatan|reports? to|melapor kepada|last day|hari terakhir"
    ),
    "bookings": _words(
        r"book|books|booked|booking|bookings|appointments?|meetings?|rooms?|move|moves|moved"
        r"|moving|reschedul\w*|cancel\w*|free|busy|availab\w+|slots?|tempah\w*|menempah"
        r"|batal\w*|kosong|sibuk|pindah\w*|temujanji|bilik|mesyuarat|jadual"
    ),
    # The TikTok guard prose belongs to products only, so every word of a TikTok order, refund,
    # cancellation or stock request selects it.
    "products": _words(
        r"stock|stocks|inventory|stok|inventori|tik ?tok|orders?|refunds?|sku|skus|products?"
        r"|produk|pesanan|shipping|shipment|ship|listings?|variants?|price|harga"
    ),
}

# The fact-read words, mapped like the ``Needs`` map: mail and team to email, training to
# training, people to hr, manager to email and hr.
_FACT_WORD_AREAS: tuple[tuple[re.Pattern[str], frozenset[Area]], ...] = (
    (_MAIL_WORDS, frozenset({"email"})),
    (_TEAM_WORDS, frozenset({"email"})),
    (_TRAINING_WORDS, frozenset({"training"})),
    (_PEOPLE_WORDS, frozenset({"hr"})),
    (_MANAGER_WORDS, frozenset({"email", "hr"})),
)


def word_rule_areas(text: str) -> frozenset[Area]:
    """The areas whose trigger words appear in ``text`` (English or Malay, any case)."""
    found: set[Area] = set()
    for pattern, areas in _FACT_WORD_AREAS:
        if pattern.search(text):
            found |= areas
    for area, pattern in _AREA_WORDS.items():
        if pattern.search(text):
            found.add(area)
    return frozenset(found)


def _needs_areas(needs: Needs) -> set[Area]:
    # The always-present ``bookings`` fact is not a parameter of Needs and selects nothing.
    found: set[Area] = set()
    if needs.mail or needs.team or needs.manager:
        found.add("email")
    if needs.training:
        found.add("training")
    if needs.people or needs.manager:
        found.add("hr")
    return found


def select_areas(
    message: str,
    *,
    tab: str | None,
    record_kind: str | None,
    previous: PriorTurn | None,
    needs: Needs,
) -> frozenset[Area] | None:
    """The union of every signal, or ``None`` when none fires (the caller uses the full prompt)."""
    found: set[Area] = set()
    if tab is not None:
        found |= _TAB_AREAS.get(tab, frozenset())
    if record_kind is not None:
        found |= _RECORD_AREAS.get(record_kind, frozenset())
    found |= word_rule_areas(message)
    found |= _needs_areas(needs)
    if _COLLEAGUE_PICK.match(message):
        found.add("email")
    if previous is not None:
        area = ACTION_AREA.get(previous.action, "general")
        if area != "general":
            found.add(area)
        found |= word_rule_areas(previous.message)
    return frozenset(found) if found else None
